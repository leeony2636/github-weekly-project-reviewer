from typing import Any


class ProviderError(RuntimeError):
    def __init__(
        self,
        provider: str,
        operation: str,
        original_error: Exception,
    ) -> None:
        self.provider = provider
        self.operation = operation
        self.error_type = type(original_error).__name__

        status_code: Any = getattr(original_error, "status_code", None)
        self.status_code = (
            status_code if isinstance(status_code, int) else None
        )

        message = (
            f"{provider} {operation} 실패"
            f" error_type={self.error_type}"
        )

        if self.status_code is not None:
            message += f" status_code={self.status_code}"

        super().__init__(message)