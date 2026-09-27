"""Domain error hierarchy and CLI exit codes (interfaces.md §4, RNF-09)."""

from typing import ClassVar


class StemSeparatorError(Exception):
    """Base error. `message` and `hint` are user-facing Spanish text."""

    exit_code: ClassVar[int]  # no value here: every subclass must set its own

    def __init__(self, message: str, hint: str) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class InputNotFoundError(StemSeparatorError):
    exit_code = 3


class UnsupportedFormatError(StemSeparatorError):
    exit_code = 3


class NoAudioStreamError(StemSeparatorError):
    exit_code = 3


class MediaReadError(StemSeparatorError):
    exit_code = 3


class UnknownModelError(StemSeparatorError):
    exit_code = 3


class UnsupportedStemError(StemSeparatorError):
    exit_code = 3


class OutputConflictError(StemSeparatorError):
    exit_code = 3


class OutputNotWritableError(StemSeparatorError):
    exit_code = 3


class FileTooLargeError(StemSeparatorError):
    """Raised only by the web backend (i8-api.md); the CLI has no size limit."""

    exit_code = 3


class FFmpegNotFoundError(StemSeparatorError):
    exit_code = 4


class InsufficientDiskSpaceError(StemSeparatorError):
    exit_code = 4


class EngineError(StemSeparatorError):
    exit_code = 5


class ExportError(StemSeparatorError):
    exit_code = 5


class OutputRestoreError(StemSeparatorError):
    """A `.bak` could not be restored after a failure (ADR-004). Takes priority over 5 and 130."""

    exit_code = 6
