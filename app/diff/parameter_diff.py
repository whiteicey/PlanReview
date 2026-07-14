"""Scope-safe comparison of normalized extracted parameter facts."""

from __future__ import annotations

from dataclasses import dataclass
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
    pairing: PairingAssessment | None = None,
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
    if pairing is not None:
        assert_pairing_confirmed(pairing)

    old_by_base = _group_by_base_key(old)
    new_by_base = _group_by_base_key(new)
    differences: list[ParameterDifference] = []
    remaining_old: list[ParameterFact] = []
    remaining_new: list[ParameterFact] = []

    for base_key in sorted(old_by_base.keys() & new_by_base.keys(), key=_base_sort_key):
        differences.extend(_diff_base_group(old_by_base[base_key], new_by_base[base_key]))

    for base_key in old_by_base.keys() - new_by_base.keys():
        remaining_old.extend(old_by_base[base_key])
    for base_key in new_by_base.keys() - old_by_base.keys():
        remaining_new.extend(new_by_base[base_key])

    unknown_scope_pairs, remaining_old, remaining_new = _pair_missing_subjects(
        remaining_old, remaining_new
    )
    differences.extend(unknown_scope_pairs)
    differences.extend(
        ParameterDifference(fact.comparison_key(), fact, None, DiffKind.REMOVED)
        for fact in sorted(remaining_old, key=_fact_sort_key)
    )
    differences.extend(
        ParameterDifference(fact.comparison_key(), None, fact, DiffKind.ADDED)
        for fact in sorted(remaining_new, key=_fact_sort_key)
    )
    return differences


def _pair_missing_subjects(
    old_facts: list[ParameterFact], new_facts: list[ParameterFact]
) -> tuple[list[ParameterDifference], list[ParameterFact], list[ParameterFact]]:
    """Pair same-name leftovers only when a subject is explicitly missing."""
    old_by_name = _group_by_name(old_facts)
    new_by_name = _group_by_name(new_facts)
    differences: list[ParameterDifference] = []
    remaining_old: list[ParameterFact] = []
    remaining_new: list[ParameterFact] = []

    for name in sorted(old_by_name.keys() | new_by_name.keys()):
        unmatched_old = sorted(old_by_name.get(name, []), key=_fact_sort_key)
        unmatched_new = sorted(new_by_name.get(name, []), key=_fact_sort_key)
        while unmatched_old and unmatched_new:
            old_index = next((i for i, fact in enumerate(unmatched_old) if fact.subject is None), None)
            new_index = next((i for i, fact in enumerate(unmatched_new) if fact.subject is None), None)
            if old_index is None and new_index is None:
                break
            old_fact = unmatched_old.pop(0 if old_index is None else old_index)
            new_fact = unmatched_new.pop(0 if new_index is None else new_index)
            differences.append(
                ParameterDifference(
                    _paired_key(old_fact, new_fact), old_fact, new_fact, DiffKind.UNKNOWN_SCOPE
                )
            )
        remaining_old.extend(unmatched_old)
        remaining_new.extend(unmatched_new)
    return differences, remaining_old, remaining_new


def _diff_base_group(
    old_facts: list[ParameterFact], new_facts: list[ParameterFact]
) -> list[ParameterDifference]:
    old_by_full = _group_by_comparison_key(old_facts)
    new_by_full = _group_by_comparison_key(new_facts)
    differences: list[ParameterDifference] = []
    unmatched_old: list[ParameterFact] = []
    unmatched_new: list[ParameterFact] = []

    for key in sorted(old_by_full.keys() | new_by_full.keys(), key=_comparison_sort_key):
        old_matches = old_by_full.get(key, [])
        new_matches = new_by_full.get(key, [])
        common = min(len(old_matches), len(new_matches))
        for index in range(common):
            old_fact, new_fact = old_matches[index], new_matches[index]
            differences.append(
                ParameterDifference(key, old_fact, new_fact, _matched_kind(old_fact, new_fact))
            )
        unmatched_old.extend(old_matches[common:])
        unmatched_new.extend(new_matches[common:])

    unmatched_old.sort(key=_fact_sort_key)
    unmatched_new.sort(key=_fact_sort_key)
    common = min(len(unmatched_old), len(unmatched_new))
    for index in range(common):
        old_fact, new_fact = unmatched_old[index], unmatched_new[index]
        differences.append(
            ParameterDifference(
                _paired_key(old_fact, new_fact), old_fact, new_fact, DiffKind.UNKNOWN_SCOPE
            )
        )

    differences.extend(
        ParameterDifference(fact.comparison_key(), fact, None, DiffKind.REMOVED)
        for fact in unmatched_old[common:]
    )
    differences.extend(
        ParameterDifference(fact.comparison_key(), None, fact, DiffKind.ADDED)
        for fact in unmatched_new[common:]
    )
    return differences


def _matched_kind(old_fact: ParameterFact, new_fact: ParameterFact) -> DiffKind:
    if not _has_complete_comparison_scope(old_fact) or not _has_complete_comparison_scope(new_fact):
        return DiffKind.UNKNOWN_SCOPE
    return (
        DiffKind.UNCHANGED
        if old_fact.normalized_value == new_fact.normalized_value
        else DiffKind.CHANGED
    )


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
