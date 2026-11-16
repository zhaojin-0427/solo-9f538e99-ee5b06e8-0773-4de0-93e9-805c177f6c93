"""Domain errors mapped to HTTP responses in main.py."""
from __future__ import annotations


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


class BadPayload(ApiError):
    def __init__(self, message: str, code: str = "bad_payload"):
        super().__init__(422, code, message)


class NotFound(ApiError):
    def __init__(self, message: str):
        super().__init__(404, "not_found", message)


class StaleRevision(ApiError):
    def __init__(self, message: str):
        super().__init__(412, "stale_revision", message)


class LimitExceeded(ApiError):
    def __init__(self, message: str):
        super().__init__(422, "limit_exceeded", message)
