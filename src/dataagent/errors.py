from __future__ import annotations


class DataAgentError(Exception):
    """Base domain error carrying an HTTP-safe status code."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NotFoundError(DataAgentError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


class ConflictError(DataAgentError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409)


class ConfigurationError(DataAgentError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=503)


class ModelResponseError(DataAgentError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=502)


class ModelOutputBudgetError(ModelResponseError):
    """Generation was truncated, not malformed JSON to be syntactically repaired."""


class ModelEmptyResponseError(ModelResponseError):
    pass


class ModelDeadlineError(ModelResponseError):
    pass


class UnsafeSqlError(DataAgentError):
    pass


class SandboxError(DataAgentError):
    pass
