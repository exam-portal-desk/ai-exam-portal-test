"""
app/services/ai/client.py
The one place AI HTTP calls are made.

    generate("question_generation_text", AIRequest(...))  ->  AIResponse
    stream("assistant_chat", AIRequest(...))              ->  iterator of StreamChunk

Resolves the feature's model via the registry, checks the request against the
model's capabilities, lets the protocol adapter build the provider payload and
parse the reply, and reports failures as AIConfigError / AIProviderError /
AITimeoutError (an AIProviderError carries a `kind`: timeout, rate_limited, auth,
model_rejected, ...). It never substitutes a different model on failure.

A working system writes nothing: only a failed call is logged (one "AI ERROR" line: feature, provider, model, kind,
timing; never a key, header, URL or prompt). With AI_LOG_LEVEL=INFO every request and reply also gets one such line,
for diagnosing.
"""

import logging
import re
import time
from dataclasses import replace
from typing import Iterator, Optional

import requests

import app.config as config
from app.services.ai import registry
from app.services.ai.adapters import get_adapter
from app.services.ai.types import (
    AIConfigError, AIProviderError, AIRequest, AIResponse, AITimeoutError, ResolvedModel, StreamChunk,
)

# Defence in depth: a provider error body or exception text should never echo
# a credential, but if one ever does it must not reach logs or an admin screen.
_SECRET_PATTERNS = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"gsk_[0-9A-Za-z]{16,}"),
    re.compile(r"sk-[0-9A-Za-z_\-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|bearer|x-goog-api-key|x-api-key)\s*[:=]\s*\S+"),
]


def redact(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text


# ─────────────────────────────────────────────
# Logging (safe metadata only)
# ─────────────────────────────────────────────

logger = logging.getLogger("smartaiexam.ai")


def _setup_logging() -> None:
    if logger.handlers or getattr(logger, "_smartaiexam_ready", False):
        return
    logger._smartaiexam_ready = True
    logger.setLevel(getattr(logging, str(getattr(config, "AI_LOG_LEVEL", "INFO")).upper(), logging.INFO))
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s [ai] %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)
    logger.propagate = False


_setup_logging()


def _who(model: ResolvedModel, feature: str) -> str:
    return (f"feature={feature} provider={model.provider} model={model.ref} model_id={model.model_id} "
            f"protocol={model.protocol} source={model.source}")


def _ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _log_request(model: ResolvedModel, feature: str, request: AIRequest, streaming: bool, payload: dict) -> None:
    logger.info("AI REQUEST %s stream=%s messages=%d system_chars=%d max_tokens=%s payload_keys=%s",
                _who(model, feature), streaming, len(request.messages), len(request.system_instruction or ""),
                request.max_tokens, ",".join(sorted(payload)))


def _log_response(model: ResolvedModel, feature: str, ms: int, **fields) -> None:
    extra = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    logger.info("AI RESPONSE %s ms=%d %s", _who(model, feature), ms, extra)


def _one_line(text: str, limit: int = 240) -> str:
    """Provider error bodies are pretty-printed JSON: a failure must be ONE log line, not twenty."""
    return " ".join(redact(str(text)).split())[:limit]


def _log_error(model: ResolvedModel, feature: str, ms: int, err: AIProviderError) -> None:
    logger.warning("AI ERROR %s ms=%d kind=%s status=%s detail=%s", _who(model, feature), ms, err.kind,
                   err.status_code, _one_line(err))


def log_problem(where: str, exc: BaseException) -> None:
    """One short, redacted line for a failure the client did not see itself (a configuration problem, a bug). A failed
    provider call is already logged by the client as "AI ERROR": do not log it a second time."""
    logger.warning("%s: %s: %s", where, type(exc).__name__, _one_line(exc, 200))


# ─────────────────────────────────────────────
# Error classification
# ─────────────────────────────────────────────

# "the id you configured is not a model this provider serves". Deliberately narrow: a 400 that complains
# about a *parameter* ("thinking level is not supported for this model") is not a model problem.
_MODEL_REJECTED = re.compile(
    r"(model[^.\n]{0,80}(does not exist|not found|decommissioned|no longer|unknown|invalid))"
    r"|((invalid|unknown|unsupported|unrecognized) model)|(models/[^\s\"']+ is not found)", re.I)
_BAD_KEY = re.compile(r"api[ _-]?key[^.\n]{0,40}(invalid|not valid|expired)|API_KEY_INVALID|invalid api key", re.I)


def _kind_for_status(status: int, body: str = "") -> str:
    if status == 429:
        return "rate_limited"
    if status in (401, 403) or (status == 400 and _BAD_KEY.search(body or "")):
        return "auth"
    if status == 404 or (status == 400 and _MODEL_REJECTED.search(body or "")):
        return "model_rejected"
    if status == 408:
        return "timeout"
    if status >= 500:
        return "server_error"
    if 400 <= status < 500:
        return "bad_request"
    return "provider_error"


def _http_error(model: ResolvedModel, status: int, body: str) -> AIProviderError:
    return AIProviderError(f"{model.provider_name} API error {status}: {redact(body[:300])}",
                           status_code=status, provider=model.provider, kind=_kind_for_status(status, body))


def _transport_error(model: ResolvedModel, exc: requests.exceptions.RequestException) -> AIProviderError:
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return AITimeoutError(f"{model.provider_name} connection timed out: {redact(str(exc))}",
                              provider=model.provider, kind="connect_timeout")
    if isinstance(exc, requests.exceptions.Timeout):
        return AITimeoutError(f"{model.provider_name} request timed out: {redact(str(exc))}",
                              provider=model.provider, kind="timeout")
    kind = "unreachable" if isinstance(exc, (requests.exceptions.ConnectionError,
                                             requests.exceptions.ChunkedEncodingError)) else "provider_error"
    return AIProviderError(f"{model.provider_name} request failed: {type(exc).__name__}: {redact(str(exc))}",
                           provider=model.provider, kind=kind)


# ─────────────────────────────────────────────
# Resolution / request checks
# ─────────────────────────────────────────────

def resolve(feature: str, api_key_override: Optional[str] = None) -> ResolvedModel:
    """The model `feature` currently uses. `api_key_override` (used only by
    callers that already hold an explicit key) replaces the configured one."""
    model = registry.resolve(feature)
    if api_key_override:
        model = replace(model, api_key=api_key_override)
    return model


def _check_request(model: ResolvedModel, request: AIRequest) -> None:
    missing = request.required_capabilities() - set(model.capabilities)
    if "vision" in missing:
        raise AIConfigError("Selected model does not support vision input.")
    if "pdf" in missing:
        raise AIConfigError("Selected model does not support PDF/document input.")
    if missing:
        raise AIConfigError("Model is not compatible with this AI request.")


def _clamp_max_tokens(model: ResolvedModel, request: AIRequest) -> AIRequest:
    limit = (model.limits or {}).get("max_output_tokens")
    if limit and request.max_tokens and request.max_tokens > limit:
        return replace(request, max_tokens=int(limit))
    return request


def _timeouts(request: AIRequest):
    """(connect, read). The read timeout is how long we wait for the provider's reply — for a normal call the
    whole generation, for a stream the gap between two chunks. Connecting has its own, short, limit."""
    return (config.AI_CONNECT_TIMEOUT, request.timeout or config.AI_REQUEST_TIMEOUT)


# ─────────────────────────────────────────────
# Normal (non-streaming) call
# ─────────────────────────────────────────────

def _call(model: ResolvedModel, feature: str, request: AIRequest) -> AIResponse:
    adapter = get_adapter(model.protocol)
    payload = adapter.build_payload(model, request)
    _log_request(model, feature, request, False, payload)
    started = time.perf_counter()

    try:
        resp = requests.post(adapter.endpoint(model), headers=adapter.headers(model),
                             json=payload, timeout=_timeouts(request))
    except requests.exceptions.RequestException as e:
        err = _transport_error(model, e)
        _log_error(model, feature, _ms(started), err)
        raise err

    if resp.status_code != 200:
        err = _http_error(model, resp.status_code, resp.text)
        _log_error(model, feature, _ms(started), err)
        raise err
    try:
        data = resp.json()
    except ValueError:
        err = AIProviderError(f"{model.provider_name} API returned a non-JSON response.",
                              status_code=resp.status_code, provider=model.provider, kind="malformed")
        _log_error(model, feature, _ms(started), err)
        raise err

    try:
        result = adapter.parse_response(model, data)
    except AIProviderError as e:
        e.kind = "malformed" if e.kind == "provider_error" else e.kind
        _log_error(model, feature, _ms(started), e)
        raise
    result.model_ref, result.provider, result.model_id = model.ref, model.provider, model.model_id
    result.elapsed_ms = _ms(started)
    _log_response(model, feature, result.elapsed_ms, chars=len(result.text or ""), finish=result.finish_reason,
                  tokens_in=result.input_tokens, tokens_out=result.output_tokens, tokens_reasoning=result.reasoning_tokens,
                  provider_timing=result.provider_timings)
    return result


def generate(feature: str, request: AIRequest, api_key_override: Optional[str] = None) -> AIResponse:
    model = resolve(feature, api_key_override)
    _check_request(model, request)
    request = _clamp_max_tokens(model, request)
    return _call(model, feature, request)


# ─────────────────────────────────────────────
# Streaming call
# ─────────────────────────────────────────────

def stream(feature: str, request: AIRequest, api_key_override: Optional[str] = None) -> Iterator[StreamChunk]:
    """Yield the reply as the provider produces it.

    If the feature's protocol adapter has no streaming support this is NOT simulated: one normal call is made and
    its whole reply is yielded as a single chunk. Errors are raised exactly like generate(), also mid-stream."""
    model = resolve(feature, api_key_override)
    _check_request(model, request)
    request = _clamp_max_tokens(model, request)
    adapter = get_adapter(model.protocol)

    if not adapter.supports_streaming:
        result = _call(model, feature, request)
        yield StreamChunk(text=result.text, finish_reason=result.finish_reason, input_tokens=result.input_tokens,
                          output_tokens=result.output_tokens, reasoning_tokens=result.reasoning_tokens,
                          provider_timings=result.provider_timings)
        return

    payload = adapter.stream_payload(model, request)
    _log_request(model, feature, request, True, payload)
    started = time.perf_counter()
    headers = {**adapter.headers(model), "Accept": "text/event-stream"}
    try:
        resp = requests.post(adapter.stream_endpoint(model), headers=headers, json=payload,
                             timeout=_timeouts(request), stream=True)
    except requests.exceptions.RequestException as e:
        err = _transport_error(model, e)
        _log_error(model, feature, _ms(started), err)
        raise err

    first_ms, chars, last = None, 0, {}
    total_limit = request.total_timeout or config.AI_STREAM_TOTAL_TIMEOUT
    try:
        if resp.status_code != 200:
            raise _http_error(model, resp.status_code, resp.text)
        resp.encoding = "utf-8"          # text/event-stream has no charset; requests would guess Latin-1
        for line in resp.iter_lines(chunk_size=1, decode_unicode=True):
            if time.perf_counter() - started > total_limit:
                raise AITimeoutError(f"{model.provider_name} stream exceeded {int(total_limit)}s in total.",
                                     provider=model.provider, kind="timeout")
            data = adapter.sse_json(line)
            if data is None:
                continue
            if isinstance(data.get("error"), dict):
                code = data["error"].get("code")
                status = code if isinstance(code, int) else 500
                raise _http_error(model, status, str(data["error"].get("message", "")))
            chunk = adapter.parse_stream_event(model, data)
            if chunk is None:
                continue
            if chunk.text:
                chars += len(chunk.text)
                if first_ms is None:
                    first_ms = _ms(started)
            for k in ("finish_reason", "input_tokens", "output_tokens", "reasoning_tokens", "provider_timings"):
                if getattr(chunk, k) is not None:
                    last[k] = getattr(chunk, k)
            yield chunk
        if not chars:
            raise AIProviderError(f"{model.provider_name} stream ended without any text.",
                                  provider=model.provider, kind="malformed")
    except requests.exceptions.RequestException as e:
        err = _transport_error(model, e)
        _log_error(model, feature, _ms(started), err)
        raise err
    except AIProviderError as e:
        _log_error(model, feature, _ms(started), e)
        raise
    finally:
        resp.close()

    _log_response(model, feature, _ms(started), first_token_ms=first_ms, chars=chars, finish=last.get("finish_reason"),
                  tokens_in=last.get("input_tokens"), tokens_out=last.get("output_tokens"),
                  tokens_reasoning=last.get("reasoning_tokens"), provider_timing=last.get("provider_timings"))


def upload_file(feature: str, path: str, mime_type: str, timeout: float,
                api_key_override: Optional[str] = None) -> str:
    """Upload a local file to the feature's provider (where the provider has a
    file API) and return a reusable reference for Part.of_file(uri=...)."""
    model = resolve(feature, api_key_override)
    adapter = get_adapter(model.protocol)
    if not adapter.supports_file_upload:
        raise AIConfigError(f"{model.provider_name} does not support file uploads; "
                            f"send the file inline instead.")
    return adapter.upload_file(model, path, mime_type, timeout)
