"""Loopback-only local API backed by the durable review repository."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from app.api.schemas import (
    CaseCreated,
    DeleteCaseRequest,
    ExportFormat,
    FindingResponse,
    FindingReviewUpdate,
    LLMConfigResponse,
    LLMConfigUpdate,
    LLMHealthResponse,
    ReviewSummary,
    RulesetReloadRequest,
    RulesetStatus,
)
from app.domain.exceptions import ParseError, PathTraversalError, ReviewError, UnsupportedFileTypeError
from app.llm.config_store import LLMConfigStore
from app.llm.factory import build_provider
from app.llm.mock import MockProvider
from app.llm.provider import LLMProviderError, LLMRequest
from app.parsers.docx_parser import DocxParser
from app.persistence.db import create_session
from app.persistence.models import CaseRecord
from app.persistence.repository import ReviewRepository
from app.reports.exporters import export_anonymous_package, export_excel, export_word
from app.review.pipeline import ReviewPipeline
from app.rules.ruleset import LoadedRuleset, RulesetError, load_active_ruleset
from app.security.credentials import CredentialStore
from app.settings import get_settings
from app.storage.case_files import StoredFile, UploadTooLargeError, store_upload_streaming
from app.storage.paths import safe_join, validate_upload_name

router = APIRouter(prefix="/api", tags=["local review"])

# In-process cache of the active ruleset. ``_loaded`` distinguishes "never
# attempted" from "attempted and found nothing", so the lazy load runs once.
_RULESET_CACHE: LoadedRuleset | None = None
_RULESET_ATTEMPTED = False


def _reset_ruleset_cache() -> None:
    """Clear the cached ruleset (used by reload and tests)."""
    global _RULESET_CACHE, _RULESET_ATTEMPTED
    _RULESET_CACHE = None
    _RULESET_ATTEMPTED = False


def _load_ruleset_into_cache(root: Path | None = None) -> LoadedRuleset | None:
    """Attempt to load the ruleset and record the result in the cache."""
    global _RULESET_CACHE, _RULESET_ATTEMPTED
    try:
        _RULESET_CACHE = load_active_ruleset(root) if root is not None else load_active_ruleset()
    except RulesetError:
        _RULESET_CACHE = None
    _RULESET_ATTEMPTED = True
    return _RULESET_CACHE


def _active_ruleset() -> LoadedRuleset | None:
    """Return the cached active rule set, loading it once on first use.

    The review path degrades to an LLM-only pass rather than failing when no
    ruleset is available; the response tells the client this happened.
    """
    if not _RULESET_ATTEMPTED:
        return _load_ruleset_into_cache()
    return _RULESET_CACHE


def _ruleset_status() -> RulesetStatus:
    loaded = _active_ruleset()
    return RulesetStatus(
        loaded=loaded is not None,
        rule_count=len(loaded.rules) if loaded else 0,
        root=str(loaded.root) if loaded else None,
    )


# LLM configuration store (non-key config on disk; key in the credential store).
_LLM_CONFIG_STORE: LLMConfigStore | None = None


def _default_credentials() -> CredentialStore:
    return CredentialStore()


def _reset_llm_config_store(credentials=None) -> None:
    """(Re)build the config store, injecting credentials in tests."""
    global _LLM_CONFIG_STORE
    creds = credentials if credentials is not None else _default_credentials()
    _LLM_CONFIG_STORE = LLMConfigStore(get_settings().storage_root / "llm_config.json", creds)


def _llm_config_store() -> LLMConfigStore:
    if _LLM_CONFIG_STORE is None:
        _reset_llm_config_store()
    assert _LLM_CONFIG_STORE is not None
    return _LLM_CONFIG_STORE


def _build_active_provider():
    store = _llm_config_store()
    config = store.load()
    try:
        api_key = store.get_key()
    except Exception:
        # Credential backend unavailable (e.g. non-Windows / no keyring): fall
        # back to Mock rather than failing the whole review.
        api_key = None
    return build_provider(config, api_key)


def _case_id(value: str) -> str:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "case_id 必须是 UUID4") from exc
    if parsed.version != 4 or str(parsed) != value.lower():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "case_id 必须是 UUID4")
    return str(parsed)


def _repository() -> ReviewRepository:
    return ReviewRepository(create_session(get_settings().db_path))


def _uploaded_file(case_id: str, file: StoredFile) -> Path:
    try:
        return safe_join(get_settings().storage_root, *file.storage_relative_path.split("/"))
    except PathTraversalError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "stored file path is invalid") from exc


def _active_run(case_id: str):
    run = _repository().get_run(case_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "案例或审查结果不存在")
    return run


def _finding_response(item) -> FindingResponse:
    return FindingResponse(
        finding_id=item.finding_id,
        origin=item.origin.value,
        category=item.category,
        severity=item.severity.value,
        parameter=item.parameter,
        title=item.title,
        description=item.description,
        suggestion=item.suggestion,
        rule_id=item.rule_id,
        evidence_span_ids=item.evidence_span_ids,
        needs_human_review=item.needs_human_review,
        review_status=item.review_status,
        human_note=item.human_note,
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "disclaimer": get_settings().disclaimer}


@router.get("/config")
def config() -> dict[str, object]:
    settings = get_settings()
    return {
        "allowed_extensions": sorted(settings.allowed_extensions),
        "max_file_bytes": settings.max_file_bytes,
        "max_pages": settings.max_pages,
        "disclaimer": settings.disclaimer,
    }


@router.get("/ruleset", response_model=RulesetStatus)
def ruleset_status() -> RulesetStatus:
    return _ruleset_status()


@router.post("/ruleset/reload", response_model=RulesetStatus)
def reload_ruleset(request: RulesetReloadRequest) -> RulesetStatus:
    """Load or reload the active ruleset.

    Fail-closed and honest: a missing/invalid ruleset returns a normal 200 with
    ``loaded: false`` rather than a 500, and no raw exception text (which could
    include a filesystem path) is echoed to the client.
    """
    _reset_ruleset_cache()
    root = Path(request.root) if request.root else None
    _load_ruleset_into_cache(root)
    return _ruleset_status()


def _llm_config_response() -> LLMConfigResponse:
    store = _llm_config_store()
    config = store.load()
    return LLMConfigResponse(
        provider=config.provider,
        base_url=config.base_url,
        model=config.model,
        key_present=store.key_present(),
    )


@router.get("/llm/config", response_model=LLMConfigResponse)
def get_llm_config() -> LLMConfigResponse:
    return _llm_config_response()


@router.post("/llm/config", response_model=LLMConfigResponse)
def set_llm_config(update: LLMConfigUpdate) -> LLMConfigResponse:
    """Save provider/base_url/model to disk and the key to the credential store.

    The API key is never written to disk or echoed back; the response only tells
    the client whether a key is present.
    """
    try:
        _llm_config_store().save(
            provider=update.provider,
            base_url=update.base_url,
            model=update.model,
            api_key=update.api_key,
        )
    except ReviewError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Base URL 不合法") from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    return _llm_config_response()


@router.post("/llm/health", response_model=LLMHealthResponse)
def llm_health() -> LLMHealthResponse:
    """Probe the configured provider with a minimal request.

    Mock always reports ok. A configured online provider makes one real call; any
    failure is reported as ok:false with a short reason, never the key/body.
    """
    provider = _build_active_provider()
    if isinstance(provider, MockProvider):
        return LLMHealthResponse(ok=True, detail="使用内置 Mock，无需连接")
    probe = LLMRequest(
        model="health",
        system_prompt="健康检查",
        user_content="健康检查",
        evidence_span_ids=["health-check-span"],
    )
    try:
        provider.review(probe)
    except LLMProviderError as exc:
        return LLMHealthResponse(ok=False, detail=str(exc))
    except Exception:
        return LLMHealthResponse(ok=False, detail="连接失败")
    return LLMHealthResponse(ok=True, detail="连接正常")


@router.post("/cases", status_code=status.HTTP_201_CREATED, response_model=CaseCreated)
async def create_case(file: UploadFile = File(...)) -> CaseCreated:
    settings = get_settings()
    try:
        filename = validate_upload_name(file.filename or "", settings.allowed_extensions)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    case_id = str(uuid4())
    try:
        stored = await store_upload_streaming(
            settings.storage_root, case_id, filename, file, settings.max_file_bytes
        )
        _repository().save_case(CaseRecord(case_id=case_id, files=[stored], statistics={"document_count": 1}))
    except UploadTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc)) from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except (OSError, ValueError, PathTraversalError) as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "无法保存案例文件") from exc
    finally:
        close = getattr(file, "close", None)
        if close is not None:
            result = close()
            if hasattr(result, "__await__"):
                await result

    return CaseCreated(
        case_id=case_id,
        file_name=stored.safe_name,
        size=stored.size,
        sha256=stored.sha256,
        storage_relative_path=stored.storage_relative_path,
    )


@router.post("/cases/{case_id}/review", status_code=status.HTTP_201_CREATED, response_model=ReviewSummary)
def review_case(case_id: str) -> ReviewSummary:
    case_id = _case_id(case_id)
    repository = _repository()
    # Read durable case metadata rather than an in-process upload cache.
    case = repository.get_case(case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "案例不存在")
    if not case.files:
        raise HTTPException(status.HTTP_409_CONFLICT, "案例没有可审查的 DOCX")
    try:
        documents = [
            DocxParser().parse(_uploaded_file(case_id, item), document_id=f"{case_id}-{index}")
            for index, item in enumerate(case.files)
        ]
    except (ParseError, OSError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "DOCX 解析失败，仅处理文本型 DOCX") from exc

    loaded = _active_ruleset()
    rules = loaded.rules if loaded else []
    terminology = loaded.terminology if loaded else None
    provider = _build_active_provider()
    run = ReviewPipeline(terminology).run(case_id, documents, rules, provider)
    try:
        repository.save_run(run)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "审查结果无法持久化") from exc
    return ReviewSummary(
        case_id=run.case_id,
        final_status=run.final_status,
        finding_count=len(run.findings),
        fact_count=len(run.facts),
        stages=[record.stage.value for record in run.stage_records],
        rules_loaded=loaded is not None,
        rule_count=len(rules),
    )


@router.get("/cases/{case_id}/findings", response_model=list[FindingResponse])
def list_findings(case_id: str) -> list[FindingResponse]:
    run = _active_run(_case_id(case_id))
    return [_finding_response(item) for item in run.findings]


@router.patch("/findings/{finding_id}", response_model=FindingResponse)
def update_finding(finding_id: str, update: FindingReviewUpdate) -> FindingResponse:
    case_id = _case_id(update.case_id)
    repository = _repository()
    try:
        repository.update_finding_review(case_id, finding_id, update.review_status, update.human_note)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "案例范围内未找到该问题") from exc
    except (TypeError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    run = _active_run(case_id)
    finding = next((item for item in run.findings if item.finding_id == finding_id), None)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "案例范围内未找到该问题")
    return _finding_response(finding)


@router.get("/cases/{case_id}/exports/{format_name}")
def export_case(case_id: str, format_name: ExportFormat):
    case_id = _case_id(case_id)
    run = _active_run(case_id)
    reports_dir = safe_join(get_settings().storage_root, "reports", case_id)
    reports_dir.mkdir(parents=True, exist_ok=True)
    if format_name == "xlsx":
        path = export_excel(run, reports_dir / f"{case_id}.xlsx")
        return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", filename="review-findings.xlsx")
    if format_name == "docx":
        path = export_word(run, reports_dir / f"{case_id}.docx")
        return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document", filename="review-findings.docx")
    path = export_anonymous_package(run, reports_dir / f"{case_id}-anonymous.zip")
    return FileResponse(path, media_type="application/zip", filename="review-anonymous.zip")


@router.post("/cases/{case_id}/delete-confirm")
def move_case_to_recycle_bin(case_id: str) -> dict[str, str]:
    case_id = _case_id(case_id)
    try:
        _repository().delete_case_to_recycle_bin(case_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "案例不存在") from exc
    return {"case_id": case_id, "status": "recycled", "confirmation_required": f"DELETE {case_id}"}


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def permanently_delete_case(case_id: str, request: DeleteCaseRequest) -> None:
    case_id = _case_id(case_id)
    repository = _repository()
    if request.confirmation != f"DELETE {case_id}":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "confirmation must equal 'DELETE {case_id}'")
    try:
        case_paths = repository.case_file_paths(case_id)
        reports_dir = safe_join(get_settings().storage_root, "reports", case_id)
        repository.permanently_delete_case(case_id, request.confirmation)
        for relative_path in case_paths:
            safe_join(get_settings().storage_root, *relative_path.split("/")).unlink(missing_ok=True)
        if reports_dir.exists():
            for artifact in reports_dir.iterdir():
                if artifact.is_file() or artifact.is_symlink():
                    artifact.unlink()
            reports_dir.rmdir()
    except ValueError as exc:
        code = status.HTTP_409_CONFLICT if "recycle bin" in str(exc) else status.HTTP_422_UNPROCESSABLE_CONTENT
        raise HTTPException(code, str(exc)) from exc
