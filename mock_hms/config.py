"""Configuration for the Mock HMS.

Everything that differs between a laptop and Render lives here and is read
from the environment. Secrets are never defaulted to a real value.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent


class Config:
    # --- Core -------------------------------------------------------------
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-for-render")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", f"sqlite:///{BASE_DIR / 'hms.db'}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JSON_SORT_KEYS = False

    # --- API auth ---------------------------------------------------------
    # Bots send this as the X-API-Key header. Stored in Orchestrator as the
    # HMS_ApiKey credential asset.
    API_KEY = os.environ.get("HMS_API_KEY", "dev-api-key-change-me")
    # Set REQUIRE_API_KEY=false locally to poke the API with curl.
    REQUIRE_API_KEY = os.environ.get("REQUIRE_API_KEY", "true").lower() == "true"

    # --- Business rules ---------------------------------------------------
    RULES_PATH = os.environ.get(
        "RULES_PATH", str(REPO_ROOT / "docs" / "phase2" / "rules.yaml")
    )

    # --- Slot locking -----------------------------------------------------
    LOCK_TTL_SECONDS = int(os.environ.get("LOCK_TTL_SECONDS", "120"))

    # --- Orchestrator queue push -----------------------------------------
    # When any of these is missing the HMS degrades gracefully: booking
    # requests are persisted with queue_status=PENDING_PUSH and retried,
    # so the form still works with no Orchestrator tenant configured.
    UIPATH_ORCH_URL = os.environ.get(
        "UIPATH_ORCH_URL", "https://cloud.uipath.com"
    )
    UIPATH_ORG_ID = os.environ.get("UIPATH_ORG_ID", "")
    UIPATH_TENANT = os.environ.get("UIPATH_TENANT", "")
    UIPATH_CLIENT_ID = os.environ.get("UIPATH_CLIENT_ID", "")
    UIPATH_CLIENT_SECRET = os.environ.get("UIPATH_CLIENT_SECRET", "")
    UIPATH_FOLDER_ID = os.environ.get("UIPATH_FOLDER_ID", "")
    UIPATH_BOOKING_QUEUE = os.environ.get("UIPATH_BOOKING_QUEUE", "BookingRequests")
    UIPATH_CONFLICT_QUEUE = os.environ.get("UIPATH_CONFLICT_QUEUE", "Conflicts")
    QUEUE_PUSH_ENABLED = os.environ.get("QUEUE_PUSH_ENABLED", "false").lower() == "true"

    # --- Seed / demo ------------------------------------------------------
    SEED_PATIENTS = int(os.environ.get("SEED_PATIENTS", "50"))
    SEED_HORIZON_DAYS = int(os.environ.get("SEED_HORIZON_DAYS", "30"))
    ALLOW_DEMO_ENDPOINTS = os.environ.get("ALLOW_DEMO_ENDPOINTS", "true").lower() == "true"

    HOSPITAL_NAME = os.environ.get("HOSPITAL_NAME", "Meridian General Hospital")
    TIMEZONE = os.environ.get("TZ", "Asia/Kolkata")

    # --- Excel mirror -----------------------------------------------------
    EXCEL_PATH = os.environ.get(
        "EXCEL_PATH", str(REPO_ROOT / "data" / "appointments.xlsx")
    )
