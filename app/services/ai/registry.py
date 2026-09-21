"""
app/services/ai/registry.py
Single source of truth for AI configuration.

  config/ai_models.json   providers, models (identity), offerings (provider x model),
                          features, default assignments
  ai_feature_assignments  Admin-chosen model overrides (DB, tiny, cached)
  environment             API keys only (never in the JSON). It never chooses a model.

resolve(feature) turns "which model does this feature use right now" into a
ResolvedModel, or raises AIConfigError with a message an admin can act on.
It never falls back to a different model on its own.

Caching: the JSON is parsed once and re-checked (a single os.stat) at most
once per AI_CONFIG_CACHE_TTL_SECONDS; DB overrides are fetched at most once
per TTL per worker. A normal AI request therefore does dict lookups only —
no disk read, no DB query. Saving an override applies immediately in the
saving worker; other workers pick it up within the TTL.

Selection precedence for a feature (the environment is not part of it):
  1. Admin override (ai_feature_assignments)
  2. `assignments.<feature>.model` in ai_models.json

API key for a feature + model: the first of these that is set in the environment
  1. `assignments.<feature>.credential_env.<provider>`  (optional, a key just for this feature)
  2. the offering's own `api.key_env`                    (optional)
  3. `providers.<provider>.api.key_env`                  (the normal, provider-wide key)
It only ever looks at keys named for the model's own provider, and never switches model.
"""

import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import app.config as config
from app.services.ai.adapters import get_adapter, list_protocols
from app.services.ai.types import AIConfigError, ResolvedModel

SUPPORTED_SCHEMA_VERSION = 3

# Project root, used only to check that a configured /static/... logo file exists.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(config.__file__)))


# ─────────────────────────────────────────────
# Parsed, validated snapshot of ai_models.json
# ─────────────────────────────────────────────

@dataclass
class _Snapshot:
    capabilities: Dict[str, dict]
    providers: Dict[str, dict]
    models: Dict[str, dict]            # callable "provider/model" refs -> identity + offering, composed
    groups: Dict[str, dict]
    features: Dict[str, dict]
    assignments: Dict[str, dict]
    rate_limit_fields: Dict[str, dict] = field(default_factory=dict)
    rate_limits: Dict[str, dict] = field(default_factory=dict)
    identities: Dict[str, dict] = field(default_factory=dict)   # provider-independent model identities
    aliases: Dict[str, str] = field(default_factory=dict)
    issues: List[dict] = field(default_factory=list)          # {"level","message"}
    model_errors: Dict[str, str] = field(default_factory=dict)  # ref -> why unusable
    loaded_at: float = 0.0
    mtime: float = 0.0

    def ref_for(self, value: str) -> Optional[str]:
        """A model ref, or a legacy alias of one, -> canonical ref (else None)."""
        if value in self.models:
            return value
        return self.aliases.get(value)


def _issue(issues: list, level: str, message: str) -> None:
    issues.append({"level": level, "message": message})


def _build_snapshot(data) -> _Snapshot:
    if not isinstance(data, dict):
        raise AIConfigError("ai_models.json must contain a JSON object.")
    if "providers" not in data and ("text_models" in data or "vision_models" in data):
        raise AIConfigError(
            "ai_models.json uses the old flat text_models/vision_models layout. "
            "It must be migrated to schema_version 3 (providers / models / offerings / features / "
            "assignments) — see config/AI_MODELS.md."
        )
    version = data.get("schema_version")
    if version == 2:
        raise AIConfigError(
            "ai_models.json is schema_version 2, where each model was tied to one provider. Version 3 keeps the "
            "model's identity (name, capabilities, logo) in `models` and moves provider-specific facts "
            "(API model id, limits, aliases) to `offerings.<provider>.<model>` — see config/AI_MODELS.md."
        )
    if version != SUPPORTED_SCHEMA_VERSION:
        raise AIConfigError(
            f"ai_models.json schema_version is {version!r}; this build supports {SUPPORTED_SCHEMA_VERSION}."
        )
    for section in ("providers", "models", "offerings", "features", "assignments"):
        if not isinstance(data.get(section), dict):
            raise AIConfigError(f"ai_models.json is missing the '{section}' object.")

    snap = _Snapshot(
        capabilities=dict(data.get("capabilities") or {}),
        providers=dict(data["providers"]),
        models={},
        identities=dict(data["models"]),
        groups=dict(data.get("groups") or {}),
        features=dict(data["features"]),
        assignments=dict(data["assignments"]),
        rate_limit_fields=dict(data.get("rate_limit_fields") or {}),
        rate_limits=dict(data.get("rate_limits") or {}),
    )
    issues = snap.issues
    protocols = set(list_protocols())

    for pkey, prov in snap.providers.items():
        if not isinstance(prov, dict):
            _issue(issues, "error", f"Provider '{pkey}' must be an object.")
            snap.providers[pkey] = {"enabled": False}
            continue
        proto = prov.get("protocol")
        if not proto:
            _issue(issues, "error", f"Provider '{pkey}' has no `protocol`.")
        elif proto not in protocols:
            _issue(issues, "error",
                   f"Provider '{pkey}' uses protocol '{proto}' but no adapter is installed for it "
                   f"(installed: {', '.join(sorted(protocols))}).")
        if not (prov.get("api") or {}).get("base_url"):
            _issue(issues, "warning", f"Provider '{pkey}' has no api.base_url.")

    # A callable model = one provider serving one model identity. Identity facts
    # (name, description, capabilities, logo) come from `models`; provider-specific
    # facts (API id, limits, aliases, overrides) from `offerings`. The model's `ui`
    # is ONLY its own — nothing here ever reads a provider's ui.
    structural: Dict[str, List[str]] = {}
    offered_models = set()
    for pkey, served in data["offerings"].items():
        for mkey, off in (served if isinstance(served, dict) else {}).items():
            ref = f"{pkey}/{mkey}"
            problems = structural.setdefault(ref, [])
            off = off if isinstance(off, dict) else {}
            if "/" in pkey or "/" in mkey:
                problems.append("provider and model keys must not contain '/'")
            ident = snap.identities.get(mkey)
            if not isinstance(ident, dict):
                problems.append(f"unknown model '{mkey}' (declare it under `models`)")
                ident = {}
            else:
                offered_models.add(mkey)
            snap.models[ref] = {
                "provider": pkey,
                "model": mkey,
                "model_id": off.get("model_id"),
                "display_name": ident.get("display_name") or mkey,
                "description": ident.get("description"),
                # capabilities belong to the model; an offering may narrow/override them
                # when a provider exposes a different feature set.
                "capabilities": off["capabilities"] if "capabilities" in off else (ident.get("capabilities") or []),
                "enabled": off.get("enabled", True),
                "limits": off.get("limits") or {},
                "aliases": off.get("aliases") or [],
                "api": off.get("api"),
                "options": off.get("options"),
                "ui": ident.get("ui"),
            }
    for pkey in data["offerings"]:
        if pkey not in snap.providers:
            _issue(issues, "error", f"`offerings` lists unknown provider '{pkey}'.")
    for mkey in snap.identities:
        if mkey not in offered_models:
            _issue(issues, "warning", f"Model '{mkey}' is not offered by any provider (add it under `offerings`).")

    for ref, model in snap.models.items():
        problems = structural.get(ref, [])
        prov = snap.providers.get(model.get("provider"))
        if prov is None:
            problems.append(f"unknown provider '{model.get('provider')}'")
        if not model.get("model_id"):
            problems.append("missing `model_id`")
        unknown = [c for c in model.get("capabilities") or [] if c not in snap.capabilities]
        if unknown:
            problems.append(f"unknown capabilities {unknown}")
        if prov is not None and prov.get("protocol") not in protocols:
            problems.append(f"no adapter for protocol '{prov.get('protocol')}'")
        for alias in model.get("aliases") or []:
            if alias in snap.models or (alias in snap.aliases and snap.aliases[alias] != ref):
                _issue(issues, "error", f"Alias '{alias}' on model '{ref}' collides with another model or alias.")
            else:
                snap.aliases[alias] = ref
        if problems:
            snap.model_errors[ref] = "; ".join(problems)
            _issue(issues, "error", f"Model '{ref}' is unusable: {snap.model_errors[ref]}.")

    for fkey, feat in snap.features.items():
        if not isinstance(feat, dict):
            _issue(issues, "error", f"Feature '{fkey}' must be an object.")
            snap.features[fkey] = feat = {"requires": []}
        if feat.get("group") and feat["group"] not in snap.groups:
            _issue(issues, "warning", f"Feature '{fkey}' references unknown group '{feat['group']}'.")
        bad = [c for c in feat.get("requires") or [] if c not in snap.capabilities]
        if bad:
            _issue(issues, "error", f"Feature '{fkey}' requires unknown capabilities {bad}.")

        assign = snap.assignments.get(fkey)
        if not assign or not assign.get("model"):
            _issue(issues, "warning", f"Feature '{fkey}' has no default model in `assignments`.")
            continue
        ref = snap.ref_for(assign["model"])
        if ref is None:
            _issue(issues, "error", f"Default model '{assign['model']}' for feature '{fkey}' does not exist.")
        elif not set(feat.get("requires") or []) <= set(snap.models[ref].get("capabilities") or []):
            _issue(issues, "error", f"Default model '{ref}' for feature '{fkey}' lacks required capabilities "
                                    f"{feat.get('requires')}.")

    for fkey, assign in snap.assignments.items():
        if fkey not in snap.features:
            _issue(issues, "warning", f"`assignments` entry '{fkey}' matches no feature.")
        if isinstance(assign, dict) and "env_override" in assign:
            _issue(issues, "warning", f"`assignments.{fkey}.env_override` is no longer used: models are chosen in "
                                      f"Admin > AI Configuration, never in the environment. You can delete that line.")

    def _check_logo(owner: str, ui, fallback: str) -> None:
        logo = (ui or {}).get("logo") if isinstance(ui, dict) else None
        if isinstance(logo, str) and logo.startswith("/static/"):
            path = os.path.normpath(os.path.join(_PROJECT_ROOT, logo.lstrip("/")))
            if not path.startswith(os.path.join(_PROJECT_ROOT, "static") + os.sep) or not os.path.isfile(path):
                _issue(issues, "warning", f"Logo file for {owner} not found: {logo} ({fallback})")

    for pkey, prov in snap.providers.items():
        _check_logo(f"provider '{pkey}'", prov.get("ui"), "an initials badge is shown until it exists")
    for mkey, ident in snap.identities.items():
        _check_logo(f"model '{mkey}'", ident.get("ui") if isinstance(ident, dict) else None,
                    "no model logo is shown until it exists")

    # Rate limits are informational (shown in Admin), so problems are warnings and
    # the bad entry is simply not displayed — they never make a model unusable.
    for ref, entry in list(snap.rate_limits.items()):
        if ref not in snap.models:
            _issue(issues, "warning", f"`rate_limits` entry '{ref}' matches no model.")
            del snap.rate_limits[ref]
            continue
        limits = (entry or {}).get("limits") if isinstance(entry, dict) else None
        if not isinstance(limits, dict) or not limits:
            _issue(issues, "warning", f"`rate_limits` entry '{ref}' has no `limits` object.")
            del snap.rate_limits[ref]
            continue
        for name, value in list(limits.items()):
            valid_value = value == "unlimited" or (isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0)
            if name not in snap.rate_limit_fields:
                _issue(issues, "warning", f"`rate_limits` for '{ref}' uses unknown field '{name}' "
                                          f"(declare it in `rate_limit_fields`).")
                del limits[name]
            elif not valid_value:
                _issue(issues, "warning", f"`rate_limits` for '{ref}': '{name}' must be a non-negative number or \"unlimited\".")
                del limits[name]
    return snap


# ─────────────────────────────────────────────
# Process-wide state
# ─────────────────────────────────────────────

_TTL = max(1, int(config.AI_CONFIG_CACHE_TTL_SECONDS))
_state_lock = threading.RLock()
_overrides_lock = threading.Lock()

_snapshot: Optional[_Snapshot] = None
_snapshot_checked_at = 0.0
_last_reload_error: Optional[str] = None

_overrides: Dict[str, dict] = {}
_overrides_loaded_at = 0.0
_overrides_error: Optional[str] = None


def _read_file() -> Tuple[_Snapshot, float]:
    path = config.AI_MODELS_CONFIG_PATH
    mtime = os.stat(path).st_mtime
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise AIConfigError(f"ai_models.json is not valid JSON: {e}")
    snap = _build_snapshot(data)
    snap.loaded_at = time.time()
    snap.mtime = mtime
    return snap, mtime


def _current() -> _Snapshot:
    """The active snapshot; re-stats the file at most once per TTL and swaps
    in a changed file only if it validates (else keeps the last good one)."""
    global _snapshot, _snapshot_checked_at, _last_reload_error
    now = time.monotonic()
    if _snapshot is not None and now - _snapshot_checked_at < _TTL:
        return _snapshot
    with _state_lock:
        now = time.monotonic()
        if _snapshot is not None and now - _snapshot_checked_at < _TTL:
            return _snapshot
        try:
            if _snapshot is None or os.stat(config.AI_MODELS_CONFIG_PATH).st_mtime != _snapshot.mtime:
                _snapshot, _ = _read_file()
                _last_reload_error = None
        except Exception as e:
            if _snapshot is None:
                raise            # nothing to fall back on — surface the real error
            _last_reload_error = _public_error(e)
            print(f"[ai.registry] keeping previous config; reload failed: {e}")
        _snapshot_checked_at = now
        return _snapshot


def _public_error(exc: Exception) -> str:
    """Text safe to show on the Admin page: our own validation messages as-is; any other
    failure (file/OS/database errors can contain server paths or hostnames) is generic."""
    if isinstance(exc, AIConfigError):
        return str(exc)
    return "The configuration file could not be read (details are in the server log)."


def _refresh_overrides() -> Dict[str, dict]:
    """DB overrides, cached for TTL. On a DB failure the last known good
    overrides keep being served (never silently reset to defaults)."""
    global _overrides, _overrides_loaded_at, _overrides_error
    now = time.monotonic()
    if _overrides_loaded_at and now - _overrides_loaded_at < _TTL:
        return _overrides
    # Blocking only for the very first load; afterwards one thread refreshes
    # while the rest keep using the cached value.
    if not _overrides_lock.acquire(blocking=not _overrides_loaded_at):
        return _overrides
    try:
        now = time.monotonic()
        if _overrides_loaded_at and now - _overrides_loaded_at < _TTL:
            return _overrides
        try:
            from app.db.ai import list_feature_assignments
            rows = list_feature_assignments()
            _overrides = {r["feature_key"]: r for r in rows}
            _overrides_error = None
        except Exception as e:
            _overrides_error = f"{type(e).__name__}: {e}"
            print(f"[ai.registry] could not read ai_feature_assignments "
                  f"({'serving last known' if _overrides else 'using registry defaults'}): {e}")
        _overrides_loaded_at = now
        return _overrides
    finally:
        _overrides_lock.release()


def reload() -> Tuple[bool, Optional[str]]:
    """Force-reload ai_models.json and the DB overrides now. Returns
    (ok, error). A file that fails validation leaves the previous config active."""
    global _snapshot, _snapshot_checked_at, _last_reload_error, _overrides_loaded_at
    with _state_lock:
        try:
            _snapshot, _ = _read_file()
            _last_reload_error = None
            ok, err = True, None
        except Exception as e:
            _last_reload_error = err = _public_error(e)
            print(f"[ai.registry] reload failed: {e}")
            ok = False
            if _snapshot is None:
                raise
        _snapshot_checked_at = time.monotonic()
    _overrides_loaded_at = 0.0
    _refresh_overrides()
    return ok, err


# ─────────────────────────────────────────────
# Selection / validation
# ─────────────────────────────────────────────

def _key_env_names(snap: _Snapshot, feature_key: str, model: dict) -> List[str]:
    """Env var names that may hold the key for this feature+model, most specific first:
    a key dedicated to this feature, the offering's own, then the provider-wide key."""
    provider = model.get("provider")
    scoped = (snap.assignments.get(feature_key) or {}).get("credential_env") or {}
    names = [scoped.get(provider),
             (model.get("api") or {}).get("key_env"),
             (snap.providers.get(provider, {}).get("api") or {}).get("key_env")]
    return [n for i, n in enumerate(names) if n and n not in names[:i]]


def _requires_key(snap: _Snapshot, model: dict) -> bool:
    merged = {**(snap.providers.get(model.get("provider"), {}).get("api") or {}), **(model.get("api") or {})}
    return merged.get("requires_key", True)


def _key_value(snap: _Snapshot, feature_key: str, model: dict) -> str:
    """The first key that is actually set, most specific first ("" when none is)."""
    for name in _key_env_names(snap, feature_key, model):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _problem(snap: _Snapshot, feature_key: str, ref: str) -> Optional[str]:
    """Why `ref` cannot serve `feature_key` right now (None = usable)."""
    model = snap.models.get(ref)
    if model is None:
        return f"Model '{ref}' does not exist in the registry."
    if ref in snap.model_errors:
        return f"Model configuration is invalid: {snap.model_errors[ref]}."
    if not model.get("enabled", True):
        return "Selected model is disabled."
    provider = snap.providers.get(model["provider"])
    if not provider.get("enabled", True):
        return "The provider of the selected model is disabled."
    feature = snap.features.get(feature_key) or {}
    missing = [c for c in feature.get("requires") or [] if c not in (model.get("capabilities") or [])]
    if missing:
        labels = {"vision": "vision (image)", "pdf": "PDF/document"}
        if len(missing) == 1 and missing[0] in labels:
            return f"Selected model does not support {labels[missing[0]]} input."
        return "Model is not compatible with this AI feature."
    if _requires_key(snap, model) and not _key_value(snap, feature_key, model):
        return "API key for this provider is not configured."
    return None


def _selection(snap: _Snapshot, overrides: Dict[str, dict], feature_key: str) -> Tuple[str, str, Optional[str]]:
    """(model_ref, source, detail). Raises AIConfigError if nothing valid is selected."""
    ov = overrides.get(feature_key)
    if ov:
        ref = snap.ref_for(ov["model_ref"])
        if ref is None:
            raise AIConfigError(
                f"The model '{ov['model_ref']}' assigned to this feature no longer exists in the registry. "
                f"Choose another model or reset it to the default."
            )
        return ref, "admin", None

    assign = snap.assignments.get(feature_key) or {}
    default_ref = snap.ref_for(assign["model"]) if assign.get("model") else None
    if default_ref is None:
        raise AIConfigError(f"No model is assigned to AI feature '{feature_key}'.")
    return default_ref, "registry", None


def resolve(feature_key: str) -> ResolvedModel:
    """The model a feature should call right now, ready for the client."""
    snap = _current()
    if feature_key not in snap.features:
        raise AIConfigError(f"Unknown AI feature '{feature_key}'.")
    ref, source, _ = _selection(snap, _refresh_overrides(), feature_key)
    problem = _problem(snap, feature_key, ref)
    if problem:
        raise AIConfigError(problem)

    model = snap.models[ref]
    provider = snap.providers[model["provider"]]
    return ResolvedModel(
        feature=feature_key,
        ref=ref,
        provider=model["provider"],
        provider_name=provider.get("display_name") or model["provider"],
        protocol=provider["protocol"],
        model_id=model["model_id"],
        display_name=model.get("display_name") or model["model_id"],
        capabilities=frozenset(model.get("capabilities") or []),
        limits=dict(model.get("limits") or {}),
        api={**(provider.get("api") or {}), **(model.get("api") or {})},
        options={**(provider.get("options") or {}), **(model.get("options") or {})},
        source=source,
        api_key=_key_value(snap, feature_key, model),
    )


# ─────────────────────────────────────────────
# Admin mutations
# ─────────────────────────────────────────────

def set_feature_model(feature_key: str, model_ref: str, admin_user_id: Optional[int]) -> None:
    """Persist an Admin override for one feature (and only that feature)."""
    snap = _current()
    if feature_key not in snap.features:
        raise AIConfigError(f"Unknown AI feature '{feature_key}'.")
    ref = snap.ref_for(model_ref)
    if ref is None:
        raise AIConfigError(f"Unknown model '{model_ref}'.")
    problem = _problem(snap, feature_key, ref)
    if problem:
        raise AIConfigError(problem)

    from app.db.ai import upsert_feature_assignment
    upsert_feature_assignment(feature_key, ref, admin_user_id)

    with _overrides_lock:
        _overrides[feature_key] = {"feature_key": feature_key, "model_ref": ref,
                                   "updated_by": admin_user_id,
                                   "updated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat()}


def clear_feature_model(feature_key: str) -> None:
    """Remove an Admin override so the feature returns to its registry default."""
    snap = _current()
    if feature_key not in snap.features:
        raise AIConfigError(f"Unknown AI feature '{feature_key}'.")
    from app.db.ai import delete_feature_assignment
    delete_feature_assignment(feature_key)
    with _overrides_lock:
        _overrides.pop(feature_key, None)


# ─────────────────────────────────────────────
# Admin overview (JSON-safe; never contains a key value)
# ─────────────────────────────────────────────

def _env_configured(name: Optional[str]) -> bool:
    return bool(name and os.environ.get(name, "").strip())


def _ui(block: Optional[dict], display_name: str) -> dict:
    block = block or {}
    words = [w for w in display_name.replace("-", " ").split() if w]
    monogram = "".join(w[0] for w in words[:2]).upper() or "AI"
    return {"logo": block.get("logo"), "icon": block.get("icon"),
            "color": block.get("color"), "website": block.get("website"), "monogram": monogram}


def _model_ui(block: Optional[dict]) -> dict:
    """A model's own branding. Deliberately no initials fallback and no provider
    fallback: a model without a logo simply shows none."""
    block = block or {}
    return {"logo": block.get("logo"), "icon": block.get("icon"),
            "color": block.get("color"), "website": block.get("website")}


def _modalities(snap: _Snapshot, caps: List[str]) -> dict:
    inputs, outputs = [], []
    for c in caps:
        spec = snap.capabilities.get(c) or {}
        inputs += [x for x in spec.get("input") or [] if x not in inputs]
        outputs += [x for x in spec.get("output") or [] if x not in outputs]
    return {"input": inputs, "output": outputs}


def _rate_limits_view(snap: _Snapshot, ref: str) -> Optional[dict]:
    """A model's documented quotas, in display order, containing only the
    fields that apply to it — None when nothing is published/recorded."""
    entry = snap.rate_limits.get(ref)
    if not entry:
        return None
    rows = [{"key": name, "label": spec.get("label") or name, "abbr": spec.get("abbr") or name,
             "value": entry["limits"][name]}
            for name, spec in snap.rate_limit_fields.items() if name in entry["limits"]]
    if not rows:
        return None
    return {"tier": entry.get("tier"), "scope": entry.get("scope"), "source": entry.get("source"),
            "as_of": entry.get("as_of"), "note": entry.get("note"), "limits": rows}


def _provider_key_status(snap: _Snapshot, pkey: str, prov: dict) -> str:
    """configured = the provider's default key env var is set; feature_scoped =
    no default key, but at least one feature has its own dedicated key for this
    provider set; missing = neither."""
    api = prov.get("api") or {}
    if not api.get("requires_key", True) or _env_configured(api.get("key_env")):
        return "configured"
    scoped = [(a.get("credential_env") or {}).get(pkey) for a in snap.assignments.values()]
    return "feature_scoped" if any(_env_configured(e) for e in scoped) else "missing"


def public_identity(feature_key: str) -> Optional[dict]:
    """Which provider and model a feature uses right now, as data that is safe to show to anyone signed in
    (names, logos, capabilities). Built from the same snapshot and Admin choices the runtime uses, so a
    label can never disagree with what really serves requests. Never contains a key, a setting name or an
    error text. Provider and model branding are separate: a model without a logo simply has none.

    None when the feature does not exist. `available` is False when the feature cannot be served right now."""
    snap = _current()
    feat = snap.features.get(feature_key)
    if feat is None:
        return None
    base = {"feature": feature_key, "feature_name": feat.get("display_name") or feature_key,
            "available": False, "source": None, "provider": None, "model": None}
    try:
        ref, source, _ = _selection(snap, _refresh_overrides(), feature_key)
    except AIConfigError:
        return base
    model = snap.models.get(ref)
    if model is None:
        return base
    pkey = model.get("provider")
    prov = snap.providers.get(pkey, {})
    pname = prov.get("display_name") or pkey
    pui = _ui(prov.get("ui"), pname)
    mui = _model_ui(model.get("ui"))
    base.update(
        available=_problem(snap, feature_key, ref) is None,
        source=source,
        provider={"key": pkey, "name": pname, "logo": pui["logo"], "monogram": pui["monogram"], "color": pui["color"]},
        model={"key": model.get("model"), "ref": ref, "name": model.get("display_name") or model.get("model_id") or ref,
               "model_id": model.get("model_id"), "logo": mui["logo"], "color": mui["color"],
               "capabilities": list(model.get("capabilities") or [])},
    )
    return base


def describe() -> dict:
    """Everything the Admin AI Configuration page renders, built from the
    same snapshot/overrides the runtime uses — so the page cannot drift from
    what actually serves requests."""
    snap = _current()
    overrides = _refresh_overrides()

    feature_states: Dict[str, dict] = {}
    for fkey in snap.features:
        state = {"ref": None, "source": None, "detail": None, "error": None}
        try:
            ref, source, detail = _selection(snap, overrides, fkey)
            state.update(ref=ref, source=source, detail=detail)
            state["error"] = _problem(snap, fkey, ref)
        except AIConfigError as e:
            state["error"] = str(e)
            ov = overrides.get(fkey)
            if ov:
                state.update(ref=ov["model_ref"], source="admin")
        feature_states[fkey] = state

    used_by: Dict[str, List[str]] = {}
    for fkey, st in feature_states.items():
        if st["ref"]:
            used_by.setdefault(st["ref"], []).append(fkey)

    def model_view(ref: str, model: dict) -> dict:
        prov = snap.providers.get(model.get("provider"), {})
        caps = list(model.get("capabilities") or [])
        default_env = (model.get("api") or {}).get("key_env") or (prov.get("api") or {}).get("key_env")
        default_ok = (not _requires_key(snap, model)) or _env_configured(default_env)
        scoped = []
        for fkey, assign in snap.assignments.items():
            env = (assign.get("credential_env") or {}).get(model.get("provider"))
            if env:
                scoped.append({"feature": fkey, "configured": _env_configured(env)})
        if default_ok:
            key_status = "configured"
        elif any(s["configured"] for s in scoped):
            key_status = "feature_scoped"
        else:
            key_status = "missing"
        return {
            "ref": ref,
            "provider": model.get("provider"),
            "provider_name": prov.get("display_name") or model.get("provider"),
            "protocol": prov.get("protocol"),
            "model_id": model.get("model_id"),
            "display_name": model.get("display_name") or model.get("model_id") or ref,
            "description": model.get("description"),
            "enabled": bool(model.get("enabled", True)),
            "capabilities": caps,
            "modalities": _modalities(snap, caps),
            "limits": model.get("limits") or {},
            "rate_limits": _rate_limits_view(snap, ref),
            "aliases": model.get("aliases") or [],
            "model": model.get("model"),
            "ui": _model_ui(model.get("ui")),
            "key_default_configured": default_ok,
            "key_status": key_status,
            "feature_keys": scoped,
            "used_by": sorted(used_by.get(ref, [])),
            "error": snap.model_errors.get(ref),
        }

    models = [model_view(ref, m) for ref, m in snap.models.items()]

    features = []
    for fkey, feat in snap.features.items():
        st = feature_states[fkey]
        requires = list(feat.get("requires") or [])
        assign = snap.assignments.get(fkey) or {}
        default_ref = snap.ref_for(assign["model"]) if assign.get("model") else None
        options = []
        for ref, m in snap.models.items():
            caps = set(m.get("capabilities") or [])
            if not set(requires) <= caps or not m.get("enabled", True) or ref in snap.model_errors:
                continue     # incompatible/disabled models are never offered
            if not snap.providers.get(m["provider"], {}).get("enabled", True):
                continue
            reason = _problem(snap, fkey, ref)
            options.append({"ref": ref, "selectable": reason is None, "reason": reason})
        ov = overrides.get(fkey)
        source_labels = {"admin": "Admin selection",
                         "registry": "Registry default (ai_models.json)"}
        active = snap.models.get(st["ref"]) if st["ref"] else None
        key_configured = (None if active is None or "provider" not in active else
                          (not _requires_key(snap, active)) or bool(_key_value(snap, fkey, active)))
        features.append({
            "key": fkey,
            "group": feat.get("group"),
            "display_name": feat.get("display_name") or fkey,
            "description": feat.get("description"),
            "requires": requires,
            "active_model": st["ref"],
            "key_configured": key_configured,
            "source": st["source"],
            "source_label": source_labels.get(st["source"]),
            "error": st["error"],
            "status": "error" if st["error"] else "active",
            "default_model": default_ref,
            "has_override": bool(ov),
            "updated_at": ov.get("updated_at") if ov else None,
            "options": options,
        })

    active_refs = {f["active_model"] for f in features if f["active_model"] and not f["error"]}
    providers = []
    for pkey, prov in snap.providers.items():
        api = prov.get("api") or {}
        providers.append({
            "key": pkey,
            "display_name": prov.get("display_name") or pkey,
            "protocol": prov.get("protocol"),
            "enabled": bool(prov.get("enabled", True)),
            "ui": _ui(prov.get("ui"), prov.get("display_name") or pkey),
            "key_status": _provider_key_status(snap, pkey, prov),
            "models": [m["ref"] for m in models if m["provider"] == pkey],
            "in_use": any(m["ref"] in active_refs and m["provider"] == pkey for m in models),
        })

    groups = sorted(
        ({"key": k, **{f: v.get(f) for f in ("display_name", "description", "icon", "order")}}
         for k, v in snap.groups.items()),
        key=lambda g: (g.get("order") or 999, g["key"]),
    )
    active_provider_keys = {m["provider"] for m in models if m["ref"] in active_refs}

    return {
        "schema_version": SUPPORTED_SCHEMA_VERSION,
        "config_file": os.path.basename(config.AI_MODELS_CONFIG_PATH),
        "loaded_at": datetime.fromtimestamp(snap.loaded_at, timezone.utc).isoformat(),
        "cache_ttl_seconds": _TTL,
        "reload_error": _last_reload_error,
        "overrides_error": bool(_overrides_error),        # detail stays in the server log only
        "issues": snap.issues,
        "protocols": [{"key": k, "label": get_adapter(k).label} for k in list_protocols()],
        "capabilities": {k: {"label": v.get("label") or k, "icon": v.get("icon")}
                         for k, v in snap.capabilities.items()},
        "summary": {
            "features": len(features),
            "active_models": len(active_refs),
            "providers_in_use": len(active_provider_keys),
            "providers": len(providers),
            "models": len(models),
            "issues": len([i for i in snap.issues if i["level"] == "error"]) + sum(1 for f in features if f["error"]),
        },
        "groups": groups,
        "providers": providers,
        "models": models,
        "features": features,
    }
