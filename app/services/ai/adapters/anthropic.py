"""
app/services/ai/adapters/anthropic.py
Anthropic Messages API wire format.

Config (provider `options` or model `options`):
  anthropic_version   value for the anthropic-version header (default 2023-06-01)
  default_max_tokens  max_tokens is REQUIRED by this API; used when a request
                      doesn't specify one (default 4096)
  omit_params         params this model rejects
  extra_body          dict merged verbatim into the request body

top_p is dropped whenever temperature is also set — recent Claude models
reject requests that specify both.
"""

from app.services.ai.adapters.base import ProviderAdapter, register_adapter
from app.services.ai.types import (
    AIProviderError, AIRequest, AIResponse, ResolvedModel,
)

_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "refusal": "content_filter",
}


@register_adapter
class AnthropicAdapter(ProviderAdapter):
    protocol = "anthropic"
    label = "Anthropic Messages"

    def endpoint(self, model: ResolvedModel) -> str:
        return self.joined_url(model.api.get("base_url", ""), model.api.get("endpoint") or "/messages")

    def extra_headers(self, model: ResolvedModel) -> dict:
        return {"anthropic-version": str(model.options.get("anthropic_version") or "2023-06-01")}

    @staticmethod
    def _blocks(parts):
        out = []
        for p in parts:
            if p.kind == "text":
                out.append({"type": "text", "text": p.text})
            elif p.kind == "image":
                out.append({"type": "image",
                            "source": {"type": "base64", "media_type": p.mime_type, "data": p.data}})
            elif p.kind == "file":
                if not p.data:
                    raise AIProviderError("This protocol cannot reference an uploaded file by URI.")
                out.append({"type": "document",
                            "source": {"type": "base64", "media_type": p.mime_type, "data": p.data}})
        return out

    def build_payload(self, model: ResolvedModel, request: AIRequest) -> dict:
        max_tokens = request.max_tokens or int(model.options.get("default_max_tokens") or 4096)
        payload = {
            "model": model.model_id,
            "max_tokens": max_tokens,
            "messages": [{"role": m.role, "content": self._blocks(m.parts)} for m in request.messages],
        }
        if request.system_instruction:
            payload["system"] = request.system_instruction
        top_p = None if request.temperature is not None else request.top_p
        payload.update(self.sampling(model, temperature=request.temperature, top_p=top_p))
        payload.update(model.options.get("extra_body") or {})
        return payload

    def parse_response(self, model: ResolvedModel, data: dict) -> AIResponse:
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise AIProviderError(
                f"{model.provider_name} API returned an unexpected response shape.",
                provider=model.provider,
            )
        usage = data.get("usage") or {}
        return AIResponse(
            text="".join(b.get("text", "") for b in blocks if b.get("type") == "text"),
            finish_reason=_STOP_REASONS.get(data.get("stop_reason") or "end_turn", "other"),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )
