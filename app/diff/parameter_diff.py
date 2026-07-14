"""Scope-safe comparison of normalized extracted parameter facts."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import TypeAlias

from app.diff.pairing import PairingAssessment, assert_pairing_confirmed
from app.domain.enums import DiffKind
from app.domain.schemas import ParameterFact


ComparisonKey: TypeAlias = tuple[str, str | None, str | None, str | None, str | None]
BaseKey: TypeAlias = tuple[str, str | None]


@dataclass(frozen=True)
class ParameterDifference:
    key: ComparisonKey
    old: ParameterFact | None
    new: ParameterFact | None
    kind: DiffKind


def diff_parameters(
    old: list[ParameterFact],
    new: list[ParameterFact],
    *,
    pairing: PairingAssessment,
) -> list[ParameterDifference]:
    """Compare normalized facts without treating changed scope as add/remove.

    Facts are first paired by the stable `(canonical_name, subject)` identity.
    Exact five-dimension matches are compared by normalized value. Remaining
    facts in a common group are paired as `UNKNOWN_SCOPE`, preserving their
    actual five-dimensional keys rather than misreporting a scope change as an
    addition and a removal. A missing subject is also an unknown scope, so an
    unmatched fact with subject ``None`` is conservatively paired by name with
    a fact having a known subject instead of becoming added/removed.
    """
    assert_pairing_confirmed(pairing)

    old_by_name = _group_by_name(old)
    new_by_name = _group_by_name(new)
    differences: list[ParameterDifference] = []

    for name in sorted(old_by_name.keys() | new_by_name.keys()):
        old_facts = old_by_name.get(name, [])
        new_facts = new_by_name.get(name, [])
        if not old_facts:
            differences.extend(
                ParameterDifference(fact.comparison_key(), None, fact, DiffKind.ADDED)
                for fact in sorted(new_facts, key=_fact_sort_key)
            )
        elif not new_facts:
            differences.extend(
                ParameterDifference(fact.comparison_key(), fact, None, DiffKind.REMOVED)
                for fact in sorted(old_facts, key=_fact_sort_key)
            )
        else:
            differences.extend(_diff_name_group(old_facts, new_facts))

    return differences


def _diff_name_group(
    old_facts: list[ParameterFact], new_facts: list[ParameterFact]
) -> list[ParameterDifference]:
    """Compare one canonical name, refusing ambiguous cardinality or duplicates."""
    if len(old_facts) != len(new_facts):
        return [_ambiguous_difference(old_facts, new_facts)]
    if _has_duplicate_full_key(old_facts) or _has_duplicate_full_key(new_facts):
        return [_ambiguous_difference(old_facts, new_facts)]

    old_by_base = _group_by_base_key(old_facts)
    new_by_base = _group_by_base_key(new_facts)
    differences: list[ParameterDifference] = []
    common_bases = old_by_base.keys() & new_by_base.keys()
    for base_key in sorted(common_bases, key=_base_sort_key):
        differences.extend(_diff_base_group(old_by_base[base_key], new_by_base[base_key]))

    old_left = [fact for key, facts in old_by_base.items() if key not in common_bases for fact in facts]
    new_left = [fact for key, facts in new_by_base.items() if key not in common_bases for fact in facts]
    if old_left or new_left:
        if len(old_left) == len(new_left) == 1:
            differences.append(
                ParameterDifference(
                    _paired_key(old_left[0], new_left[0]), old_left[0], new_left[0], DiffKind.UNKNOWN_SCOPE
                )
            )
        else:
            differences.append(_ambiguous_difference(old_left, new_left))
    return differences


def _ambiguous_difference(
    old_facts: list[ParameterFact], new_facts: list[ParameterFact]
) -> ParameterDifference:
    facts = sorted(old_facts + new_facts, key=_fact_sort_key)
    name = facts[0].canonical_name if facts else ""
    return ParameterDifference((name, None, None, None, None), None, None, DiffKind.UNKNOWN_SCOPE)


def _has_duplicate_full_key(facts: list[ParameterFact]) -> bool:
    keys = [fact.comparison_key() for fact in facts]
    return len(keys) != len(set(keys))


def _diff_base_group(
    old_facts: list[ParameterFact], new_facts: list[ParameterFact]
) -> list[ParameterDifference]:
    if len(old_facts) != len(new_facts) or _has_duplicate_full_key(old_facts) or _has_duplicate_full_key(new_facts):
        return [_ambiguous_difference(old_facts, new_facts)]
    old_by_full = _group_by_comparison_key(old_facts)
    new_by_full = _group_by_comparison_key(new_facts)
    differences: list[ParameterDifference] = []

    for key in sorted(old_by_full.keys() & new_by_full.keys(), key=_comparison_sort_key):
        old_fact, new_fact = old_by_full[key][0], new_by_full[key][0]
        differences.append(
            ParameterDifference(key, old_fact, new_fact, _matched_kind(old_fact, new_fact))
        )

    unmatched_old = [fact for key, facts in old_by_full.items() if key not in new_by_full for fact in facts]
    unmatched_new = [fact for key, facts in new_by_full.items() if key not in old_by_full for fact in facts]
    if not unmatched_old and not unmatched_new:
        return differences
    if len(unmatched_old) == len(unmatched_new) == 1:
        differences.append(
            ParameterDifference(
                _paired_key(unmatched_old[0], unmatched_new[0]),
                unmatched_old[0],
                unmatched_new[0],
                DiffKind.UNKNOWN_SCOPE,
            )
        )
    else:
        differences.append(_ambiguous_difference(unmatched_old, unmatched_new))
    return differences


def _matched_kind(old_fact: ParameterFact, new_fact: ParameterFact) -> DiffKind:
    if not _has_complete_comparison_scope(old_fact) or not _has_complete_comparison_scope(new_fact):
        return DiffKind.UNKNOWN_SCOPE
    if not _has_compatible_normalized_values(old_fact, new_fact):
        return DiffKind.UNKNOWN_SCOPE
    return (
        DiffKind.UNCHANGED
        if old_fact.normalized_value == new_fact.normalized_value
        else DiffKind.CHANGED
    )


def _has_compatible_normalized_values(
    old_fact: ParameterFact, new_fact: ParameterFact
) -> bool:
    old_value, new_value = old_fact.normalized_value, new_fact.normalized_value
    if old_value is None or new_value is None:
        return False
    if not math.isfinite(old_value) or not math.isfinite(new_value):
        return False
    if old_fact.canonical_unit != new_fact.canonical_unit:
        return False
    return True


def _has_complete_comparison_scope(fact: ParameterFact) -> bool:
    return (
        fact.subject is not None
        and fact.time_scope is not None
        and fact.statistical_scope is not None
        and fact.condition is not None
    )


def _group_by_base_key(facts: list[ParameterFact]) -> dict[BaseKey, list[ParameterFact]]:
    result: dict[BaseKey, list[ParameterFact]] = {}
    for fact in facts:
        result.setdefault((fact.canonical_name, fact.subject), []).append(fact)
    return result


def _group_by_name(facts: list[ParameterFact]) -> dict[str, list[ParameterFact]]:
    result: dict[str, list[ParameterFact]] = {}
    for fact in facts:
        result.setdefault(fact.canonical_name, []).append(fact)
    return result


def _group_by_comparison_key(
    facts: list[ParameterFact],
) -> dict[ComparisonKey, list[ParameterFact]]:
    result: dict[ComparisonKey, list[ParameterFact]] = {}
    for fact in facts:
        result.setdefault(fact.comparison_key(), []).append(fact)
    for matches in result.values():
        matches.sort(key=_fact_sort_key)
    return result


def _paired_key(old_fact: ParameterFact, new_fact: ParameterFact) -> ComparisonKey:
    """Use the old key as the stable key while retaining both facts on the diff."""
    return old_fact.comparison_key()


def _fact_sort_key(fact: ParameterFact) -> tuple[str, str, str, str, str, str]:
    return (*_comparison_sort_key(fact.comparison_key()), fact.fact_id)


def _base_sort_key(key: BaseKey) -> tuple[str, str]:
    return (key[0], key[1] or "")


def _comparison_sort_key(key: ComparisonKey) -> tuple[str, str, str, str, str]:
    return tuple(value or "" for value in key)
