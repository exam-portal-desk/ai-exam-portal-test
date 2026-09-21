"""
app/services/ai/adapters/gemini.py
Google Gemini native generateContent protocol (+ resumable File API upload).

Optional config (provider `options` or model `options`):
  omit_params             params this model rejects
  extra_generation_config dict merged into generationConfig (e.g. thinkingConfig)
  extra_body              dict merged verbatim into the request body

Penalty params (frequency/presence) are intentionally not forwarded: not every
Gemini model accepts them and the API rejects the whole request when unsupported.

Thinking: some models (Gemini 2.5, Gemma 4, ...) reason before answering and the API marks
those reasoning parts `thought: true`. They are never part of the answer text returned here.
How much a model thinks is model-specific, so it is configured on the offering, e.g.
  "options": { "extra_generation_config": { "thinkingConfig": { "thinkingLevel": "minimal" } } }
"""

import json
import os

import requests

from app.services.ai.adapters.base import ProviderAdapter, register_adapter
from app.services.ai.types import (
    AIProviderError, AIRequest, AIResponse, AITimeoutError, ResolvedModel, StreamChunk,
)

_FINISH_REASONS = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
}


@register_adapter
class GeminiAdapter(ProviderAdapter):
    protocol = "gemini"
    label = "Google Gemini generateContent"
    supports_file_upload = True
    supports_streaming = True

    @staticmethod
    def _answer_text(candidate: dict) -> str:
        """The visible answer of one candidate: every text part that is not model reasoning."""
        parts = (candidate.get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))

    def endpoint(self, model: ResolvedModel) -> str:
        if model.api.get("endpoint"):
            return self.joined_url(model.api.get("base_url", ""), model.api["endpoint"])
        return f"{model.api.get('base_url', '').rstrip('/')}/models/{model.model_id}:generateContent"

    @staticmethod
    def _parts(parts):
        out = []
        for p in parts:
            if p.kind == "text":
                out.append({"text": p.text})
            elif p.data:      # image, or inline file
                out.append({"inlineData": {"mimeType": p.mime_type, "data": p.data}})
            elif p.uri:
                out.append({"fileData": {"fileUri": p.uri, "mimeType": p.mime_type}})
        return out

    def build_payload(self, model: ResolvedModel, request: AIRequest) -> dict:
        payload = {
            "contents": [
                {"role": "model" if m.role == "assistant" else "user", "parts": self._parts(m.parts)}
                for m in request.messages
            ],
        }
        if request.system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": request.system_instruction}]}

        gen = self.sampling(
            model,
            temperature=request.temperature,
            topP=request.top_p,
            maxOutputTokens=request.max_tokens,
        )
        if request.response_format == "json" and "structured_output" in model.capabilities:
            gen["responseMimeType"] = "application/json"
        gen.update(model.options.get("extra_generation_config") or {})
        if gen:
            payload["generationConfig"] = gen
        payload.update(model.options.get("extra_body") or {})
        return payload

    def parse_response(self, model: ResolvedModel, data: dict) -> AIResponse:
        candidates = data.get("candidates") or []
        if not candidates:
            raise AIProviderError(
                f"{model.provider_name} API returned no candidates: {json.dumps(data)[:300]}",
                provider=model.provider,
            )
        cand = candidates[0]
        text = self._answer_text(cand)
        usage = data.get("usageMetadata") or {}
        return AIResponse(
            text=text,
            finish_reason=_FINISH_REASONS.get(cand.get("finishReason") or "STOP", "other"),
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
            reasoning_tokens=usage.get("thoughtsTokenCount"),
        )

    # ── streaming ─────────────────────────────────────────────────────────
    def stream_endpoint(self, model: ResolvedModel) -> str:
        if model.api.get("endpoint"):
            return self.joined_url(model.api.get("base_url", ""), model.api["endpoint"]) + "?alt=sse"
        return f"{model.api.get('base_url', '').rstrip('/')}/models/{model.model_id}:streamGenerateContent?alt=sse"

    def stream_payload(self, model: ResolvedModel, request: AIRequest) -> dict:
        return self.build_payload(model, request)

    def parse_stream_event(self, model: ResolvedModel, data: dict):
        usage = data.get("usageMetadata") or {}
        candidates = data.get("candidates") or []
        text, finish = "", None
        if candidates and isinstance(candidates[0], dict):
            text = self._answer_text(candidates[0])
            if candidates[0].get("finishReason"):
                finish = _FINISH_REASONS.get(candidates[0]["finishReason"], "other")
        if not (text or finish or usage):
            return None
        return StreamChunk(text=text, finish_reason=finish, input_tokens=usage.get("promptTokenCount"),
                           output_tokens=usage.get("candidatesTokenCount"),
                           reasoning_tokens=usage.get("thoughtsTokenCount"))

    def upload_file(self, model: ResolvedModel, path: str, mime_type: str, timeout: float) -> str:
        """Resumable File API upload; returns a file URI reusable across
        requests. The URI is only valid for Gemini-protocol models."""
        upload_url = model.api.get("upload_url")
        if not upload_url:
            raise AIProviderError(f"No upload_url configured for provider '{model.provider_name}'.",
                                  provider=model.provider)

        file_size = os.path.getsize(path)
        start_headers = {
            **self.headers(model),
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(file_size),
            "X-Goog-Upload-Header-Content-Type": mime_type,
        }
        try:
            start = requests.post(upload_url, headers=start_headers,
                                  json={"file": {"display_name": os.path.basename(path)}},
                                  timeout=timeout)
            if start.status_code != 200:
                raise AIProviderError(
                    f"{model.provider_name} file upload init failed {start.status_code}: {start.text[:300]}",
                    status_code=start.status_code, provider=model.provider)
            session_url = start.headers.get("X-Goog-Upload-URL") or start.headers.get("x-goog-upload-url")
            if not session_url:
                raise AIProviderError(f"{model.provider_name} file upload did not return an upload URL",
                                      provider=model.provider)

            with open(path, "rb") as f:
                body = f.read()
            done = requests.post(
                session_url,
                headers={"Content-Length": str(file_size), "X-Goog-Upload-Offset": "0",
                         "X-Goog-Upload-Command": "upload, finalize"},
                data=body, timeout=timeout,
            )
        except requests.exceptions.Timeout as e:
            raise AITimeoutError(f"{model.provider_name} file upload timed out: {e}", provider=model.provider)
        except requests.exceptions.RequestException as e:
            raise AIProviderError(f"{model.provider_name} file upload failed: {type(e).__name__}: {e}",
                                  provider=model.provider)

        if done.status_code != 200:
            raise AIProviderError(
                f"{model.provider_name} file upload failed {done.status_code}: {done.text[:300]}",
                status_code=done.status_code, provider=model.provider)
        uri = done.json().get("file", {}).get("uri")
        if not uri:
            raise AIProviderError(
                f"{model.provider_name} file upload response missing file URI: {done.text[:300]}",
                provider=model.provider)
        return uri
