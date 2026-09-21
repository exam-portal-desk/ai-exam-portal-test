"""
app/services/ai/adapters/base.py
Provider adapter contract. An adapter owns EVERYTHING protocol-specific:
endpoint shape, auth headers, request payload, response parsing, and (where
the provider has one) file upload. The rest of the app only ever sees the
normalized AIRequest / AIResponse in app/services/ai/types.py.

Adding a new API protocol = one new subclass registered with @register_adapter
(see openai_compatible.py for the shortest example). Adding models or
providers that speak an existing protocol needs no code at all.
"""

import json
from typing import Dict, List, Optional, Type

from app.services.ai.types import AIConfigError, AIRequest, AIResponse, ResolvedModel, StreamChunk

_ADAPTERS: Dict[str, "ProviderAdapter"] = {}


def register_adapter(cls: Type["ProviderAdapter"]) -> Type["ProviderAdapter"]:
    """Class decorator: makes the adapter selectable via `protocol` in the registry."""
    if not cls.protocol:
        raise ValueError(f"{cls.__name__} must define a non-empty `protocol`")
    _ADAPTERS[cls.protocol] = cls()
    return cls


def get_adapter(protocol: str) -> "ProviderAdapter":
    adapter = _ADAPTERS.get(protocol)
    if adapter is None:
        raise AIConfigError(f"No adapter is installed for API protocol '{protocol}'.")
    return adapter


def list_protocols() -> List[str]:
    return sorted(_ADAPTERS)


class ProviderAdapter:
    protocol: str = ""
    label: str = ""                       # human-readable protocol name (admin UI)
    supports_file_upload: bool = False
    supports_streaming: bool = False      # True only when stream_* below are implemented

    # ── per-protocol hooks ────────────────────────────────────────────────

    def endpoint(self, model: ResolvedModel) -> str:
        raise NotImplementedError

    def build_payload(self, model: ResolvedModel, request: AIRequest) -> dict:
        raise NotImplementedError

    def parse_response(self, model: ResolvedModel, data: dict) -> AIResponse:
        raise NotImplementedError

    def upload_file(self, model: ResolvedModel, path: str, mime_type: str, timeout: float) -> str:
        raise AIConfigError(f"Provider protocol '{self.protocol}' does not support file uploads.")

    def extra_headers(self, model: ResolvedModel) -> Dict[str, str]:
        """Protocol-specific headers beyond content-type/auth."""
        return {}

    # ── optional streaming hooks (Server-Sent Events) ───────────────────────
    # An adapter that sets supports_streaming = True gives the client the URL and body for a streamed
    # call, and turns each SSE `data:` payload into a StreamChunk. Adapters that do not simply keep the
    # normal request/response path.

    def stream_endpoint(self, model: ResolvedModel) -> str:
        raise NotImplementedError

    def stream_payload(self, model: ResolvedModel, request: AIRequest) -> dict:
        raise NotImplementedError

    def parse_stream_event(self, model: ResolvedModel, data: dict) -> Optional[StreamChunk]:
        """One decoded SSE JSON payload -> a StreamChunk (or None to ignore it)."""
        raise NotImplementedError

    @staticmethod
    def sse_json(line: str) -> Optional[dict]:
        """The JSON object carried by one SSE line, or None for anything else (comments, keep-alives,
        `[DONE]`, event names, malformed data)."""
        if not line or not line.startswith("data:"):
            return None
        body = line[5:].strip()
        if not body or body == "[DONE]":
            return None
        try:
            obj = json.loads(body)
        except ValueError:
            return None
        return obj if isinstance(obj, dict) else None

    # ── shared behaviour ──────────────────────────────────────────────────

    def headers(self, model: ResolvedModel) -> Dict[str, str]:
        """Content-Type + auth header (config-driven: `api.auth.header` /
        `api.auth.scheme`, e.g. Authorization/Bearer or x-goog-api-key/"")
        + provider/model `api.headers` + protocol extras."""
        headers = {"Content-Type": "application/json"}
        if model.api.get("requires_key", True):
            auth = model.api.get("auth") or {}
            header = auth.get("header") or "Authorization"
            scheme = auth.get("scheme", "Bearer" if header.lower() == "authorization" else "")
            headers[header] = f"{scheme} {model.api_key}".strip() if scheme else model.api_key
        headers.update(model.api.get("headers") or {})
        headers.update(self.extra_headers(model))
        return headers

    @staticmethod
    def joined_url(base_url: str, path: str) -> str:
        if path.startswith(("http://", "https://")):
            return path
        return f"{base_url.rstrip('/')}/{path.lstrip('/')}"

    @staticmethod
    def sampling(model: ResolvedModel, **params) -> dict:
        """Drop unset params and any param the model/provider config lists in
        options.omit_params (escape hatch for models that reject a param)."""
        omit = set(model.options.get("omit_params") or [])
        return {k: v for k, v in params.items() if v is not None and k not in omit}
