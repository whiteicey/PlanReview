"""Loopback-only local API backed by the durable review repository."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict
import logging
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    CaseCreated,
    DeleteCaseRequest,
    ExpertExperienceDigest,
    ExpertExperienceSummary,
    ExportFormat,
    FindingResponse,
    FindingReviewBody,
    FindingReviewResponse,
    FindingReviewUpdate,
    LLMConfigResponse,
    LLMConfigUpdate,
    LLMHealthResponse,
    LLMStructuredOutputTestResponse,
    ReviewFailureResponse,
    ReviewJobAccepted,
    RunDiagnostics,
    ReviewProgressEventResponse,
    ReviewProgressResponse,
    ReviewSummary,
    RunSummary,
    RulesetReloadRequest,
    RulesetStatus,
)
from app.domain.exceptions import (
    DocxResourceLimitError,
    ParseError,
    PathTraversalError,
    ReviewError,
    UnsafeDocxPackageError,
    UnsupportedFileTypeError,
)
from app.domain.enums import PipelineStage
from app.domain.ids import normalize_review_run_id
from app.llm.config_store import LLMConfigStore
from app.llm.factory import build_provider
from app.llm.limits import (
    MAX_LLM_EVIDENCE_IDS,
    MAX_LLM_FINDINGS,
    MAX_LLM_SINGLE_SPAN_CHARACTERS,
    MAX_LLM_SPANS,
    MAX_LLM_TOTAL_CHARACTERS,
)
from app.llm.mock import MockProvider
from app.llm.provider import (
    LLMConfigurationError,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMValidationError,
    validate_findings,
)
from app.parsers.docx_parser import DocxParser
from app.persistence.db import DatabaseRuntime
from app.persistence.models import CaseRecord
from app.persistence.models import ExpertExperienceSummaryJobORM, FindingORM, ReviewRunORM
from app.persistence.repository import ReviewRepository
from app.experience.repository import ExperienceRepository
from app.experience.schemas import (
    ExperienceDeleteRequest,
    ExperienceJobResponse,
    ExperienceListItem,
    ExperienceListResponse,
    ExperienceMutationResponse,
    ExperienceSummary,
)
from app.reports.exporters import export_anonymous_package, export_excel, export_word
from app.review.pipeline import ReviewPipeline
from app.review.background_jobs import cache_key, execute_review_job
from app.review.parsed_cache import ParsedDocumentCache
from app.rules.ruleset import LoadedRuleset, RulesetError, load_active_ruleset
from app.security.credentials import CredentialStore
from app.settings import get_settings
from app.storage.audit import new_file_operation_event, persist_file_operation_event
from app.storage.case_files import (
    StoredFile,
    UploadTooLargeError,
    cleanup_quarantine,
    discard_staged_upload,
    finalize_staged_upload,
    quarantine_case_storage,
    remove_case_storage,
    restore_quarantined_case,
    stage_upload_streaming,
)
from app.storage.paths import safe_join, validate_upload_name

_REQUEST_SESSION: ContextVar[Session | None] = ContextVar("review_db_session", default=None)
_RUNTIME_LOCK = RLock()


def _runtime_for_request(request: Request) -> DatabaseRuntime:
    desired_path = get_settings().db_path.expanduser().resolve()
    with _RUNTIME_LOCK:
        runtime = getattr(request.app.state, "database_runtime", None)
        if runtime is None or runtime.path != desired_path:
            if runtime is not None:
                runtime.dispose()
            runtime = DatabaseRuntime(desired_path)
            runtime.initialize()
            request.app.state.database_runtime = runtime
        return runtime


async def get_db_session(request: Request):
    """Provide and always close one independent SQLAlchemy Session per request."""
    session = _runtime_for_request(request).session()
    token = _REQUEST_SESSION.set(session)
    try:
        yield session
    finally:
        _REQUEST_SESSION.reset(token)
        session.close()


router = APIRouter(
    prefix="/api",
    tags=["local review"],
    dependencies=[Depends(get_db_session)],
)

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
    if config.provider == "mock":
        return build_provider(config, None)
    try:
        api_key = store.get_key()
    except Exception:
        return build_provider(config, None, credential_error=True)
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
    session = _REQUEST_SESSION.get()
    if session is None:
        raise RuntimeError("repository requested outside request database lifecycle")
    return ReviewRepository(session)


def _parsed_cache(request: Request) -> ParsedDocumentCache:
    cache = getattr(request.app.state, "parsed_document_cache", None)
    if cache is None:
        cache = ParsedDocumentCache(max_cases=8)
        request.app.state.parsed_document_cache = cache
    return cache


def _uploaded_file(case_id: str, file: StoredFile) -> Path:
    try:
        return safe_join(get_settings().storage_root, *file.storage_relative_path.split("/"))
    except PathTraversalError as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "stored file path is invalid") from exc


def _latest_successful_run(case_id: str):
    run = _repository().get_latest_successful_run(case_id)
    if run is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "案例没有已成功完成的审查结果")
    return run


def _run_id(value: str) -> str:
    try:
        return normalize_review_run_id(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "run_id 必须是标准 UUID") from exc


def _run_for_case(case_id: str, run_id: str):
    run = _repository().get_run_for_case(_case_id(case_id), _run_id(run_id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "案例范围内未找到该审查运行")
    return run


def _finding_response(item) -> FindingResponse:
    if item.run_id is None:
        raise ValueError("finding is not bound to a review run")
    session = _REQUEST_SESSION.get()
    row = None if session is None else session.scalar(
        select(FindingORM).join(ReviewRunORM).where(
            ReviewRunORM.run_id == item.run_id,
            FindingORM.finding_id == item.finding_id,
        )
    )
    job = None if row is None or not row.experience_summary_job_id else session.get(
        ExpertExperienceSummaryJobORM, row.experience_summary_job_id
    )
    experience_status = (
        "NOT_REQUESTED" if not item.is_expert_experience else (job.status if job is not None else "NOT_REQUESTED")
    )
    return FindingResponse(
        run_id=item.run_id,
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
        reviewed_at=item.reviewed_at,
        is_expert_experience=item.is_expert_experience,
        experience_saved_at=item.experience_saved_at,
        experience_updated_at=item.experience_updated_at,
        experience_id=None if job is None else job.job_id,
        experience_summary_status=experience_status,
    )


_FAILURE_MESSAGES = {
    PipelineStage.UPLOADED: "审查输入校验失败，请检查上传文件。",
    PipelineStage.PARSED: "文档解析失败，请确认文件为可读取的文本型 DOCX。",
    PipelineStage.EXTRACTED: "参数提取阶段失败，请检查文档内容。",
    PipelineStage.NORMALIZED: "参数单位规范化失败，请检查数值和单位。",
    PipelineStage.RULE_CHECKED: "规则校验未完成，请检查规则配置或重试。",
    PipelineStage.LLM_REVIEWED: "AI 复核输出未通过证据校验，请重试或联系管理员。",
    PipelineStage.RECONCILED: "审查结果整理失败，请重试或联系管理员。",
    PipelineStage.READY_FOR_HUMAN_REVIEW: "审查结果准备失败，请重试或联系管理员。",
    PipelineStage.FAILED: "本次审查未完成，请重试或联系管理员。",
}


def _failure_response(run) -> ReviewFailureResponse:
    failed_record = next(
        (
            record
            for record in run.stage_records
            if record.status == "failed" and record.stage is not PipelineStage.FAILED
        ),
        None,
    )
    failed_stage = failed_record.stage if failed_record is not None else PipelineStage.FAILED
    return ReviewFailureResponse(
        case_id=run.case_id,
        run_id=run.run_id,
        final_status="FAILED",
        failed_stage=failed_stage.value,
        failure_detail=_FAILURE_MESSAGES.get(failed_stage, _FAILURE_MESSAGES[PipelineStage.FAILED]),
    )


def _run_summary(run) -> RunSummary:
    return RunSummary(
        case_id=run.case_id,
        run_id=run.run_id,
        final_status=run.final_status,
        created_at=run.created_at,
        finding_count=len(run.findings),
        fact_count=len(run.facts),
        stages=[record.stage.value for record in run.stage_records],
        llm_provider=run.llm_provider,
        llm_model=run.llm_model,
        llm_status=run.llm_status.value,
        llm_finding_count=run.llm_finding_count,
        llm_error_summary=run.llm_error_summary,
        validation_reason_code=run.validation_reason_code,
        candidate_count=run.candidate_count,
        valid_count=run.valid_count,
        rejected_count=run.rejected_count,
        available_span_count=run.available_span_count,
        selected_span_count=run.selected_span_count,
        selected_character_count=run.selected_character_count,
        coverage_ratio=run.coverage_ratio,
    )


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "disclaimer": get_settings().disclaimer}


@router.get("/config")
def config() -> dict[str, object]:
    settings = get_settings()
    return {
        "allowed_extensions": sorted(settings.allowed_extensions),
        "max_upload_bytes": settings.max_upload_bytes,
        "max_zip_members": settings.max_zip_members,
        "max_zip_uncompressed_bytes": settings.max_zip_uncompressed_bytes,
        "max_zip_member_bytes": settings.max_zip_member_bytes,
        "max_zip_compression_ratio": settings.max_zip_compression_ratio,
        "max_document_characters": settings.max_document_characters,
        "max_paragraphs": settings.max_paragraphs,
        "max_tables": settings.max_tables,
        "max_table_cells": settings.max_table_cells,
        "max_llm_spans": MAX_LLM_SPANS,
        "max_llm_total_characters": MAX_LLM_TOTAL_CHARACTERS,
        "max_llm_single_span_characters": MAX_LLM_SINGLE_SPAN_CHARACTERS,
        "max_llm_evidence_ids": MAX_LLM_EVIDENCE_IDS,
        "max_llm_findings": MAX_LLM_FINDINGS,
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
    _load_ruleset_into_cache()
    return _ruleset_status()


def _llm_config_response() -> LLMConfigResponse:
    store = _llm_config_store()
    config = store.load()
    credential_available = True
    configuration_error = config.configuration_error
    try:
        key_present = store.key_present()
    except Exception:
        key_present = False
        credential_available = False
        configuration_error = "系统凭据存储不可用"
    return LLMConfigResponse(
        provider=config.provider,
        base_url=config.base_url,
        model=config.model,
        allow_private_endpoint=config.allow_private_endpoint,
        key_present=key_present,
        credential_storage_available=credential_available,
        configuration_error=configuration_error,
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
            allow_private_endpoint=update.allow_private_endpoint,
        )
    except ReviewError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Base URL 不合法") from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "修改在线端点或私网模式前必须重新输入 API Key。",
        ) from exc
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "系统凭据存储不可用") from exc
    return _llm_config_response()


@router.delete("/llm/config/credentials", response_model=LLMConfigResponse)
def clear_llm_credentials() -> LLMConfigResponse:
    try:
        _llm_config_store().delete_key()
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "系统凭据存储不可用") from exc
    return _llm_config_response()


@router.post("/llm/health", response_model=LLMHealthResponse)
def llm_health() -> LLMHealthResponse:
    """Test transport/auth/model entry only; do not parse a Finding response."""
    provider = _build_active_provider()
    if isinstance(provider, MockProvider):
        return LLMHealthResponse(ok=True, detail="使用内置 Mock；未执行真实基础连接测试")
    try:
        provider.test_connection()
    except LLMConfigurationError:
        return LLMHealthResponse(ok=False, detail="LLM 配置不完整或凭据存储不可用")
    except LLMProviderError:
        return LLMHealthResponse(ok=False, detail="AI 服务连接失败")
    except Exception:
        return LLMHealthResponse(ok=False, detail="连接失败")
    return LLMHealthResponse(ok=True, detail="基础连接正常；尚未验证结构化审查输出。")


@router.post("/llm/structured-output-test", response_model=LLMStructuredOutputTestResponse)
def llm_structured_output_test() -> LLMStructuredOutputTestResponse:
    """Exercise the formal validation chain using two non-business evidence IDs."""
    provider = _build_active_provider()
    if isinstance(provider, MockProvider):
        return LLMStructuredOutputTestResponse(
            connection_ok=True,
            structured_output_ok=False,
            detail="使用内置 Mock；未执行真实结构化输出测试",
        )
    evidence_ids = ["structured-test-span-1", "structured-test-span-2"]
    request = LLMRequest(
        model=getattr(provider, "model_name", None) or "structured-output-test",
        system_prompt=(
            "这是结构化输出测试。必须恰好返回一条合法Finding，同时引用给出的两个证据编号；"
            "只返回JSON数组，不得输出说明、Markdown、代码围栏或<think>。"
        ),
        user_content=(
            "[structured-test-span-1]\n虚拟证据：计划产能为100。\n\n"
            "[structured-test-span-2]\n虚拟证据：同一计划产能为200。"
        ),
        evidence_span_ids=evidence_ids,
    )
    try:
        response = provider.review(request)
        if not isinstance(response, LLMResponse):
            raise LLMValidationError("missing_field")
        findings = validate_findings(response.findings, evidence_ids)
    except LLMConfigurationError:
        return LLMStructuredOutputTestResponse(
            connection_ok=False, structured_output_ok=False,
            detail="LLM 配置不完整或凭据存储不可用",
        )
    except LLMProviderError:
        return LLMStructuredOutputTestResponse(
            connection_ok=False, structured_output_ok=False,
            detail="AI 服务连接失败",
        )
    except LLMValidationError as exc:
        return LLMStructuredOutputTestResponse(
            connection_ok=True,
            structured_output_ok=False,
            validation_reason_code=exc.reason_code,
            candidate_count=exc.candidate_count,
            valid_count=exc.valid_count,
            rejected_count=exc.rejected_count,
            detail=str(exc),
        )
    candidate_count = len(findings)
    if candidate_count == 0:
        return LLMStructuredOutputTestResponse(
            connection_ok=True, structured_output_ok=False,
            candidate_count=0, valid_count=0, rejected_count=0,
            detail="结构化响应合法，但未返回测试要求的一条问题",
        )
    if candidate_count != 1:
        return LLMStructuredOutputTestResponse(
            connection_ok=True, structured_output_ok=False,
            candidate_count=candidate_count, valid_count=candidate_count, rejected_count=0,
            detail="结构化响应合法，但问题数量不符合测试要求",
        )
    if set(findings[0]["evidence_span_ids"]) != set(evidence_ids):
        return LLMStructuredOutputTestResponse(
            connection_ok=True, structured_output_ok=False,
            validation_reason_code="invalid_evidence",
            candidate_count=1, valid_count=0, rejected_count=1,
            detail="证据引用缺失或不在本次送审范围",
        )
    return LLMStructuredOutputTestResponse(
        connection_ok=True, structured_output_ok=True,
        candidate_count=1, valid_count=1, rejected_count=0,
        detail="基础连接和结构化输出校验均通过",
    )


@router.post("/cases", status_code=status.HTTP_201_CREATED, response_model=CaseCreated)
async def create_case(file: UploadFile = File(...), request: Request = None) -> CaseCreated:
    settings = get_settings()
    try:
        filename = validate_upload_name(file.filename or "", settings.allowed_extensions)
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    case_id = str(uuid4())
    staged = None
    stored = None
    parsed_document = None
    try:
        staged = await stage_upload_streaming(
            settings.storage_root, case_id, filename, file, settings.max_upload_bytes
        )
        # Parsing before the atomic move prevents an invalid document from
        # entering the durable case directory.
        parsed_document = DocxParser().parse(staged.temporary_path, document_id=f"{case_id}-0")
        stored = finalize_staged_upload(settings.storage_root, staged)
        repository = _repository()
        try:
            repository.save_case(
                CaseRecord(
                    case_id=case_id,
                    files=[stored],
                    statistics={"document_count": 1},
                )
            )
            if request is not None:
                _parsed_cache(request).put(case_id, stored.sha256, [parsed_document])
        except Exception as exc:
            recovery_required = False
            summary = "compensation completed"
            try:
                remove_case_storage(settings.storage_root, case_id)
            except Exception:
                recovery_required = True
                summary = "file cleanup failed"
            event = new_file_operation_event(
                case_id,
                "create",
                "database_commit",
                "failed",
                summary,
                recovery_required=recovery_required,
            )
            persist_file_operation_event(repository, settings.runtime_root, event)
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "无法保存案例"
            ) from exc
    except UploadTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "DOCX exceeds configured resource limits") from exc
    except DocxResourceLimitError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "DOCX exceeds configured resource limits") from exc
    except UnsafeDocxPackageError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "DOCX package structure is not supported") from exc
    except ParseError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "DOCX 文档结构无法解析") from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc
    except HTTPException:
        raise
    except (OSError, ValueError, PathTraversalError) as exc:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "无法保存案例文件") from exc
    finally:
        if staged is not None:
            try:
                discard_staged_upload(settings.storage_root, staged)
            except (OSError, PathTraversalError):
                pass
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
def review_case(case_id: str, request: Request) -> ReviewSummary:
    case_id = _case_id(case_id)
    repository = _repository()
    # Read durable case metadata rather than an in-process upload cache.
    case = repository.get_case(case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "案例不存在")
    if not case.files:
        raise HTTPException(status.HTTP_409_CONFLICT, "案例没有可审查的 DOCX")
    try:
        documents = _parsed_cache(request).get(case_id, cache_key(case))
        if documents is None:
            documents = [
                DocxParser().parse(_uploaded_file(case_id, item), document_id=f"{case_id}-{index}")
                for index, item in enumerate(case.files)
            ]
            _parsed_cache(request).put(case_id, cache_key(case), documents)
    except DocxResourceLimitError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "DOCX exceeds configured resource limits") from exc
    except (ParseError, UnsafeDocxPackageError, OSError) as exc:
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
    if run.final_status == "FAILED":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=_failure_response(run).model_dump(mode="json"),
        )
    return ReviewSummary(
        case_id=run.case_id,
        run_id=run.run_id,
        final_status=run.final_status,
        finding_count=len(run.findings),
        fact_count=len(run.facts),
        stages=[record.stage.value for record in run.stage_records],
        rules_loaded=loaded is not None,
        rule_count=len(rules),
        llm_provider=run.llm_provider,
        llm_model=run.llm_model,
        llm_status=run.llm_status.value,
        llm_finding_count=run.llm_finding_count,
        llm_error_summary=run.llm_error_summary,
        validation_reason_code=run.validation_reason_code,
        candidate_count=run.candidate_count,
        valid_count=run.valid_count,
        rejected_count=run.rejected_count,
        available_span_count=run.available_span_count,
        selected_span_count=run.selected_span_count,
        selected_character_count=run.selected_character_count,
        coverage_ratio=run.coverage_ratio,
    )


@router.post(
    "/cases/{case_id}/review-jobs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReviewJobAccepted,
)
def create_review_job(
    case_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> ReviewJobAccepted:
    case_id = _case_id(case_id)
    repository = _repository()
    case = repository.get_case(case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "案例不存在")
    if not case.files:
        raise HTTPException(status.HTTP_409_CONFLICT, "案例没有可审查的 DOCX")
    run_id = str(uuid4())
    repository.create_running_run(case_id, run_id)
    runtime = _runtime_for_request(request)
    background_tasks.add_task(
        execute_review_job,
        runtime,
        _parsed_cache(request),
        get_settings(),
        case_id,
        run_id,
        _active_ruleset(),
        _build_active_provider(),
    )
    return ReviewJobAccepted(run_id=run_id, status="RUNNING")


@router.get("/runs/{run_id}/progress", response_model=ReviewProgressResponse)
def get_review_progress(
    run_id: str,
    after_sequence: int = Query(default=0, ge=0),
) -> ReviewProgressResponse:
    run_id = _run_id(run_id)
    repository = _repository()
    run = repository.get_run(run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "审查运行不存在")
    events = repository.list_progress_events(run_id, after_sequence)
    return ReviewProgressResponse(
        run_id=run_id,
        run_status=run.final_status,
        last_sequence=repository.last_progress_sequence(run_id),
        events=[
            ReviewProgressEventResponse(
                sequence=item.sequence,
                stage=item.stage,
                event_type=item.event_type,
                status=item.status,
                message=item.message,
                details=item.details,
                created_at=item.created_at,
            )
            for item in events
        ],
    )


@router.get("/cases/{case_id}/runs", response_model=list[RunSummary])
def list_review_runs(case_id: str) -> list[RunSummary]:
    case_id = _case_id(case_id)
    if _repository().get_case(case_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "案例不存在")
    return [_run_summary(run) for run in _repository().list_runs(case_id)]


@router.get("/cases/{case_id}/runs/{run_id}", response_model=RunSummary)
def get_review_run(case_id: str, run_id: str) -> RunSummary:
    return _run_summary(_run_for_case(case_id, run_id))


@router.get(
    "/cases/{case_id}/runs/{run_id}/diagnostics",
    response_model=RunDiagnostics,
)
def get_run_diagnostics(case_id: str, run_id: str) -> RunDiagnostics:
    """Return bounded lifecycle and batch diagnostics without review payloads."""
    run = _run_for_case(case_id, run_id)
    batch_metrics = list(run.batch_metrics or [])
    selection_diagnostics = {}
    for metric in batch_metrics:
        if isinstance(metric, dict) and isinstance(metric.get("selection_diagnostics"), dict):
            selection_diagnostics = dict(metric["selection_diagnostics"])
            break
    integrity = {
        "packet_ledger_entries": (run.packet_lifecycle_ledger or {}).get("ledger_entry_count", 0),
        "candidate_ledger_entries": (run.ai_candidate_lifecycle_ledger or {}).get("ledger_entry_count", 0),
        "packet_ledger_truncated": bool((run.packet_lifecycle_ledger or {}).get("ledger_truncated", False)),
        "candidate_ledger_truncated": bool((run.ai_candidate_lifecycle_ledger or {}).get("ledger_truncated", False)),
        "batch_count": len(batch_metrics),
        "finding_count": len(run.findings),
        "rule_result_count": len(run.rule_results),
        "distinct_rule_id_count": len({item.rule_id for item in run.rule_results}),
    }
    return RunDiagnostics(
        case_id=run.case_id,
        run_id=run.run_id,
        evidence_selector_version=run.evidence_selector_version,
        packet_lifecycle_ledger=run.packet_lifecycle_ledger or {},
        ai_candidate_lifecycle_ledger=run.ai_candidate_lifecycle_ledger or {},
        rule_metrics=run.rule_metrics or {},
        batch_metrics=batch_metrics,
        selection_diagnostics=selection_diagnostics,
        integrity=integrity,
    )


@router.get(
    "/cases/{case_id}/runs/{run_id}/findings",
    response_model=list[FindingResponse],
)
def list_run_findings(case_id: str, run_id: str) -> list[FindingResponse]:
    run = _run_for_case(case_id, run_id)
    if run.final_status == "FAILED":
        raise HTTPException(status.HTTP_409_CONFLICT, "审查未完成，不能把失败运行当作问题列表")
    return [_finding_response(item) for item in run.findings]


@router.get("/cases/{case_id}/findings", response_model=list[FindingResponse])
def list_findings(case_id: str) -> list[FindingResponse]:
    run = _latest_successful_run(_case_id(case_id))
    return [_finding_response(item) for item in run.findings]


def _update_run_finding(
    request: Request,
    case_id: str,
    run_id: str,
    finding_id: str,
    review_status,
    human_note: str | None,
    is_expert_experience: bool | None,
) -> FindingReviewResponse:
    case_id = _case_id(case_id)
    run_id = _run_id(run_id)
    repository = _repository()
    try:
        repository.update_finding_review(
            case_id,
            run_id,
            finding_id,
            review_status,
            human_note,
            is_expert_experience,
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "指定 Run 中未找到该问题") from exc
    except (TypeError, ValueError) as exc:
        detail = (
            "专家备注最大 4000 字"
            if human_note is not None and len(human_note) > 4_000
            else "专家备注疑似包含敏感凭据"
        )
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail) from exc
    run = repository.get_run_for_case(case_id, run_id)
    finding = None if run is None else next(
        (item for item in run.findings if item.finding_id == finding_id), None
    )
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "指定 Run 中未找到该问题")
    experience_repository = ExperienceRepository(_REQUEST_SESSION.get())
    requested = None
    request_failed = False
    try:
        requested = experience_repository.synchronize_after_review(case_id, run_id, finding_id)
        if requested is not None and requested.status == "PENDING":
            runner = getattr(request.app.state, "experience_job_runner", None)
            if runner is not None:
                runner.enqueue(requested.job_id)
    except Exception:
        request_failed = True
        logging.warning("Unable to create expert-experience job after review save", exc_info=True)
    finding_row = experience_repository._finding(case_id, run_id, finding_id)
    assert finding_row is not None
    job = None if not finding_row.experience_summary_job_id else experience_repository.get_job(
        finding_row.experience_summary_job_id
    )
    job_status = "FAILED" if request_failed else (
        "NOT_REQUESTED" if requested is None else requested.status
    )
    total_count = experience_repository.active_count()
    return FindingReviewResponse(
        **_finding_response(finding).model_dump(exclude={"experience_id", "experience_summary_status"}),
        review_saved=True,
        expert_experience_saved=job_status == "COMPLETED" and job is not None and not job.experience_is_deleted,
        expert_experience_requested=finding.is_expert_experience,
        expert_experience_total_count=total_count,
        experience_id=None if job is None else job.job_id,
        experience_summary_status=job_status,
        experience_summary_job_id=None if job is None else job.job_id,
        source_run_id=run_id,
        source_finding_id=finding_id,
        finding_row_id=finding_row.id,
    )


@router.get("/expert-experiences/summary", response_model=ExpertExperienceSummary)
def expert_experience_summary() -> ExpertExperienceSummary:
    summary = _repository().get_expert_experience_summary()
    return ExpertExperienceSummary(total_count=summary.total_count, updated_at=summary.updated_at)


@router.get("/expert-experiences/digest", response_model=ExpertExperienceDigest)
def expert_experience_digest(limit: int = Query(default=8, ge=1, le=50)) -> ExpertExperienceDigest:
    digest = _repository().get_expert_experience_digest(limit)
    return ExpertExperienceDigest(
        total_count=digest.total_count,
        updated_at=digest.updated_at,
        status_counts=digest.status_counts,
        categories=[asdict(item) for item in digest.categories],
        recent_conclusions=[asdict(item) for item in digest.recent_conclusions],
    )


def _experience_job_response(job: ExpertExperienceSummaryJobORM) -> ExperienceJobResponse:
    finding = _REQUEST_SESSION.get().get(FindingORM, job.finding_row_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "专家经验来源不存在")
    summary = ExperienceSummary.model_validate(job.summary_json) if job.summary_json else None
    return ExperienceJobResponse(
        job_id=job.job_id,
        experience_id=job.job_id,
        source_run_id=job.source_run_id,
        source_finding_id=job.source_finding_id,
        finding_row_id=job.finding_row_id,
        status="DELETED" if job.experience_is_deleted else job.status,
        expert_review_status=finding.review_status,
        experience_summary=summary,
        error_summary=job.error_summary,
        expert_experience_total_count=ExperienceRepository(_REQUEST_SESSION.get()).active_count(),
        updated_at=job.updated_at,
    )


@router.get("/expert-experience-summary-jobs/{job_id}", response_model=ExperienceJobResponse)
def get_experience_summary_job(job_id: str) -> ExperienceJobResponse:
    job = ExperienceRepository(_REQUEST_SESSION.get()).get_job(_run_id(job_id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "专家经验归纳任务不存在")
    return _experience_job_response(job)


@router.post(
    "/cases/{case_id}/runs/{run_id}/findings/{finding_id}/expert-experience/retry",
    response_model=ExperienceJobResponse,
)
def retry_experience_summary(
    case_id: str, run_id: str, finding_id: str, request: Request
) -> ExperienceJobResponse:
    repository = ExperienceRepository(_REQUEST_SESSION.get())
    try:
        requested = repository.synchronize_after_review(
            _case_id(case_id), _run_id(run_id), finding_id, force_retry=True
        )
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "指定Run中未找到该问题") from exc
    if requested is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "该问题未请求沉淀专家经验")
    if requested.status == "PENDING":
        runner = getattr(request.app.state, "experience_job_runner", None)
        if runner is not None:
            runner.enqueue(requested.job_id)
    job = repository.get_job(requested.job_id)
    assert job is not None
    return _experience_job_response(job)


@router.get("/expert-experiences", response_model=ExperienceListResponse)
def list_expert_experiences(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    q: str | None = Query(default=None, max_length=200),
    severity: str | None = Query(default=None),
    origin: str | None = Query(default=None),
    rule_id: str | None = Query(default=None, max_length=255),
    view: str = Query(default="active", pattern="^(active|deleted|failed)$"),
) -> ExperienceListResponse:
    repository = ExperienceRepository(_REQUEST_SESSION.get())
    rows, total, active, deleted, failed, updated_at = repository.list_current(
        page=page, page_size=page_size, query=q, severity=severity,
        origin=origin, rule_id=rule_id, view=view,
    )
    items = []
    for job, finding in rows:
        items.append(ExperienceListItem(
            experience_id=job.job_id,
            source_case_id=finding.review_run.case_id,
            source_run_id=job.source_run_id,
            source_finding_id=job.source_finding_id,
            finding_row_id=finding.id,
            status="DELETED" if job.experience_is_deleted else job.status,
            expert_review_status=finding.review_status,
            title=finding.title,
            category=finding.category,
            severity=finding.severity,
            origin=finding.origin,
            rule_id=finding.rule_id,
            expert_note=finding.human_note,
            summary=ExperienceSummary.model_validate(job.summary_json) if job.summary_json else None,
            summary_model=job.summary_model,
            saved_at=job.completed_at,
            updated_at=job.updated_at,
        ))
    return ExperienceListResponse(
        items=items, total_count=total, active_count=active, deleted_count=deleted,
        failed_count=failed, page=page, page_size=page_size, updated_at=updated_at,
    )


@router.delete("/expert-experiences/{experience_id}", response_model=ExperienceMutationResponse)
def delete_expert_experience(
    experience_id: str,
    body: ExperienceDeleteRequest | None = Body(default=None),
) -> ExperienceMutationResponse:
    repository = ExperienceRepository(_REQUEST_SESSION.get())
    try:
        job = repository.delete(_run_id(experience_id), None if body is None else body.reason)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "专家经验不存在") from exc
    return ExperienceMutationResponse(
        experience_id=job.job_id, status="DELETED", deleted=True,
        expert_experience_total_count=repository.active_count(),
        deleted_experience_count=repository.deleted_count(),
    )


@router.post("/expert-experiences/{experience_id}/restore", response_model=ExperienceMutationResponse)
def restore_expert_experience(experience_id: str, request: Request) -> ExperienceMutationResponse:
    repository = ExperienceRepository(_REQUEST_SESSION.get())
    try:
        job, requested = repository.restore(_run_id(experience_id))
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "专家经验不存在") from exc
    if requested is not None:
        runner = getattr(request.app.state, "experience_job_runner", None)
        if runner is not None:
            runner.enqueue(requested.job_id)
    return ExperienceMutationResponse(
        experience_id=job.job_id, status=job.status, deleted=job.experience_is_deleted,
        expert_experience_total_count=repository.active_count(),
        deleted_experience_count=repository.deleted_count(),
    )


@router.patch(
    "/cases/{case_id}/runs/{run_id}/findings/{finding_id}",
    response_model=FindingReviewResponse,
)
def update_run_finding(
    case_id: str, run_id: str, finding_id: str, update: FindingReviewBody, request: Request
) -> FindingReviewResponse:
    return _update_run_finding(
        request,
        case_id,
        run_id,
        finding_id,
        update.review_status,
        update.human_note,
        update.is_expert_experience,
    )


@router.patch("/findings/{finding_id}", response_model=FindingReviewResponse)
def update_finding(finding_id: str, update: FindingReviewUpdate, request: Request) -> FindingReviewResponse:
    return _update_run_finding(
        request,
        update.case_id,
        update.run_id,
        finding_id,
        update.review_status,
        update.human_note,
        update.is_expert_experience,
    )


@router.get("/cases/{case_id}/exports/{format_name}")
def export_case(case_id: str, format_name: ExportFormat):
    case_id = _case_id(case_id)
    run = _latest_successful_run(case_id)
    reports_dir = safe_join(get_settings().storage_root, "reports", case_id)
    reports_dir.mkdir(parents=True, exist_ok=True)
    if format_name == "xlsx":
        evidence_texts: dict[str, str] = {}
        evidence_file_names: dict[str, str] = {}
        case = _repository().get_case(case_id)
        if case is not None:
            for index, item in enumerate(case.files):
                parsed = DocxParser().parse(
                    _uploaded_file(case_id, item), document_id=f"{case_id}-{index}"
                )
                for span in parsed.spans:
                    evidence_texts[span.span_id] = span.text
                    evidence_file_names[span.span_id] = item.safe_name
        path = export_excel(
            run,
            reports_dir / f"{case_id}.xlsx",
            evidence_texts=evidence_texts,
            evidence_file_names=evidence_file_names,
        )
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
    if case_id not in repository.recycle_bin_case_ids():
        raise HTTPException(status.HTTP_409_CONFLICT, "case must be in recycle bin before permanent deletion")
    settings = get_settings()
    event_id = str(uuid4())
    try:
        quarantined = quarantine_case_storage(settings.storage_root, case_id, event_id)
    except (OSError, ValueError, PathTraversalError) as exc:
        event = new_file_operation_event(
            case_id,
            "delete",
            "quarantine",
            "failed",
            "file restore failed",
            recovery_required=True,
        )
        persist_file_operation_event(repository, settings.runtime_root, event)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "无法安全隔离案例文件") from exc

    try:
        repository.permanently_delete_case(case_id, request.confirmation)
    except ValueError as exc:
        try:
            restore_quarantined_case(quarantined)
            recovery_required = False
            summary = "compensation completed"
        except Exception:
            recovery_required = True
            summary = "file restore failed"
        event = new_file_operation_event(
            case_id,
            "delete",
            "database_commit",
            "failed",
            summary,
            recovery_required=recovery_required,
        )
        persist_file_operation_event(repository, settings.runtime_root, event)
        raise HTTPException(status.HTTP_409_CONFLICT, "案例删除条件不满足") from exc
    except Exception as exc:
        try:
            restore_quarantined_case(quarantined)
            recovery_required = False
            summary = "compensation completed"
        except Exception:
            recovery_required = True
            summary = "file restore failed"
        event = new_file_operation_event(
            case_id,
            "delete",
            "database_commit",
            "failed",
            summary,
            recovery_required=recovery_required,
        )
        persist_file_operation_event(repository, settings.runtime_root, event)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "无法删除案例") from exc

    try:
        cleanup_quarantine(settings.storage_root, quarantined)
    except Exception as exc:
        event = new_file_operation_event(
            case_id,
            "delete",
            "quarantine_cleanup",
            "failed",
            "file cleanup failed",
            recovery_required=True,
        )
        persist_file_operation_event(repository, settings.runtime_root, event)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "案例已删除但文件清理未完成") from exc

    event = new_file_operation_event(
        case_id,
        "delete",
        "completed",
        "completed",
        "operation completed",
        recovery_required=False,
    )
    persist_file_operation_event(repository, settings.runtime_root, event)
