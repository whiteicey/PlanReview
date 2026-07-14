import pytest

from app.domain.exceptions import (
    ReviewError, UnsupportedFileTypeError, PathTraversalError,
    UnknownOperatorError,
)


def test_subclasses_are_review_errors():
    for exc in (UnsupportedFileTypeError, PathTraversalError, UnknownOperatorError):
        assert issubclass(exc, ReviewError)


def test_message_preserved():
    with pytest.raises(UnsupportedFileTypeError, match="仅处理文本型 DOCX"):
        raise UnsupportedFileTypeError("暂不支持，仅处理文本型 DOCX")
