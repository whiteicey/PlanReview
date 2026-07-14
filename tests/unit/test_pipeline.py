from __future__ import annotations

import pytest

from app.domain.enums import PipelineStage
from app.pipeline import StageRunner


@pytest.mark.parametrize(
    "failed_stage",
    [
        PipelineStage.PARSED,
        PipelineStage.LLM_REVIEWED,
        PipelineStage.RULE_CHECKED,
    ],
)
def test_stage_failure_records_failure_and_stops_later_stages(
    failed_stage: PipelineStage,
) -> None:
    called: list[PipelineStage] = []

    def stage_callback(stage: PipelineStage):
        def callback() -> str:
            called.append(stage)
            if stage is failed_stage:
                raise RuntimeError(
                    "request failed at C:\\secret\\case.docx "
                    "api_key=top-secret body={\"password\":\"hunter2\"}"
                )
            return stage.value

        return callback

    stages = [
        (PipelineStage.PARSED, stage_callback(PipelineStage.PARSED)),
        (PipelineStage.LLM_REVIEWED, stage_callback(PipelineStage.LLM_REVIEWED)),
        (PipelineStage.RULE_CHECKED, stage_callback(PipelineStage.RULE_CHECKED)),
        (
            PipelineStage.READY_FOR_HUMAN_REVIEW,
            stage_callback(PipelineStage.READY_FOR_HUMAN_REVIEW),
        ),
    ]
    result = StageRunner().run(stages)

    assert called == [
        PipelineStage.PARSED,
        PipelineStage.LLM_REVIEWED,
        PipelineStage.RULE_CHECKED,
    ][: (
        1
        if failed_stage is PipelineStage.PARSED
        else 2 if failed_stage is PipelineStage.LLM_REVIEWED else 3
    )]
    assert called[-1] is failed_stage
    assert result.final_status == "FAILED"
    assert result.stage_records[-1].stage is PipelineStage.FAILED
    failed_record = result.stage_records[-2]
    assert failed_record.stage is failed_stage
    assert failed_record.status == "failed"
    assert failed_record.exception_type == "RuntimeError"
    assert result.stage_records[-1].exception_type == "RuntimeError"
    error_text = " ".join(record.error or "" for record in result.stage_records)
    assert "secret" not in error_text
    assert "top-secret" not in error_text
    assert "hunter2" not in error_text
    assert "case.docx" not in error_text
    assert "body=" not in error_text


def test_successful_stage_runner_reaches_human_review_without_failure_record() -> None:
    called: list[str] = []
    result = StageRunner().run(
        [
            (PipelineStage.PARSED, lambda: called.append("parser")),
            (PipelineStage.RULE_CHECKED, lambda: called.append("rules")),
        ]
    )

    assert called == ["parser", "rules"]
    assert result.final_status == "READY_FOR_HUMAN_REVIEW"
    assert all(record.status == "completed" for record in result.stage_records)
    assert not any(record.stage is PipelineStage.FAILED for record in result.stage_records)
