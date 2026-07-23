"""Single bounded executor and lease heartbeat for experience jobs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
from threading import Event, Lock, Thread
from uuid import uuid4

from app.experience.evidence import resolve_evidence
from app.experience.repository import ExperienceRepository
from app.experience.summarizer import build_experience_summarizer, safe_summary_error
from app.llm.config_store import LLMConfigStore
from app.llm.provider import LLMProviderError
from app.persistence.db import DatabaseRuntime
from app.persistence.models import FindingORM
from app.review.parsed_cache import ParsedDocumentCache
from app.security.credentials import CredentialStore
from app.settings import Settings

HEARTBEAT_SECONDS = 10.0


class ExperienceJobRunner:
    def __init__(
        self,
        runtime: DatabaseRuntime,
        parsed_cache: ParsedDocumentCache,
        settings: Settings,
        *,
        summarizer_factory=None,
    ) -> None:
        self.runtime = runtime
        self.parsed_cache = parsed_cache
        self.settings = settings
        self.summarizer_factory = summarizer_factory or self._default_summarizer
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="experience-summary")
        self._active: dict[str, str] = {}
        self._submitted: set[str] = set()
        self._lock = Lock()
        self._stop = Event()
        self._heartbeat = Thread(target=self._heartbeat_loop, name="experience-heartbeat", daemon=True)

    def _default_summarizer(self):
        store = LLMConfigStore(self.settings.storage_root / "llm_config.json", CredentialStore())
        return build_experience_summarizer(store)

    def start(self) -> None:
        self._heartbeat.start()
        session = self.runtime.session()
        try:
            pending = ExperienceRepository(session).recover_expired_jobs()
        finally:
            session.close()
        for job_id in pending:
            self.enqueue(job_id)

    def enqueue(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._submitted or job_id in self._active or self._stop.is_set():
                return
            self._submitted.add(job_id)
        self.executor.submit(self._execute, job_id)

    def _execute(self, job_id: str) -> None:
        token = str(uuid4())
        session = self.runtime.session()
        repository = ExperienceRepository(session)
        with self._lock:
            self._submitted.discard(job_id)
        try:
            if not repository.claim(job_id, token):
                return
            with self._lock:
                self._active[job_id] = token
            job = repository.get_job(job_id)
            if job is None:
                return
            finding = session.get(FindingORM, job.finding_row_id)
            if finding is None:
                raise ValueError("原Finding不存在")
            snapshot = resolve_evidence(repository, job, finding, self.parsed_cache, self.settings)
            repository.save_evidence_snapshot(job_id, token, snapshot)
            content = self._user_content(finding, snapshot)
            last_error: Exception | None = None
            for attempt in range(2):
                try:
                    summarizer = self.summarizer_factory()
                    summary = summarizer.summarize(content)
                    repository.complete(job_id, token, summary, summarizer.provider_name, summarizer.model_name)
                    return
                except Exception as exc:
                    last_error = exc
                    retryable = isinstance(exc, ValueError) or (
                        isinstance(exc, LLMProviderError) and exc.retryable
                    )
                    if attempt == 0 and retryable:
                        continue
                    break
            assert last_error is not None
            repository.fail(job_id, token, safe_summary_error(last_error))
        except Exception as exc:
            logging.warning("Experience summary job failed: %s", job_id, exc_info=True)
            try:
                repository.fail(job_id, token, safe_summary_error(exc))
            except Exception:
                logging.warning("Unable to persist experience failure: %s", job_id, exc_info=True)
        finally:
            with self._lock:
                self._active.pop(job_id, None)
            session.close()

    @staticmethod
    def _user_content(finding: FindingORM, snapshot: list[dict]) -> str:
        lines = [
            f"问题标题：{finding.title}",
            f"问题说明：{finding.description}",
            f"原建议：{finding.suggestion}",
            f"严重程度：{finding.severity}",
            f"来源：{finding.origin}",
            f"规则：{finding.rule_id or '无'}",
            f"专家结论（系统固定，不得改写）：{finding.review_status}",
            f"专家备注：{finding.human_note or '无'}",
        ]
        for index, item in enumerate(snapshot, 1):
            lines.append(f"证据{index} [{item['span_id']}] {item['location']}：{item['text']}")
        return "\n".join(lines)[:8000]

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            with self._lock:
                active = list(self._active.items())
            for job_id, token in active:
                session = self.runtime.session()
                try:
                    if not ExperienceRepository(session).heartbeat(job_id, token):
                        with self._lock:
                            self._active.pop(job_id, None)
                except Exception:
                    logging.warning("Experience heartbeat failed: %s", job_id, exc_info=True)
                finally:
                    session.close()

    def shutdown(self) -> None:
        self._stop.set()
        self.executor.shutdown(wait=False, cancel_futures=False)
        if self._heartbeat.is_alive():
            self._heartbeat.join(timeout=2.0)

