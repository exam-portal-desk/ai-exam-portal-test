"""
main.py
Thin application entry point.
All configuration, blueprints, and initialization live in app/ and config.py.
"""

from gevent import monkey
monkey.patch_all()

from app import create_app, socketio
import config

app = create_app()

if __name__ == "__main__":
    print(f"🚀 Starting ExamPortal ({'Production' if config.IS_PRODUCTION else 'Development'})...")
    socketio.run(
        app,
        debug=config.DEBUG,
        host="0.0.0.0",
        port=int(__import__("os").environ.get("PORT", 5000)),
    )