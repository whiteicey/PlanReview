from __future__ import annotations

from enum import Enum


class RuleStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ReviewStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    MODIFIED = "modified"
    RESOLVED = "resolved"


class LLMStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    COMPLETED = "COMPLETED"
    COMPLETED_PARTIAL = "COMPLETED_PARTIAL"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    VALIDATION_FAILED = "VALIDATION_FAILED"


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FindingCategory(str, Enum):
    COMPLETENESS = "completeness"
    CONSISTENCY = "consistency"
    AGGREGATION = "aggregation"
    CROSS_DOMAIN = "cross_domain"
    CAPACITY = "capacity"
    VERSION_CHANGE = "version_change"
    TERMINOLOGY = "terminology"
    EVIDENCE = "evidence"
    TRACEABILITY = "traceability"
    UNKNOWN_SCOPE = "unknown_scope"
    OTHER = "other"

    @classmethod
    def _missing_(cls, value):
        return {"version-change": cls.VERSION_CHANGE, "unknown": cls.UNKNOWN_SCOPE}.get(value)


class Origin(str, Enum):
    RULE = "rule"
    LLM = "llm"
    HYBRID = "hybrid"
    HUMAN = "human"


class OnMissing(str, Enum):
    UNKNOWN = "unknown"
    FAIL = "fail"
    BLOCK = "block"


class PipelineStage(str, Enum):
    """Legacy stages plus the explicit review lifecycle stages."""

    # Legacy lifecycle members remain source-compatible for existing callers.
    CREATED = "CREATED"
    VALIDATING_FILES = "VALIDATING_FILES"
    PAIRING_FILES = "PAIRING_FILES"
    PARSING = "PARSING"
    BUILDING_SPANS = "BUILDING_SPANS"
    EXTRACTING_PARAMETERS = "EXTRACTING_PARAMETERS"
    NORMALIZING_FACTS = "NORMALIZING_FACTS"
    RUNNING_RULES = "RUNNING_RULES"
    RETRIEVING_KNOWLEDGE = "RETRIEVING_KNOWLEDGE"
    CALLING_MODEL = "CALLING_MODEL"
    VALIDATING_MODEL_OUTPUT = "VALIDATING_MODEL_OUTPUT"
    MERGING_FINDINGS = "MERGING_FINDINGS"
    WAITING_HUMAN_REVIEW = "WAITING_HUMAN_REVIEW"
    COMPLETED = "COMPLETED"

    # Current review run lifecycle.
    UPLOADED = "UPLOADED"
    PARSED = "PARSED"
    EXTRACTED = "EXTRACTED"
    NORMALIZED = "NORMALIZED"
    RULE_CHECKED = "RULE_CHECKED"
    LLM_REVIEWED = "LLM_REVIEWED"
    RECONCILED = "RECONCILED"
    READY_FOR_HUMAN_REVIEW = "READY_FOR_HUMAN_REVIEW"
    FAILED = "FAILED"


class DiffKind(str, Enum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"
    UNCHANGED = "unchanged"
    UNKNOWN_SCOPE = "unknown_scope"


class BlockType(str, Enum):
    PARAGRAPH = "paragraph"
    TABLE_CELL = "table_cell"
    HEADING = "heading"


class ExtractionMethod(str, Enum):
    REGEX = "regex"
    TABLE = "table"
