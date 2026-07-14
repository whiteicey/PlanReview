from __future__ import annotations


class ReviewError(Exception):
    """本项目所有领域异常的基类。"""


class UnsupportedFileTypeError(ReviewError):
    pass


class FileTooLargeError(ReviewError):
    pass


class PathTraversalError(ReviewError):
    pass


class RuleLoadError(ReviewError):
    pass


class UnknownOperatorError(ReviewError):
    pass


class ParseError(ReviewError):
    pass
