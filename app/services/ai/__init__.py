"""
app/services/ai
Provider-agnostic AI layer. Features import from here only:

    from app.services.ai import generate, AIRequest, Message, Part

    resp = generate("assistant_chat", AIRequest(messages=[Message.of("user", "hi")]))

Layers: registry (config/ai_models.json + Admin overrides -> which model) ->
client (HTTP) -> adapters (protocol-specific payload/response). See
config/AI_MODELS.md.
"""

from app.services.ai.types import (  # noqa: F401
    AIConfigError, AIError, AIProviderError, AIRequest, AIResponse, AITimeoutError,
    Message, Part, ResolvedModel, StreamChunk, public_error,
)
from app.services.ai.client import generate, log_problem, resolve, stream, upload_file  # noqa: F401
from app.services.ai.registry import public_identity  # noqa: F401
