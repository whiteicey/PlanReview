from app.domain.enums import BlockType, RuleStatus, Severity
from app.domain.schemas import ParameterFact, SourceSpan, RuleDefinition
from app.rules.engine import RuleEngine


def test_compatibility_operator_unknowns_on_scope_mismatch():
    def fact(fid, value, *, time="建设期", stat="规划部署", subject="气田_A"):
        return ParameterFact(fact_id=fid, canonical_name="开发井总数", raw_name="开发井总数", raw_value=str(value), normalized_value=value, raw_unit="口", canonical_unit="口", subject=subject, time_scope=time, statistical_scope=stat, source_document="D", source_span_id=fid, extraction_method="table")
    rule = RuleDefinition(rule_id="C", version="1", name="compat", category="consistency", severity=Severity.MEDIUM, operator="legacy_fact_consistency", on_missing="unknown", params={"left":"开发井总数","right":"生产井数"})
    facts = [fact("a", 36), fact("b", 30, time="达产期"), fact("c", 36, stat="日峰值"), fact("d", 30, subject="单区")]
    spans = [SourceSpan(span_id=x, document_id="D", block_type=BlockType.TABLE_CELL, text=x, text_hash=x) for x in "abcd"]
    result = RuleEngine().evaluate([rule], facts, spans)[0]
    assert result.status is RuleStatus.UNKNOWN
