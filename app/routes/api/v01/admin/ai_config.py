"""
app/routes/api/v01/admin/ai_config.py
Admin AI Configuration JSON API (v01). Reads and writes the same registry
state the AI runtime resolves models from (app/services/ai/registry.py) —
this module holds no configuration of its own.

  GET    /api/v01/admin/ai-config                              overview (providers, models, features)
  PUT    /api/v01/admin/ai-config/features/<key>/model         {"model": "<ref>"}  override one feature
  DELETE /api/v01/admin/ai-config/features/<key>/model         back to the registry default
  POST   /api/v01/admin/ai-config/reload                       re-read ai_models.json now
  GET    /api/v01/admin/ai-config/guide                        the guide (config/AI_MODELS.md) as markdown text

No response ever contains an API key value — only whether one is configured.
"""

from flask import jsonify, request, session

from app.routes.api.v01.admin import admin_api_bp
from app.middleware.session_guard import require_admin_permission
from app.services.ai import AIConfigError, registry
from app.services.ai.guide import read_guide

AI_CONFIG_PERMISSION = "ai_configuration"

_SAVE_FAILED = ("Could not save the selection. If this is a new deployment, make sure the "
                "AI-configuration database migration has been applied.")


def _overview_response(status: int = 200, **extra):
    try:
        return jsonify({"success": True, "data": registry.describe(), **extra}), status
    except AIConfigError as e:
        return jsonify({"success": False, "message": str(e)}), 500


@admin_api_bp.route("/ai-config", methods=["GET"])
@require_admin_permission(AI_CONFIG_PERMISSION)
def api_ai_config_overview():
    return _overview_response()


@admin_api_bp.route("/ai-config/features/<feature_key>/model", methods=["PUT"])
@require_admin_permission(AI_CONFIG_PERMISSION)
def api_ai_config_set_feature_model(feature_key):
    body = request.get_json(silent=True) or {}
    model_ref = str(body.get("model") or "").strip()
    if not model_ref:
        return jsonify({"success": False, "message": "A model is required."}), 400
    try:
        registry.set_feature_model(feature_key, model_ref, session.get("user_id"))
    except AIConfigError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        print(f"[ai_config] set_feature_model failed: {e}")
        return jsonify({"success": False, "message": _SAVE_FAILED}), 500
    return _overview_response()


@admin_api_bp.route("/ai-config/features/<feature_key>/model", methods=["DELETE"])
@require_admin_permission(AI_CONFIG_PERMISSION)
def api_ai_config_reset_feature_model(feature_key):
    try:
        registry.clear_feature_model(feature_key)
    except AIConfigError as e:
        return jsonify({"success": False, "message": str(e)}), 400
    except Exception as e:
        print(f"[ai_config] clear_feature_model failed: {e}")
        return jsonify({"success": False, "message": _SAVE_FAILED}), 500
    return _overview_response()


@admin_api_bp.route("/ai-config/reload", methods=["POST"])
@require_admin_permission(AI_CONFIG_PERMISSION)
def api_ai_config_reload():
    try:
        ok, error = registry.reload()
    except AIConfigError as e:
        return jsonify({"success": False, "message": str(e)}), 500
    if not ok:
        return jsonify({"success": False,
                        "message": f"ai_models.json was not reloaded — the previous configuration is still active. {error}"}), 400
    return _overview_response()


@admin_api_bp.route("/ai-config/guide", methods=["GET"])
@require_admin_permission(AI_CONFIG_PERMISSION)
def api_ai_config_guide():
    """The single, human-maintained guide document. The page renders it; nothing
    instructional is stored in ai_models.json."""
    text = read_guide()
    if text is None:
        return jsonify({"success": False, "message": "The guide is not available right now."}), 404
    return jsonify({"success": True, "data": {"markdown": text}})
