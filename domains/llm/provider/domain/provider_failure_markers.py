"""Provider failure classification markers.

These string tuples are matched (substring) against the textual representation of
provider errors to classify them into retry-policy failure categories. They are
referenced by
``llm.provider.infrastructure.foundation.chat_tranport.failure_classification``.
"""

# Quota exhausted / billing exhausted.
QUOTA_ERROR_MARKERS: tuple[str, ...] = (
    "quota",
    "exceeded your current quota",
    "insufficient_quota",
    "billing",
    "account balance",
    "exceeded the monthly",
    "has been exhausted",
)

# No permission / no access to the requested model.
MODEL_PERMISSION_ERROR_MARKERS: tuple[str, ...] = (
    "does not exist",
    "model not found",
    "does not have access",
    "do not have access",
    "permission denied",
    "access to model",
    "not authorized to access",
    "forbidden",
)

# Capability not supported by the provider/model.
PROVIDER_FAILURE_CAPABILITY_ERROR_MARKERS: tuple[str, ...] = (
    "does not support",
    "unsupported",
    "not supported",
    "maximum context length",
    "context length",
    "token limit",
    "function calling is not",
)

# Rate limiting.
PROVIDER_FAILURE_RATE_LIMIT_ERROR_MARKERS: tuple[str, ...] = (
    "rate limit",
    "too many requests",
    "ratelimit",
    "RateLimitError",
    "429",
    "requests per minute",
    "requests per day",
)

# Service unavailable / overload / transient server errors.
PROVIDER_FAILURE_SERVICE_UNAVAILABLE_ERROR_MARKERS: tuple[str, ...] = (
    "service unavailable",
    "unavailable",
    "overloaded",
    "internal server error",
    "503",
    "502",
    "bad gateway",
    "server error",
)

# Generic transient / network errors worth retrying.
PROVIDER_TRANSIENT_ERROR_MARKERS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "connection reset",
    "connectionerror",
    "connection aborted",
    "connection refused",
    "socket",
    "temporary failure",
    "econnreset",
    "broken pipe",
    "network",
)
