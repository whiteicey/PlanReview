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


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


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
