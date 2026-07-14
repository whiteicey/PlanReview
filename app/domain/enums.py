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
