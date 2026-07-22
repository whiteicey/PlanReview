"""In-process background execution for local asynchronous reviews."""

from __future__ import annotations

import logging
from uuid import uuid4

from app.llm.provider import LLMProvider
from app.parsers.docx_parser import DocxParser, ParsedDocument
from app.persistence.db import DatabaseRuntime
from app.persistence.models import CaseRecord
from app.persistence.repository import ReviewRepository
from app.review.parsed_cache import ParsedDocumentCache
from app.review.pipeline import ReviewPipeline
from app.rules.ruleset import LoadedRuleset
from app.settings import Settings
from app.storage.paths import safe_join


def cache_key(case: CaseRecord) -> str:
    return ":".join(item.sha256 for item in case.files)


def _document_metrics(documents: list[ParsedDocument]) -> dict[str, int]:
    spans = [span for document in documents for span in document.spans]
    sections = {
        tuple(span.section_path)
        for span in spans
        if span.section_path
    }
    tables = {
        (span.document_id, span.table_index)
        for span in spans
        if span.table_index is not None
    }
    return {
        "section_count": len(sections),
        "table_count": len(tables),
        "span_count": len(spans),
    }


def execute_review_job(
    runtime: DatabaseRuntime,
    parsed_cache: ParsedDocumentCache,
    settings: Settings,
    case_id: str,
    run_id: str,
    loaded_ruleset: LoadedRuleset | None,
    provider: LLMProvider,
) -> None:
    """Claim and execute exactly one worker for a Run."""
    worker_token = str(uuid4())
    session = runtime.session()
    repository = ReviewRepository(session)
    run = None
    finalized = False
    try:
        if not repository.claim_running_run(run_id, worker_token):
            return

        def progress(stage, event_type, status, message, details=None) -> None:
            try:
                repository.append_progress_event(
                    run_id, stage, event_type, status, message, details
                )
            except Exception:
                logging.warning(
                    "Progress event persistence failed for %s/%s; using safe fallback",
                    stage,
                    event_type,
                    exc_info=True,
                )
                fallback_message = (
                    "AI批次处理完成"
                    if event_type == "AI_BATCH_COMPLETED"
                    else "审查进度已更新"
                )
                try:
                    repository.append_progress_event(
                        run_id, stage, event_type, status, fallback_message, None
                    )
                except Exception:
                    logging.warning(
                        "Safe progress fallback also failed for %s/%s",
                        stage,
                        event_type,
                        exc_info=True,
                    )

        progress("INPUT_VALIDATION", "TASK_CREATED", "running", "审查任务已创建，后台执行者已就绪")
        progress("INPUT_VALIDATION", "STAGE_STARTED", "running", "正在校验案例和文档状态")
        case = repository.get_case(case_id)
        if case is None or not case.files:
            raise ValueError("case is unavailable")
        progress(
            "INPUT_VALIDATION", "STAGE_COMPLETED", "completed", "当前文档满足审查条件",
            {"document_role": "CURRENT", "document_status": "PARSED"},
        )

        key = cache_key(case)
        documents = parsed_cache.get(case_id, key)
        progress("DOCUMENT_PARSE", "STAGE_STARTED", "running", "正在读取文档解析结果")
        if documents is not None:
            metrics = _document_metrics(documents)
            progress(
                "DOCUMENT_PARSE", "STAGE_COMPLETED", "completed", "已读取现有解析结果",
                {**metrics, "cache_hit": True},
            )
        else:
            documents = [
                DocxParser().parse(
                    safe_join(settings.storage_root, *item.storage_relative_path.split("/")),
                    document_id=f"{case_id}-{index}",
                )
                for index, item in enumerate(case.files)
            ]
            parsed_cache.put(case_id, key, documents)
            metrics = _document_metrics(documents)
            progress(
                "DOCUMENT_PARSE", "STAGE_COMPLETED", "completed",
                f"文档解析完成，已生成 {metrics['span_count']} 个证据片段",
                {**metrics, "cache_hit": False},
            )

        rules = loaded_ruleset.rules if loaded_ruleset else []
        terminology = loaded_ruleset.terminology if loaded_ruleset else None
        run = ReviewPipeline(terminology).run(
            case_id,
            documents,
            rules,
            provider,
            run_id=run_id,
            progress=progress,
            checkpoint=lambda current_run, findings: repository.checkpoint_running_run(
                current_run, worker_token, findings
            ),
        )
        # This commit is the terminal compare-and-set from RUNNING. Any
        # diagnostics or callbacks after it must never downgrade the result.
        repository.finish_running_run(run, worker_token)
        finalized = True
    except Exception:
        logging.exception("Background review job failed for run %s", run_id)
        if finalized:
            logging.warning(
                "Post-finalization callback failed for run %s; preserving terminal state",
                run_id,
            )
            return
        try:
            current = repository.get_run(run_id)
            if current is not None and current.final_status != "RUNNING":
                logging.warning(
                    "Run %s already reached terminal state %s; suppressing duplicate failure event",
                    run_id,
                    current.final_status,
                )
                return
            repository.append_progress_event(
                run_id,
                "FAILED",
                "TASK_FAILED",
                "failed",
                "审查任务执行失败，请检查配置或重新运行。",
            )
        except Exception:
            logging.exception("Unable to persist safe failure event for run %s", run_id)
        try:
            repository.set_running_run_failed(run_id, worker_token)
        except Exception:
            logging.exception("Unable to mark failed background run %s", run_id)
    finally:
        session.close()

