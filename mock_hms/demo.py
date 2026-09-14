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
    """Force two active appointments onto one slot.

    Deliberately bypasses `book_appointment` and writes straight to the table,
    because the whole point of the guards there is that this cannot happen
    through the normal path. The partial unique index still blocks a true
    duplicate, so the plant instead moves a second appointment onto a booked
    slot with the index temporarily bypassed via a direct UPDATE.
    """
    blocked = _guard()
    if blocked:
        return blocked

    booked = (
        Appointment.query.filter(
            Appointment.status.in_(Appointment.ACTIVE_STATUSES),
            Appointment.appointment_datetime > utcnow() + timedelta(hours=24),
        )
        .order_by(Appointment.appointment_datetime)
        .first()
    )
    if booked is None:
        return err("VALIDATION_ERROR", "No future appointment to collide with")

    victim = (
        Appointment.query.filter(
            Appointment.appointment_id != booked.appointment_id,
            Appointment.status.in_(Appointment.ACTIVE_STATUSES),
            Appointment.slot_id != booked.slot_id,
        )
        .order_by(Appointment.appointment_id.desc())
        .first()
    )
    if victim is None:
        return err("VALIDATION_ERROR", "Need at least two active appointments")

    old_slot_id = victim.slot_id
    # Raw UPDATE: SQLAlchemy would honour the partial unique index on flush,
    # which is exactly the guard we are simulating a failure of.
    db.session.execute(
        db.text(
            "UPDATE appointment SET slot_id=:s, doctor_id=:d, department_id=:dep, "
            "appointment_datetime=:dt WHERE appointment_id=:a"
        ),
        {
            "s": booked.slot_id,
            "d": booked.doctor_id,
            "dep": booked.department_id,
            "dt": booked.appointment_datetime,
            "a": victim.appointment_id,
        },
    )
    old = db.session.get(Slot, old_slot_id)
    if old:
        old.status = "AVAILABLE"
        old.version += 1
    db.session.commit()

    c = svc.raise_conflict(
        "DOUBLE_BOOKING",
        severity="HIGH",
        appointment_id=booked.appointment_id,
        related_appointment_id=victim.appointment_id,
        slot_id=booked.slot_id,
        detected_by="HMS",
        detail=f"Planted for demo: {booked.reference_no} and {victim.reference_no} share slot {booked.slot_id}",
    )
    return jsonify(
        {
            "planted": True,
            "slot_id": booked.slot_id,
            "appointments": [booked.reference_no, victim.reference_no],
            "conflict_id": c.conflict_id,
        }
    )


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
