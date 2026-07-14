from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.domain.schemas import ParameterFact


@dataclass(frozen=True)
class TerminologyMap:
    """Explicit canonical-name and alias mappings.

    Matching is intentionally exact (apart from surrounding whitespace).  The
    mapping is not used for fuzzy or similarity-based business-field changes.
    """

    canonical_to_aliases: Mapping[str, frozenset[str]]

    def __post_init__(self) -> None:
        """Normalize and freeze mappings supplied through any constructor."""
        normalized = {
            str(canonical).strip(): frozenset(
                {
                    str(canonical).strip(),
                    *(str(alias).strip() for alias in aliases),
                }
            )
            for canonical, aliases in self.canonical_to_aliases.items()
        }
        object.__setattr__(self, "canonical_to_aliases", MappingProxyType(normalized))

    @classmethod
    def from_mapping(cls, mapping: dict[str, list[str]]) -> TerminologyMap:
        """Build a terminology map with trimmed canonical names and aliases."""
        return cls(mapping)

    def canonicalize(self, raw_name: str) -> str:
        """Return a mapped canonical name or the original unknown name.

        Canonical names win over aliases, including when a string appears as
        both a canonical name and an alias in the supplied mapping. Alias
        matching is exact after stripping surrounding whitespace only; an
        unknown term is returned with its original whitespace preserved.
        """
        if raw_name in self.canonical_to_aliases:
            return raw_name

        # Canonical names have priority over exact aliases.
        for canonical, aliases in self.canonical_to_aliases.items():
            if raw_name in aliases and raw_name != canonical:
                return canonical

        value = raw_name.strip()
        if value in self.canonical_to_aliases:
            return value
        for canonical, aliases in self.canonical_to_aliases.items():
            if value in aliases:
                return canonical
        return raw_name


def normalize_facts(
    facts: list[ParameterFact], terminology: TerminologyMap
) -> list[ParameterFact]:
    """Return copied facts with only ``canonical_name`` normalized.

    Source/raw names and every other fact field remain unchanged; the input
    fact objects are never mutated.
    """
    return [
        fact.model_copy(
            update={"canonical_name": terminology.canonicalize(fact.raw_name)}
        )
        for fact in facts
    ]
