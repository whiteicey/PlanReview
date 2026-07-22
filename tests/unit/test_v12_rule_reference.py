from __future__ import annotations

from app.domain.enums import BlockType, RuleStatus
from app.domain.schemas import SourceSpan
from app.rules.operators import OperatorContext, get_operator


def test_reference_001_missing_target_is_fail_and_ambiguous_target_unknown():
    spans = [
        SourceSpan(
            span_id="h1",
            document_id="doc",
            section_path=["1 General"],
            block_type=BlockType.HEADING,
            text="1 General",
            text_hash="h",
        )
    ]
    context = OperatorContext(facts=[], spans=spans)
    operator = get_operator("reference_v12")
    assert operator(context, {"references": [{"target": "1 General"}]}).status is RuleStatus.PASS
    assert operator(context, {"references": [{"target": "9 Missing"}]}).status is RuleStatus.FAIL
    assert operator(context, {"references": [{"target": ""}]}).status is RuleStatus.UNKNOWN
