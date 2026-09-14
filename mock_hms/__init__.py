"""Mock HMS — the hospital system the RPA bots automate against.

Two surfaces on purpose:
  * UI screens  (mock_hms/views.py) — what the bots click, type into and scrape
  * REST API    (mock_hms/api.py)   — what they call for locks, bulk reads and
                                      set-based queries a screen cannot express

See docs/phase1/hms_interfaces.md for the contract both implement.
"""
from __future__ import annotations

import logging
import os

from flask import Flask, jsonify

from .config import Config
from .models import db


def create_app(config_object=Config, **overrides) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)
    app.config.update(overrides)

    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    db.init_app(app)

    from .views import ui
    from .api import api
    from .demo import demo

    app.register_blueprint(ui)
    app.register_blueprint(api)
    app.register_blueprint(demo)

    @app.errorhandler(404)
    def not_found(e):
        if _wants_json():
            return jsonify(
                {"error_code": "NOT_FOUND", "message": "No such endpoint", "retryable": False}
            ), 404
        return "Not found", 404

    @app.errorhandler(500)
    def server_error(e):
        app.logger.exception("Unhandled error")
        if _wants_json():
            # INTERNAL_ERROR is retryable — the bot should back off and retry,
            # not fail the transaction as a business exception.
            return jsonify(
                {"error_code": "INTERNAL_ERROR", "message": "Unhandled server error",
                 "retryable": True}
            ), 500
        return "Internal server error", 500

    @app.context_processor
    def inject_globals():
        from .rules import RULES

        return {"rules": RULES, "hospital_name": app.config["HOSPITAL_NAME"]}

    with app.app_context():
        db.create_all()

    return app


def _wants_json() -> bool:
    from flask import request

    return request.path.startswith("/api/") or request.accept_mimetypes.best == "application/json"
