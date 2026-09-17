"""REST API — /api/v1

Implements docs/phase1/hms_interfaces.md Part B. Every error uses the same
envelope so a bot branches on `error_code` and never parses prose:

    {"error_code": "SLOT_TAKEN", "message": "...", "retryable": true}
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from functools import wraps

from flask import Blueprint, jsonify, request, current_app

from .models import (
    db,
    Department,
    Doctor,
    Patient,
    Slot,
    Appointment,
    Notification,
    DoctorUnavailability,
    AppointmentAudit,
    ConflictLog,
    BookingRequest,
    utcnow,
)
from .rules import RULES
from . import services as svc
from .orchestrator_client import push_booking_request

api = Blueprint("api", __name__, url_prefix="/api/v1")

# error_code -> (http status, retryable)
ERROR_MAP = {
    "SLOT_TAKEN": (409, True),
    "SLOT_LOCKED": (423, True),
    "SLOT_BLOCKED": (409, False),
    "SLOT_NOT_FOUND": (404, False),
    "VALIDATION_ERROR": (400, False),
    "PATIENT_NOT_FOUND": (404, False),
    "APPOINTMENT_NOT_FOUND": (404, False),
    "DUPLICATE_PHONE": (409, False),
    "DUPLICATE_NOTIFICATION": (409, False),
    "DAILY_CAP_REACHED": (409, False),
    "DOCTOR_INACTIVE": (409, False),
    "TOO_CLOSE_TO_RESCHEDULE": (409, False),
    "URGENT_NEVER_AUTO_MOVED": (409, False),
    "AMBIGUOUS_PRIORITY": (409, False),
    "UNAUTHORIZED": (401, False),
    "INTERNAL_ERROR": (500, True),
}


def err(code: str, message: str | None = None):
    status, retryable = ERROR_MAP.get(code, (400, False))
    return (
        jsonify({"error_code": code, "message": message or code, "retryable": retryable}),
        status,
    )


def require_api_key(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_app.config.get("REQUIRE_API_KEY"):
            return fn(*a, **kw)
        if request.headers.get("X-API-Key") != current_app.config["API_KEY"]:
            return err("UNAUTHORIZED", "Missing or invalid X-API-Key header")
        return fn(*a, **kw)

    return wrapper


def _date_arg(name, default=None):
    v = request.args.get(name)
    if not v:
        return default
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return default


def _dt_arg(name, default=None):
    v = request.args.get(name)
    if not v:
        return default
    try:
        return datetime.fromisoformat(v.replace("Z", ""))
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------
@api.get("/health")
def health():
    """Unauthenticated — Render pings this."""
    return jsonify(
        {
            "status": "ok",
            "time": utcnow().isoformat(),
            "rules_version": RULES.version,
            "counts": {
                "departments": Department.query.count(),
                "doctors": Doctor.query.count(),
                "patients": Patient.query.count(),
                "slots": Slot.query.count(),
                "appointments": Appointment.query.count(),
                "open_conflicts": ConflictLog.query.filter_by(status="OPEN").count(),
            },
        }
    )


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
@api.get("/departments")
@require_api_key
def list_departments():
    return jsonify([d.to_dict() for d in Department.query.order_by(Department.code)])


@api.get("/doctors")
@require_api_key
def list_doctors():
    q = Doctor.query
    if request.args.get("department"):
        q = q.join(Department).filter(Department.code == request.args["department"])
    if request.args.get("is_active"):
        q = q.filter(Doctor.is_active.is_(request.args["is_active"].lower() == "true"))
    return jsonify([d.to_dict() for d in q.order_by(Doctor.doctor_id)])


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------
@api.get("/patients")
@require_api_key
def get_patients():
    phone = request.args.get("phone")
    if phone:
        p = svc.find_patient_by_phone(phone)
        return jsonify([p.to_dict()] if p else [])
    name = request.args.get("name")
    q = Patient.query
    if name:
        q = q.filter(Patient.full_name.ilike(f"%{name}%"))
    return jsonify([p.to_dict() for p in q.limit(100)])


@api.get("/patients/<int:patient_id>")
@require_api_key
def get_patient(patient_id):
    p = db.session.get(Patient, patient_id)
    return jsonify(p.to_dict()) if p else err("PATIENT_NOT_FOUND")


@api.post("/patients")
@require_api_key
def create_patient():
    d = request.get_json(silent=True) or {}
    dob = None
    if d.get("date_of_birth"):
        try:
            dob = date.fromisoformat(d["date_of_birth"][:10])
        except ValueError:
            return err("VALIDATION_ERROR", "date_of_birth must be YYYY-MM-DD")

    p, e = svc.register_patient(
        full_name=d.get("full_name", ""),
        phone=d.get("phone", ""),
        email=d.get("email"),
        date_of_birth=dob,
        gender=d.get("gender", "O"),
        preferred_channel=d.get("preferred_channel", "SMS"),
    )
    return err(e) if e else (jsonify(p.to_dict()), 201)


@api.patch("/patients/<int:patient_id>")
@require_api_key
def update_patient(patient_id):
    """Used by the Reminder Bot to honour a Twilio 21610 unsubscribe."""
    p = db.session.get(Patient, patient_id)
    if p is None:
        return err("PATIENT_NOT_FOUND")
    d = request.get_json(silent=True) or {}
    for field in ("sms_consent", "email_consent", "preferred_channel", "email", "phone"):
        if field in d:
            setattr(p, field, d[field])
    db.session.commit()
    return jsonify(p.to_dict())


# ---------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------
@api.get("/slots")
@require_api_key
def get_slots():
    # date_to defaults to a window *after date_from*, not after today. Anchoring
    # it to today meant a caller searching from a future date silently got zero
    # slots back and concluded NO_SLOT_AVAILABLE.
    d_from = _date_arg("date_from", date.today())
    d_to = _date_arg("date_to", d_from + timedelta(days=RULES.default_search_window_days))

    slots = svc.search_slots(
        department_code=request.args.get("department"),
        doctor_id=request.args.get("doctor_id", type=int),
        date_from=d_from,
        date_to=d_to,
        status=request.args.get("status", "AVAILABLE") or None,
        include_emergency_reserve=request.args.get("include_reserve", "false").lower() == "true",
        limit=request.args.get("limit", 500, type=int),
    )
    return jsonify([s.to_dict() for s in slots])


@api.get("/slots/<int:slot_id>")
@require_api_key
def get_slot(slot_id):
    """The pre-submit re-check. Returns `version` so the bot can compare."""
    s = db.session.get(Slot, slot_id)
    return jsonify(s.to_dict()) if s else err("SLOT_NOT_FOUND")


@api.post("/slots/<int:slot_id>/lock")
@require_api_key
def post_lock_slot(slot_id):
    d = request.get_json(silent=True) or {}
    s, e = svc.lock_slot(slot_id, d.get("locked_by", "SchedulingBot"))
    return err(e) if e else jsonify(s.to_dict())


@api.post("/slots/<int:slot_id>/release")
@require_api_key
def post_release_slot(slot_id):
    d = request.get_json(silent=True) or {}
    s, e = svc.release_slot(slot_id, d.get("locked_by"))
    return err(e) if e else jsonify(s.to_dict())


@api.patch("/slots/<int:slot_id>")
@require_api_key
def patch_slot(slot_id):
    s = db.session.get(Slot, slot_id)
    if s is None:
        return err("SLOT_NOT_FOUND")
    d = request.get_json(silent=True) or {}
    if "status" in d:
        if d["status"] not in Slot.STATUSES:
            return err("VALIDATION_ERROR", f"status must be one of {Slot.STATUSES}")
        s.status = d["status"]
        s.version += 1
    db.session.commit()
    return jsonify(s.to_dict())


# ---------------------------------------------------------------------------
# Appointments
# ---------------------------------------------------------------------------
@api.get("/appointments")
@require_api_key
def get_appointments():
    q = Appointment.query
    frm, to = _dt_arg("from"), _dt_arg("to")
    if frm:
        q = q.filter(Appointment.appointment_datetime >= frm)
    if to:
        q = q.filter(Appointment.appointment_datetime <= to)
    if request.args.get("status"):
        q = q.filter(Appointment.status == request.args["status"])
    if request.args.get("doctor_id", type=int):
        q = q.filter(Appointment.doctor_id == request.args.get("doctor_id", type=int))
    if request.args.get("unsynced", "").lower() == "true":
        q = q.filter(Appointment.synced_to_excel_at.is_(None))

    include_patient = request.args.get("include_patient", "false").lower() == "true"
    rows = q.order_by(Appointment.appointment_datetime).limit(
        request.args.get("limit", 500, type=int)
    )
    return jsonify([a.to_dict(include_patient=include_patient) for a in rows])


@api.get("/appointments/<reference_no>")
@require_api_key
def get_appointment(reference_no):
    a = Appointment.query.filter_by(reference_no=reference_no).first()
    if a is None and reference_no.isdigit():
        a = db.session.get(Appointment, int(reference_no))
    return jsonify(a.to_dict(include_patient=True)) if a else err("APPOINTMENT_NOT_FOUND")


@api.post("/appointments")
@require_api_key
def create_appointment():
    d = request.get_json(silent=True) or {}
    if not d.get("patient_id") or not d.get("slot_id"):
        return err("VALIDATION_ERROR", "patient_id and slot_id are required")

    a, e = svc.book_appointment(
        patient_id=d["patient_id"],
        slot_id=d["slot_id"],
        priority=d.get("priority", "NORMAL"),
        reason=d.get("reason"),
        is_follow_up=bool(d.get("is_follow_up")),
        queue_item_ref=d.get("queue_item_ref"),
        booked_by=d.get("booked_by", "SchedulingBot"),
        source=d.get("source", "BOT"),
        expected_version=d.get("expected_version"),
    )
    return err(e) if e else (jsonify(a.to_dict()), 201)


@api.patch("/appointments/<int:appointment_id>")
@require_api_key
def patch_appointment(appointment_id):
    a = db.session.get(Appointment, appointment_id)
    if a is None:
        return err("APPOINTMENT_NOT_FOUND")
    d = request.get_json(silent=True) or {}

    if "slot_id" in d:
        a2, e = svc.reschedule_appointment(
            appointment_id,
            d["slot_id"],
            actor=d.get("actor", "ConflictResolutionBot"),
            note=d.get("note"),
            force=bool(d.get("force")),
        )
        return err(e) if e else jsonify(a2.to_dict())

    if "status" in d:
        if d["status"] not in Appointment.STATUSES:
            return err("VALIDATION_ERROR", f"status must be one of {Appointment.STATUSES}")
        old = a.status
        a.status = d["status"]
        db.session.commit()
        svc.audit(a.appointment_id, "STATUS_CHANGE", d.get("actor", "api"), old, d["status"])

    if "synced_to_excel_at" in d:
        a.synced_to_excel_at = utcnow()
        db.session.commit()

    return jsonify(a.to_dict())


@api.post("/appointments/<int:appointment_id>/cancel")
@require_api_key
def post_cancel(appointment_id):
    d = request.get_json(silent=True) or {}
    a, e = svc.cancel_appointment(
        appointment_id, actor=d.get("actor", "api"), note=d.get("note")
    )
    if e:
        return err(e)

    # A cancellation is an opportunity, not just a deletion. Raise it so the
    # Conflict Bot can consider backfilling the freed slot.
    svc.raise_conflict(
        "CANCELLATION_CASCADE",
        severity="MEDIUM",
        appointment_id=a.appointment_id,
        slot_id=a.slot_id,
        detected_by="HMS",
        detail=f"Cancelled {a.reference_no}; slot {a.slot_id} freed",
    )
    return jsonify(a.to_dict())


@api.get("/appointments/<int:appointment_id>/audit")
@require_api_key
def get_audit(appointment_id):
    rows = (
        AppointmentAudit.query.filter_by(appointment_id=appointment_id)
        .order_by(AppointmentAudit.created_at)
        .all()
    )
    return jsonify([r.to_dict() for r in rows])


# ---------------------------------------------------------------------------
# Reminders and notifications
# ---------------------------------------------------------------------------
@api.get("/reminders/due")
@require_api_key
def reminders_due():
    """The Reminder Bot's key endpoint."""
    window = request.args.get("window", "24H").upper()
    if window not in ("24H", "2H"):
        return err("VALIDATION_ERROR", "window must be 24H or 2H")
    now = _dt_arg("now")  # test hook: pretend it is another time
    return jsonify(svc.due_reminders(window, now=now))


@api.get("/notifications")
@require_api_key
def get_notifications():
    q = Notification.query
    if request.args.get("appointment_id", type=int):
        q = q.filter(Notification.appointment_id == request.args.get("appointment_id", type=int))
    if request.args.get("type"):
        q = q.filter(Notification.type == request.args["type"])
    if request.args.get("status"):
        q = q.filter(Notification.status == request.args["status"])
    rows = q.order_by(Notification.created_at.desc()).limit(
        request.args.get("limit", 200, type=int)
    )
    return jsonify([n.to_dict() for n in rows])


@api.post("/notifications")
@require_api_key
def create_notification():
    d = request.get_json(silent=True) or {}
    required = ("appointment_id", "type", "channel", "recipient", "template_id")
    if not all(d.get(k) for k in required):
        return err("VALIDATION_ERROR", f"required: {', '.join(required)}")

    n, e = svc.log_notification(
        appointment_id=d["appointment_id"],
        ntype=d["type"],
        channel=d["channel"],
        recipient=d["recipient"],
        template_id=d["template_id"],
        status=d.get("status", "SENT"),
        provider=d.get("provider"),
        provider_message_id=d.get("provider_message_id"),
        attempt_no=int(d.get("attempt_no", 1)),
        error_code=d.get("error_code"),
        body_preview=d.get("body_preview"),
    )
    return err(e) if e else (jsonify(n.to_dict()), 201)


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------
@api.get("/conflicts")
@require_api_key
def get_conflicts():
    q = ConflictLog.query
    if request.args.get("status"):
        q = q.filter(ConflictLog.status == request.args["status"])
    if request.args.get("type"):
        q = q.filter(ConflictLog.conflict_type == request.args["type"])
    rows = q.order_by(ConflictLog.detected_at.desc()).limit(
        request.args.get("limit", 200, type=int)
    )
    return jsonify([c.to_dict() for c in rows])


@api.post("/conflicts")
@require_api_key
def post_conflict():
    d = request.get_json(silent=True) or {}
    if d.get("conflict_type") not in ConflictLog.TYPES:
        return err("VALIDATION_ERROR", f"conflict_type must be one of {ConflictLog.TYPES}")
    c = svc.raise_conflict(
        conflict_type=d["conflict_type"],
        severity=d.get("severity", "MEDIUM"),
        appointment_id=d.get("appointment_id"),
        related_appointment_id=d.get("related_appointment_id"),
        slot_id=d.get("slot_id"),
        detected_by=d.get("detected_by", "SchedulingBot"),
        detail=d.get("detail"),
    )
    return jsonify(c.to_dict()), 201


@api.patch("/conflicts/<int:conflict_id>")
@require_api_key
def patch_conflict(conflict_id):
    c = db.session.get(ConflictLog, conflict_id)
    if c is None:
        return err("VALIDATION_ERROR", "conflict not found")
    d = request.get_json(silent=True) or {}
    if "resolution" in d:
        c.resolution = d["resolution"]
    if "resolution_note" in d:
        c.resolution_note = (d["resolution_note"] or "")[:255]
    if "status" in d:
        c.status = d["status"]
        if d["status"] in ("RESOLVED", "ESCALATED"):
            c.resolved_at = utcnow()
    db.session.commit()
    return jsonify(c.to_dict())


@api.get("/conflicts/detect/double-bookings")
@require_api_key
def detect_double():
    findings = svc.detect_double_bookings()
    if request.args.get("raise", "false").lower() == "true":
        for f in findings:
            svc.raise_conflict(
                "DOUBLE_BOOKING",
                severity="HIGH",
                appointment_id=f["keeper"]["appointment_id"],
                related_appointment_id=f["losers"][0]["appointment_id"] if f["losers"] else None,
                slot_id=f["slot_id"],
                detected_by="ReconcileSweep",
                detail=f"{f['count']} active appointments on slot {f['slot_id']}",
            )
    return jsonify(findings)


@api.get("/doctors/unavailability")
@require_api_key
def get_unavailability():
    q = DoctorUnavailability.query
    if request.args.get("is_processed"):
        q = q.filter(
            DoctorUnavailability.is_processed.is_(
                request.args["is_processed"].lower() == "true"
            )
        )
    return jsonify([u.to_dict() for u in q.order_by(DoctorUnavailability.from_datetime)])


@api.post("/doctors/unavailability")
@require_api_key
def post_unavailability():
    d = request.get_json(silent=True) or {}
    try:
        frm = datetime.fromisoformat(d["from_datetime"])
        to = datetime.fromisoformat(d["to_datetime"])
    except (KeyError, ValueError):
        return err("VALIDATION_ERROR", "from_datetime and to_datetime must be ISO-8601")

    u = DoctorUnavailability(
        doctor_id=d["doctor_id"],
        from_datetime=frm,
        to_datetime=to,
        reason=d.get("reason"),
        created_by=d.get("created_by", "coordinator"),
    )
    db.session.add(u)
    db.session.commit()

    svc.raise_conflict(
        "DOCTOR_UNAVAILABLE",
        severity="HIGH",
        detected_by="HMS",
        detail=f"Doctor {u.doctor_id} unavailable {frm.isoformat()} to {to.isoformat()}",
    )
    return jsonify(u.to_dict()), 201


@api.patch("/doctors/unavailability/<int:unavailability_id>")
@require_api_key
def patch_unavailability(unavailability_id):
    u = db.session.get(DoctorUnavailability, unavailability_id)
    if u is None:
        return err("VALIDATION_ERROR", "unavailability not found")
    d = request.get_json(silent=True) or {}
    if "is_processed" in d:
        u.is_processed = bool(d["is_processed"])
    db.session.commit()
    return jsonify(u.to_dict())


@api.post("/slots/block")
@require_api_key
def post_block_slots():
    """Block a doctor's slots before running a cascade."""
    d = request.get_json(silent=True) or {}
    try:
        frm = datetime.fromisoformat(d["from_datetime"])
        to = datetime.fromisoformat(d["to_datetime"])
    except (KeyError, ValueError):
        return err("VALIDATION_ERROR", "from_datetime and to_datetime must be ISO-8601")
    n = svc.block_slots_in_range(d["doctor_id"], frm, to)
    return jsonify({"blocked": n})


# ---------------------------------------------------------------------------
# Booking requests — intake hand-off
# ---------------------------------------------------------------------------
@api.post("/booking-requests")
@require_api_key
def post_booking_request():
    d = request.get_json(silent=True) or {}
    errors = svc.validate_booking_request(d)
    if errors:
        return err("VALIDATION_ERROR", "; ".join(errors))

    br = BookingRequest(
        reference=svc.next_request_reference(),
        patient_name=d["patient_name"].strip(),
        phone=d["phone"].strip(),
        email=(d.get("email") or "").strip() or None,
        department_code=d["department_code"],
        preferred_doctor_id=d.get("preferred_doctor_id") or None,
        preferred_date_from=date.fromisoformat(str(d["preferred_date_from"])[:10]),
        preferred_date_to=date.fromisoformat(str(d["preferred_date_to"])[:10]),
        time_band=d.get("time_band", "ANY"),
        urgency=d.get("urgency", "NORMAL"),
        is_follow_up=bool(d.get("is_follow_up")),
        reason=(d.get("reason") or "")[:255] or None,
    )
    db.session.add(br)
    db.session.commit()

    push_booking_request(current_app._get_current_object(), br)
    return jsonify(br.to_dict()), 201


@api.get("/booking-requests")
@require_api_key
def get_booking_requests():
    q = BookingRequest.query
    if request.args.get("queue_status"):
        q = q.filter(BookingRequest.queue_status == request.args["queue_status"])
    rows = q.order_by(BookingRequest.created_at.desc()).limit(
        request.args.get("limit", 100, type=int)
    )
    return jsonify([r.to_dict() for r in rows])


@api.get("/booking-requests/<reference>")
@require_api_key
def get_booking_request(reference):
    br = BookingRequest.query.filter_by(reference=reference).first()
    return jsonify(br.to_dict()) if br else err("VALIDATION_ERROR", "request not found")


@api.patch("/booking-requests/<reference>")
@require_api_key
def patch_booking_request(reference):
    """Bots mark an item PROCESSED or FAILED once they finish with it, so the
    next poll skips items already picked up."""
    br = BookingRequest.query.filter_by(reference=reference).first()
    if br is None:
        return err("VALIDATION_ERROR", "request not found")
    d = request.get_json(silent=True) or {}
    if "queue_status" in d:
        allowed = ("NEW", "PENDING_PUSH", "QUEUED", "PROCESSED", "FAILED")
        if d["queue_status"] not in allowed:
            return err("VALIDATION_ERROR", f"queue_status must be one of {allowed}")
        br.queue_status = d["queue_status"]
    if "push_error" in d:
        br.push_error = (d["push_error"] or "")[:255] or None
    if "queue_item_id" in d:
        br.queue_item_id = d["queue_item_id"]
    db.session.commit()
    return jsonify(br.to_dict())
