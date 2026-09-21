"""
app/services/ai/types.py
Provider-neutral request/response objects and errors. Every AI feature builds
an AIRequest and receives an AIResponse; only the provider adapters know how
those map to a concrete provider's wire format.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Union


# ─────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────

class AIError(Exception):
    """Base class for every error raised by the AI layer."""


class AIConfigError(AIError, ValueError):
    """The selected model cannot be used as configured (disabled, missing API
    key, incompatible with the feature/request, unknown reference, ...).
    Subclasses ValueError so callers that already guard configuration
    problems with `except ValueError` keep working. Messages are safe to
    show to an admin — they never contain a key value."""


class AIProviderError(AIError):
    """The provider was reached (or should have been) but the call failed.

    `kind` says what went wrong, so callers can react (and show a useful, safe
    message) without parsing text:
        timeout / connect_timeout   no answer in time (waiting for the reply / connecting)
        unreachable                 the connection failed (DNS, refused, dropped)
        rate_limited                HTTP 429
        auth                        HTTP 401/403 (the key was rejected)
        model_rejected              the provider does not accept the configured model id
        bad_request                 any other HTTP 4xx
        server_error                HTTP 5xx
        malformed                   the reply could not be read
        provider_error              anything else
    """

    def __init__(self, message: str, status_code: Optional[int] = None, provider: str = "",
                 kind: str = "provider_error"):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.kind = kind


class AITimeoutError(AIProviderError):
    """The provider did not answer within the request timeout."""

    def __init__(self, message: str, status_code: Optional[int] = None, provider: str = "",
                 kind: str = "timeout"):
        super().__init__(message, status_code=status_code, provider=provider, kind=kind)


# What a person may be told. Never contains a key, a URL, a header or a stack trace.
_PUBLIC_MESSAGES = {
    "timeout": "AI provider request timed out while waiting for a response.",
    "connect_timeout": "Could not connect to the AI provider in time. Please try again shortly.",
    "unreachable": "Could not reach the AI provider. Please try again shortly.",
    "rate_limited": "AI provider rate limit reached. Please try again shortly.",
    "auth": "The AI provider rejected the configured credentials. Please contact the administrator.",
    "model_rejected": "The configured model was rejected by the provider. Please contact the administrator.",
    "bad_request": "The AI provider could not process this request.",
    "server_error": "The AI provider is having problems right now. Please try again shortly.",
    "malformed": "The AI provider returned a response that could not be read.",
    "provider_error": "I'm having trouble connecting to my AI service. Please try again.",
}
_CONFIG_MESSAGE = "AI service is currently unavailable. Please contact the administrator."
_GENERIC_MESSAGE = "I encountered an error. Please try again."

_HTTP_STATUS = {"timeout": 504, "connect_timeout": 504, "unreachable": 502, "rate_limited": 429, "auth": 502,
                "model_rejected": 502, "bad_request": 502, "server_error": 502, "malformed": 502,
                "provider_error": 502}


def public_error(exc: BaseException) -> tuple:
    """(message safe to show to a student/admin, suggested HTTP status) for any exception."""
    if isinstance(exc, AIConfigError):
        return _CONFIG_MESSAGE, 503
    if isinstance(exc, AIProviderError):
        kind = exc.kind if exc.kind in _PUBLIC_MESSAGES else "provider_error"
        return _PUBLIC_MESSAGES[kind], _HTTP_STATUS[kind]
    return _GENERIC_MESSAGE, 500


# ─────────────────────────────────────────────
# Request
# ─────────────────────────────────────────────

@dataclass
class Part:
    """One piece of message content.

    kind == "text":  text
    kind == "image": data (base64) + mime_type
    kind == "file":  mime_type + either data (base64, inline) or uri (an
                     already-uploaded provider file reference)
    """
    kind: str
    text: Optional[str] = None
    data: Optional[str] = None
    mime_type: Optional[str] = None
    uri: Optional[str] = None
    name: Optional[str] = None

    @staticmethod
    def of_text(text: str) -> "Part":
        return Part(kind="text", text=text)

    @staticmethod
    def of_image(data_b64: str, mime_type: str = "image/jpeg") -> "Part":
        return Part(kind="image", data=data_b64, mime_type=mime_type)

    @staticmethod
    def of_file(mime_type: str, data_b64: Optional[str] = None, uri: Optional[str] = None,
                name: Optional[str] = None) -> "Part":
        return Part(kind="file", mime_type=mime_type, data=data_b64, uri=uri, name=name)


@dataclass
class Message:
    role: str                      # "user" | "assistant"
    parts: List[Part]

    @staticmethod
    def of(role: str, content: Union[str, List[Part]]) -> "Message":
        parts = [Part.of_text(content)] if isinstance(content, str) else list(content)
        return Message(role=role, parts=parts)


@dataclass
class AIRequest:
    """What a feature wants done, independent of any provider."""
    messages: List[Message]
    system_instruction: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    response_format: Optional[str] = None      # None | "json"
    reasoning_effort: Optional[str] = None     # None | "low": think as little as the model allows. Only sent to a model whose
                                               # offering says how (options.low_reasoning_effort); ignored by every other model
    timeout: Optional[float] = None            # seconds; None -> config.AI_REQUEST_TIMEOUT
    total_timeout: Optional[float] = None      # streaming only: ceiling for the whole reply; None -> config.AI_STREAM_TOTAL_TIMEOUT

    def required_capabilities(self) -> set:
        """Capabilities a model must have to accept this request's inputs."""
        needed = {"text"}
        for msg in self.messages:
            for part in msg.parts:
                if part.kind == "image":
                    needed.add("vision")
                elif part.kind == "file" and (part.mime_type or "").lower() == "application/pdf":
                    needed.add("pdf")
        return needed


# ─────────────────────────────────────────────
# Response
# ─────────────────────────────────────────────

@dataclass
class AIResponse:
    text: str
    finish_reason: str = "stop"            # "stop" | "length" | "content_filter" | "other"
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    model_ref: str = ""
    provider: str = ""
    model_id: str = ""
    reasoning_tokens: Optional[int] = None  # hidden "thinking" tokens, when the provider reports them
    elapsed_ms: Optional[int] = None        # our own wall-clock for the provider call
    first_token_ms: Optional[int] = None    # streaming only: time until the first visible text
    provider_timings: Optional[dict] = None  # the provider's own inference timing (seconds), when it sends any


@dataclass
class StreamChunk:
    """One incremental piece of a streamed reply. Everything but `text` is optional and usually
    only present on the last chunks."""
    text: str = ""
    finish_reason: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    provider_timings: Optional[dict] = None


# ─────────────────────────────────────────────
# Resolved model (registry -> client -> adapter)
# ─────────────────────────────────────────────

@dataclass(frozen=True)
class ResolvedModel:
    """Everything needed to call one model for one feature. Built per call by
    registry.resolve(); holds the API key in memory only — repr/compare
    exclude it and nothing in the admin API ever serializes it."""
    feature: str
    ref: str                       # registry key, e.g. "gemini/gemini-2.5-flash"
    provider: str                  # provider key, e.g. "gemini"
    provider_name: str             # display name
    protocol: str                  # adapter key, e.g. "gemini"
    model_id: str                  # the provider's own model identifier
    display_name: str
    capabilities: frozenset
    limits: dict
    api: dict                      # provider api config merged with model overrides
    options: dict                  # provider options merged with model options
    source: str                    # "admin" | "registry"
    api_key: str = field(default="", repr=False, compare=False)
