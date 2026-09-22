"""rinth errors.

Everything derives from RinthError; cli.py catches those and prints them as a
single readable line instead of a traceback.
"""


class RinthError(Exception):
    """Any expected failure. The message is shown to the user verbatim."""

    def __init__(self, message, hint=None):
        super().__init__(message)
        self.hint = hint


class ApiError(RinthError):
    """The API answered with something we cannot use."""


class NotFound(ApiError):
    """404: the project or version does not exist."""


class Gone(ApiError):
    """410: the project existed but was withdrawn."""


class RateLimited(ApiError):
    """429 after exhausting retries."""


class ManifestError(RinthError):
    """rinth.toml is missing, unreadable, or has invalid fields."""


class ResolveError(RinthError):
    """No version satisfies the given constraints."""


class DownloadError(RinthError):
    """Network failure or hash mismatch."""
