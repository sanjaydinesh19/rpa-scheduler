"""Pushes booking requests into a UiPath Orchestrator queue.

Degrades on purpose. If Orchestrator is unreachable or unconfigured, the
request is already persisted with queue_status=PENDING_PUSH and a background
sweep retries it. A patient's booking request is never lost because a cloud
tenant was briefly down — which is the same argument for using a queue in the
first place, applied one layer up.
"""
from __future__ import annotations

import logging
import threading
import time as _time
from datetime import datetime, timedelta

import requests

log = logging.getLogger(__name__)

_token_cache = {"access_token": None, "expires_at": datetime.min}
_token_lock = threading.Lock()


class OrchestratorError(Exception):
    pass


def _get_token(cfg) -> str:
    """Client-credentials token, cached until 60s before expiry."""
    with _token_lock:
        if _token_cache["access_token"] and _token_cache["expires_at"] > datetime.utcnow():
            return _token_cache["access_token"]

        resp = requests.post(
            f"{cfg['orch_url']}/identity_/connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "scope": "OR.Queues OR.Jobs",
            },
            timeout=20,
        )
        if resp.status_code != 200:
            raise OrchestratorError(f"Token request failed: {resp.status_code} {resp.text[:200]}")

        body = resp.json()
        _token_cache["access_token"] = body["access_token"]
        _token_cache["expires_at"] = datetime.utcnow() + timedelta(
            seconds=int(body.get("expires_in", 3600)) - 60
        )
        return _token_cache["access_token"]


def add_queue_item(cfg: dict, queue_name: str, payload: dict, reference: str | None = None) -> str:
    """Returns the Orchestrator queue item id. Raises OrchestratorError."""
    token = _get_token(cfg)
    url = (
        f"{cfg['orch_url']}/{cfg['org_id']}/{cfg['tenant']}"
        "/orchestrator_/odata/Queues/UiPathODataSvc.AddQueueItem"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "X-UIPATH-OrganizationUnitId": str(cfg["folder_id"]),
        "Content-Type": "application/json",
    }
    body = {
        "itemData": {
            "Name": queue_name,
            "Priority": "Normal",
            "SpecificContent": payload,
            "Reference": reference or payload.get("RequestId"),
        }
    }

    resp = requests.post(url, json=body, headers=headers, timeout=30)
    if resp.status_code not in (200, 201):
        raise OrchestratorError(f"AddQueueItem failed: {resp.status_code} {resp.text[:300]}")
    return str(resp.json().get("Id"))


def push_booking_request(app, booking_request) -> tuple[bool, str | None]:
    """Push one BookingRequest. Returns (ok, error_message).

    Called inline by the booking form. Never raises into the request handler —
    a failed push must not turn into a 500 for the patient.
    """
    from .models import db

    cfg = _cfg_from_app(app)
    if not cfg:
        booking_request.queue_status = "PENDING_PUSH"
        booking_request.push_error = "Orchestrator not configured (QUEUE_PUSH_ENABLED=false)"
        db.session.commit()
        return False, booking_request.push_error

    try:
        item_id = add_queue_item(
            cfg,
            cfg["booking_queue"],
            booking_request.to_queue_payload(),
            reference=booking_request.reference,
        )
        booking_request.queue_status = "QUEUED"
        booking_request.queue_item_id = item_id
        booking_request.push_error = None
        db.session.commit()
        return True, None
    except Exception as exc:  # network, auth, Orchestrator 5xx
        log.warning("Queue push failed for %s: %s", booking_request.reference, exc)
        booking_request.queue_status = "PENDING_PUSH"
        booking_request.push_error = str(exc)[:255]
        db.session.commit()
        return False, str(exc)


def retry_pending_pushes(app, limit: int = 25) -> dict:
    """Background sweep for requests that failed their initial push."""
    from .models import db, BookingRequest

    with app.app_context():
        cfg = _cfg_from_app(app)
        if not cfg:
            return {"attempted": 0, "queued": 0, "skipped": "not configured"}

        pending = (
            BookingRequest.query.filter_by(queue_status="PENDING_PUSH")
            .order_by(BookingRequest.created_at)
            .limit(limit)
            .all()
        )
        queued = 0
        for br in pending:
            ok, _ = push_booking_request(app, br)
            if ok:
                queued += 1
            _time.sleep(0.2)  # be polite to the Orchestrator API
        return {"attempted": len(pending), "queued": queued}


def _cfg_from_app(app) -> dict | None:
    c = app.config
    if not c.get("QUEUE_PUSH_ENABLED"):
        return None
    required = ("UIPATH_ORG_ID", "UIPATH_TENANT", "UIPATH_CLIENT_ID", "UIPATH_CLIENT_SECRET")
    if not all(c.get(k) for k in required):
        return None
    return {
        "orch_url": c["UIPATH_ORCH_URL"].rstrip("/"),
        "org_id": c["UIPATH_ORG_ID"],
        "tenant": c["UIPATH_TENANT"],
        "client_id": c["UIPATH_CLIENT_ID"],
        "client_secret": c["UIPATH_CLIENT_SECRET"],
        "folder_id": c["UIPATH_FOLDER_ID"],
        "booking_queue": c["UIPATH_BOOKING_QUEUE"],
        "conflict_queue": c["UIPATH_CONFLICT_QUEUE"],
    }
