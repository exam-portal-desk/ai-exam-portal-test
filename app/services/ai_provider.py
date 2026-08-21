"""
app/services/ai_provider.py
AI model registry resolver. config/ai_models.json defines which models
exist (text_models / vision_models); env vars select which one is active
per flow and hold each model's API key. Services request a resolved model
by category + name and get back {provider, model, api_key, endpoint} —
call mechanics stay in each service since request/response shapes differ
per provider and per flow.
"""

import json
import os
from pathlib import Path
from typing import Dict

_REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "ai_models.json"


def _load_registry() -> Dict:
    with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_registry = _load_registry()


def get_model(category: str, name: str) -> Dict:
    """Resolved config for one registry entry: provider, model, api_key, endpoint (if any)."""
    for entry in _registry.get(category, []):
        if entry["name"] == name:
            api_key = os.environ.get(entry.get("api_key_env", ""), "")
            return {**entry, "api_key": api_key}
    raise ValueError(f"AI model '{name}' not found in {category}")


def list_models(category: str) -> list:
    """Safe listing for a future model-selector UI — names only, never keys."""
    return [{"name": e["name"], "provider": e["provider"]} for e in _registry.get(category, [])]


def build_headers(model: Dict) -> Dict:
    """
    HTTP headers for calling this model's endpoint. Auth scheme is config-driven
    so swapping providers never requires touching service code: defaults to
    `Authorization: Bearer <key>` (Groq-style); a registry entry can override
    the header name via `auth_header` (e.g. Gemini's `x-goog-api-key`).
    """
    auth_header = model.get("auth_header")
    if auth_header:
        return {auth_header: model["api_key"], "Content-Type": "application/json"}
    return {"Authorization": f"Bearer {model['api_key']}", "Content-Type": "application/json"}
