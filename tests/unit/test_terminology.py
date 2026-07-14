from app.domain.enums import ExtractionMethod
from app.domain.schemas import ParameterFact
from app.extraction.terminology import TerminologyMap, normalize_facts


def fact(name: str) -> ParameterFact:
    return ParameterFact(
        fact_id="f1",
        canonical_name=name,
        raw_name=name,
        raw_value="36",
        normalized_value=36,
        raw_unit="口",
        canonical_unit="well",
        subject="全区",
        time_scope="全生命周期",
        statistical_scope="累计",
        condition="基准方案",
        source_document="D1",
        source_version="V1",
        source_span_id="s1",
        extraction_method=ExtractionMethod.TABLE,
        confidence=0.8,
    )


def test_alias_maps_to_canonical_name():
    terms = TerminologyMap.from_mapping({"开发井总数": ["钻井总数", "部署井数"]})
    assert terms.canonicalize("部署井数") == "开发井总数"
    assert terms.canonicalize(" 开发井总数 ") == "开发井总数"


def test_unknown_term_is_not_silently_changed():
    terms = TerminologyMap.from_mapping({"开发井总数": ["部署井数"]})
    assert terms.canonicalize("未知井数") == "未知井数"
    assert terms.canonicalize("  未知井数  ") == "  未知井数  "


def test_whitespace_wrapped_alias_maps_without_fuzzy_matching():
    terms = TerminologyMap.from_mapping({"开发井总数": ["部署井数"]})
    assert terms.canonicalize("  部署井数  ") == "开发井总数"
    assert terms.canonicalize("开发井总数A") == "开发井总数A"


def test_normalize_facts_preserves_raw_name_and_other_fields():
    original = fact("部署井数")
    normalized = normalize_facts(
        [original], TerminologyMap.from_mapping({"开发井总数": ["部署井数"]})
    )

    assert normalized[0].canonical_name == "开发井总数"
    assert normalized[0].raw_name == "部署井数"
    assert normalized[0] is not original
    assert normalized[0].model_dump(exclude={"canonical_name"}) == original.model_dump(
        exclude={"canonical_name"}
    )
    assert original.canonical_name == "部署井数"


def test_canonical_match_takes_precedence_over_aliases():
    terms = TerminologyMap.from_mapping(
        {"开发井总数": ["部署井数"], "部署井数": ["井数"]}
    )
    assert terms.canonicalize("部署井数") == "部署井数"
    assert terms.canonicalize("井数") == "部署井数"


def test_terminology_mapping_and_alias_sets_reject_mutation():
    terms = TerminologyMap.from_mapping({"开发井总数": ["部署井数"]})

    try:
        terms.canonical_to_aliases["新术语"] = frozenset({"新术语"})
    except TypeError:
        pass
    else:
        raise AssertionError("canonical mapping must reject mutation")

    assert isinstance(terms.canonical_to_aliases["开发井总数"], frozenset)
    try:
        terms.canonical_to_aliases["开发井总数"].add("其他名称")
    except AttributeError:
        pass
    else:
        raise AssertionError("alias set must reject mutation")
