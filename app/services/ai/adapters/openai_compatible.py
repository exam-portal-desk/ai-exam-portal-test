"""
app/services/ai/adapters/openai_compatible.py
OpenAI Chat Completions wire format — spoken by OpenAI, Groq, Mistral,
Together, DeepSeek, OpenRouter, xAI, Ollama/vLLM and many others. Any provider
using this protocol is configuration-only.

Optional config (provider `options` or model `options`):
  max_tokens_field  name of the max-output field (default "max_tokens";
                    newer OpenAI reasoning models want "max_completion_tokens")
  omit_params       params this model rejects, e.g. ["temperature"]
  extra_body        dict merged verbatim into the request body
"""

from app.services.ai.adapters.base import ProviderAdapter, register_adapter
from app.services.ai.types import (
    AIProviderError, AIRequest, AIResponse, Part, ResolvedModel, StreamChunk,
)

_FINISH_REASONS = {"stop": "stop", "length": "length", "content_filter": "content_filter"}

# Providers that report their own inference timing put it in `usage` (seconds). Only these keys are read.
_TIMING_KEYS = ("queue_time", "prompt_time", "completion_time", "total_time")


def _timings(usage: dict):
    found = {k: usage[k] for k in _TIMING_KEYS if isinstance(usage.get(k), (int, float))}
    return found or None


@register_adapter
class OpenAICompatibleAdapter(ProviderAdapter):
    protocol = "openai_compatible"
    label = "OpenAI-compatible Chat Completions"
    supports_streaming = True

    def endpoint(self, model: ResolvedModel) -> str:
        path = model.api.get("endpoint") or "/chat/completions"
        return self.joined_url(model.api.get("base_url", ""), path)

    @staticmethod
    def _content(parts):
        # A lone text part stays a plain string — identical to the payload
        # these features sent before adapters existed.
        if len(parts) == 1 and parts[0].kind == "text":
            return parts[0].text
        out = []
        for p in parts:
            if p.kind == "text":
                out.append({"type": "text", "text": p.text})
            elif p.kind == "image":
                out.append({"type": "image_url",
                            "image_url": {"url": f"data:{p.mime_type};base64,{p.data}"}})
            elif p.kind == "file":
                if not p.data:
                    raise AIProviderError("This protocol cannot reference an uploaded file by URI.")
                out.append({"type": "file",
                            "file": {"filename": p.name or "document.pdf",
                                     "file_data": f"data:{p.mime_type};base64,{p.data}"}})
        return out

    def build_payload(self, model: ResolvedModel, request: AIRequest) -> dict:
        messages = []
        if request.system_instruction:
            messages.append({"role": "system", "content": request.system_instruction})
        for m in request.messages:
            messages.append({"role": m.role, "content": self._content(m.parts)})

        max_field = model.options.get("max_tokens_field") or "max_tokens"
        payload = {"model": model.model_id, "messages": messages}
        payload.update(self.sampling(
            model,
            temperature=request.temperature,
            top_p=request.top_p,
            frequency_penalty=request.frequency_penalty,
            presence_penalty=request.presence_penalty,
            **{max_field: request.max_tokens},
        ))
        if request.response_format == "json" and "structured_output" in model.capabilities:
            payload["response_format"] = {"type": "json_object"}
        low = model.options.get("low_reasoning_effort")           # e.g. "low": the value THIS model's provider expects
        if request.reasoning_effort == "low" and low:
            payload["reasoning_effort"] = low
        payload.update(model.options.get("extra_body") or {})
        return payload

    def parse_response(self, model: ResolvedModel, data: dict) -> AIResponse:
        try:
            choice = data["choices"][0]
            content = choice["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise AIProviderError(
                f"{model.provider_name} API returned an unexpected response shape.",
                provider=model.provider,
            )
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        usage = data.get("usage") or {}
        return AIResponse(
            text=content or "",
            finish_reason=_FINISH_REASONS.get(choice.get("finish_reason") or "stop", "other"),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            reasoning_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            provider_timings=_timings(usage),
        )

    # ── streaming ─────────────────────────────────────────────────────────
    def stream_endpoint(self, model: ResolvedModel) -> str:
        return self.endpoint(model)

    def stream_payload(self, model: ResolvedModel, request: AIRequest) -> dict:
        payload = self.build_payload(model, request)
        payload["stream"] = True
        return payload

    def parse_stream_event(self, model: ResolvedModel, data: dict):
        usage = data.get("usage") or (data.get("x_groq") or {}).get("usage") or {}
        choices = data.get("choices") or []
        text, finish = "", None
        if choices and isinstance(choices[0], dict):
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            text = content if isinstance(content, str) else ""
            if choices[0].get("finish_reason"):
                finish = _FINISH_REASONS.get(choices[0]["finish_reason"], "other")
        if not (text or finish or usage):
            return None
        return StreamChunk(text=text, finish_reason=finish,
                           input_tokens=usage.get("prompt_tokens"), output_tokens=usage.get("completion_tokens"),
                           provider_timings=_timings(usage))
