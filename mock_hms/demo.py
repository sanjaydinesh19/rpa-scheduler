"""Demo and reset endpoints.

These exist so the Phase 4 demo is reproducible: reset to a known state, plant
a conflict on demand, then watch the bots resolve it. Gated behind
ALLOW_DEMO_ENDPOINTS so a deployment can switch them off.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request, current_app

from .models import db, Slot, Appointment, ConflictLog, Patient, utcnow
from . import services as svc
from .api import require_api_key, err

demo = Blueprint("demo", __name__, url_prefix="/api/v1/admin")


def _guard():
    if not current_app.config.get("ALLOW_DEMO_ENDPOINTS"):
        return err("UNAUTHORIZED", "Demo endpoints are disabled")
    return None


@demo.post("/reseed")
@require_api_key
def reseed():
    """Drop and rebuild the whole database from seed.py. Destructive."""
    blocked = _guard()
    if blocked:
        return blocked

    from .seed import seed_all

    db.drop_all()
    db.create_all()
    summary = seed_all(current_app)
    return jsonify({"reseeded": True, **summary})


@demo.post("/plant-double-booking")
@require_api_key
def plant_double_booking():
    """Create a real, reachable double-booking on demand.

    Delegates to the same function the seed uses. It deliberately does *not*
    try to put two appointments on one slot: the partial unique index refuses
    that even from raw SQL, which is the guard working. What it plants instead
    is an overlapping slot on the same doctor's calendar — two individually
    legal rows, one doctor, one moment in time — which is the double-booking
    shape no index can prevent and the Conflict Bot actually has to resolve.
    """
    blocked = _guard()
    if blocked:
        return blocked

    from .seed import plant_double_booking as plant

    result = plant()
    if result is None:
        return err(
            "VALIDATION_ERROR",
            "Could not plant a conflict — need a future appointment and a free overlapping time",
        )
    return jsonify({"planted": True, **result})


@demo.post("/plant-unavailability")
@require_api_key
def plant_unavailability():
    """Mark the busiest future afternoon unavailable — the cascade demo."""
    blocked = _guard()
    if blocked:
        return blocked

    d = request.get_json(silent=True) or {}
    hours_ahead = int(d.get("hours_ahead", 48))
    target = utcnow() + timedelta(hours=hours_ahead)

    appt = (
        Appointment.query.filter(
            Appointment.appointment_datetime >= target,
            Appointment.status.in_(Appointment.ACTIVE_STATUSES),
        )
        .order_by(Appointment.appointment_datetime)
        .first()
    )
    if appt is None:
        return err("VALIDATION_ERROR", "No appointment far enough ahead to disrupt")

    day = appt.appointment_datetime.date()
    frm = datetime.combine(day, datetime.min.time()) + timedelta(hours=13)
    to = datetime.combine(day, datetime.min.time()) + timedelta(hours=18)

    from .models import DoctorUnavailability

    u = DoctorUnavailability(
        doctor_id=appt.doctor_id,
        from_datetime=frm,
        to_datetime=to,
        reason="Emergency surgery (planted for demo)",
        created_by="demo",
    )
    db.session.add(u)
    db.session.commit()

    affected = svc.appointments_affected_by(u)
    c = svc.raise_conflict(
        "DOCTOR_UNAVAILABLE",
        severity="HIGH",
        detected_by="HMS",
        detail=f"Doctor {u.doctor_id} unavailable {frm} to {to}; {len(affected)} affected",
    )
    return jsonify(
        {
            "planted": True,
            "unavailability_id": u.unavailability_id,
            "doctor_id": u.doctor_id,
            "from": frm.isoformat(),
            "to": to.isoformat(),
            "affected_appointments": [a.reference_no for a in affected],
            "conflict_id": c.conflict_id,
        }
    )


@demo.post("/clear-conflicts")
@require_api_key
def clear_conflicts():
    blocked = _guard()
    if blocked:
        return blocked
    n = ConflictLog.query.filter_by(status="OPEN").update(
        {"status": "RESOLVED", "resolution": "NO_ACTION", "resolved_at": utcnow()}
    )
    db.session.commit()
    return jsonify({"cleared": n})


@demo.get("/stats")
@require_api_key
def stats():
    """One call that gives the Phase 4 report its numbers."""
    from .models import Notification, BookingRequest

    total = Appointment.query.filter(
        Appointment.status.in_(Appointment.ACTIVE_STATUSES)
    ).count()
    by_bot = Appointment.query.filter(
        Appointment.source == "BOT", Appointment.status.in_(Appointment.ACTIVE_STATUSES)
    ).count()
    return jsonify(
        {
            "appointments_active": total,
            "booked_by_bot": by_bot,
            "bot_share_pct": round(100 * by_bot / total, 1) if total else 0,
            "notifications_sent": Notification.query.filter_by(status="SENT").count(),
            "notifications_failed": Notification.query.filter_by(status="FAILED").count(),
            "conflicts_open": ConflictLog.query.filter_by(status="OPEN").count(),
            "conflicts_resolved": ConflictLog.query.filter_by(status="RESOLVED").count(),
            "conflicts_escalated": ConflictLog.query.filter_by(status="ESCALATED").count(),
            "requests_total": BookingRequest.query.count(),
            "requests_pending_push": BookingRequest.query.filter_by(
                queue_status="PENDING_PUSH"
            ).count(),
            "unsynced_to_excel": Appointment.query.filter(
                Appointment.synced_to_excel_at.is_(None),
                Appointment.status.in_(Appointment.ACTIVE_STATUSES),
            ).count(),
        }
    )
