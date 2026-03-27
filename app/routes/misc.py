"""
app/routes/misc.py
Miscellaneous routes: home, footer pages, debug endpoints.
"""

from flask import Blueprint, render_template, jsonify, session
import os

misc_bp = Blueprint("misc", __name__)


@misc_bp.route("/")
def home():
    return render_template("index.html")


# Footer / static info pages
for _name, _path in [
    ("privacy_policy",  "privacy_policy.html"),
    ("terms_of_service","terms_of_service.html"),
    ("support",         "support.html"),
    ("contact",         "contact.html"),
    ("about",           "about.html"),
]:
    def _make_view(template):
        def _view():
            return render_template(template)
        return _view

    misc_bp.add_url_rule(
        f"/{_name.replace('_','-')}",
        endpoint=_name,
        view_func=_make_view(_path),
    )


@misc_bp.route("/debug/env-check")
def debug_env_check():
    import json
    from app.services.drive_service import get_drive

    env_status = {}
    for var in ["SECRET_KEY","GOOGLE_SERVICE_ACCOUNT_JSON","SUPABASE_URL","SUPABASE_KEY"]:
        val = os.environ.get(var)
        if val:
            if var == "GOOGLE_SERVICE_ACCOUNT_JSON":
                try:
                    d = json.loads(val) if val.strip().startswith("{") else json.load(open(val))
                    env_status[var] = {"status": "Valid JSON", "client_email": d.get("client_email","?")}
                except Exception as e:
                    env_status[var] = {"status": f"Invalid: {e}"}
            else:
                env_status[var] = {"status": "Present"}
        else:
            env_status[var] = {"status": "MISSING"}

    drive = get_drive()
    drive_status = "Not initialized"
    if drive:
        try:
            about = drive.about().get(fields="user").execute()
            drive_status = f"Connected as: {about.get('user',{}).get('emailAddress','?')}"
        except Exception as e:
            drive_status = f"Service exists but test failed: {e}"

    return jsonify({"environment": env_status, "drive": drive_status})


@misc_bp.route("/debug/service-status")
def debug_service_status():
    from app.services.drive_service import get_drive
    drive = get_drive()
    test = "Not initialized"
    if drive:
        try:
            about = drive.about().get(fields="user").execute()
            test = f"Connected as: {about.get('user',{}).get('emailAddress','?')}"
        except Exception as e:
            test = f"Error: {e}"
    return jsonify({"drive_initialized": drive is not None, "drive_test": test})