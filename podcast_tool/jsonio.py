import json
import sys


EXIT_OK = 0
EXIT_INVALID_ARGUMENT = 2
EXIT_NOT_FOUND = 3
EXIT_CONFIG = 4
EXIT_TASK_FAILED = 5
EXIT_PROVIDER_TEMPORARY = 6
EXIT_RUNTIME = 10


class ToolError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        exit_code: int = EXIT_RUNTIME,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.retryable = retryable

    def to_error(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


def write_json(payload: dict, exit_code: int = EXIT_OK) -> int:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return exit_code


def write_error(error: ToolError, extra: dict | None = None) -> int:
    payload = {"ok": False, "error": error.to_error()}
    if extra:
        payload.update(extra)
    elif "job" not in payload and "jobs" not in payload:
        payload["job"] = None
    return write_json(payload, error.exit_code)


def unexpected_error(exc: Exception) -> int:
    error = ToolError(
        code="RUNTIME_ERROR",
        message=str(exc),
        exit_code=EXIT_RUNTIME,
        retryable=False,
    )
    print(f"podcast_tool unexpected error: {type(exc).__name__}: {exc}", file=sys.stderr)
    return write_error(error)
