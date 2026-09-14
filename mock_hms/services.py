"""Domain logic for the Mock HMS.

Shared by the UI blueprint (what the bots click) and the API blueprint (what
the bots call). Putting the logic here rather than in either blueprint means a
booking made through the form and a booking made through the API go down the
same path and hit the same guards — which is what makes the Phase 4 tests
meaningful.

Error handling convention: functions return `(result, error_code)`. A non-None
error_code is a business outcome the caller must handle, never an exception to
swallow. Codes match docs/phase1/hms_interfaces.md Part D.
"""
from __future__ import annotations

import re
from datetime import datetime, date, time, timedelta

from sqlalchemy import func, and_, or_

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

PHONE_RE = re.compile(r"^\+[1-9]\d{9,14}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


# ---------------------------------------------------------------------------
# Reference numbers
# ---------------------------------------------------------------------------
def next_reference_no() -> str:
    year = date.today().year
    n = db.session.query(func.count(Appointment.appointment_id)).scalar() or 0
    return f"APT-{year}-{n + 1:06d}"


def next_request_reference() -> str:
    n = db.session.query(func.count(BookingRequest.request_id)).scalar() or 0
    return f"REQ-{n + 1:06d}"


def next_mrn() -> str:
    n = db.session.query(func.count(Patient.patient_id)).scalar() or 0
    return f"MRN-{n + 1:05d}"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def validate_booking_request(data: dict) -> list[str]:
    """Returns a list of human-readable problems. Empty list == valid."""
    errors = []

    if not (data.get("patient_name") or "").strip():
        errors.append("Patient name is required")

    phone = (data.get("phone") or "").strip()
    if not phone:
        errors.append("Phone is required")
    elif not PHONE_RE.match(phone):
        errors.append("Phone must be E.164 format, e.g. +919876543210")

    email = (data.get("email") or "").strip()
    if email and not EMAIL_RE.match(email):
        errors.append("Email format is invalid")

    code = (data.get("department_code") or "").strip()
    if not code:
        errors.append("Department is required")
    elif not Department.query.filter_by(code=code, is_active=True).first():
        errors.append(f"Unknown or inactive department: {code}")

    try:
        d_from = _as_date(data.get("preferred_date_from"))
        d_to = _as_date(data.get("preferred_date_to"))
    except (ValueError, TypeError):
        errors.append("Preferred dates must be YYYY-MM-DD")
        return errors

    if not d_from or not d_to:
        errors.append("Both preferred dates are required")
    else:
        if d_to < d_from:
            errors.append("Preferred date range is reversed")
        if d_from < date.today():
            errors.append("Preferred date cannot be in the past")
        horizon = date.today() + timedelta(days=RULES.max_booking_horizon_days)
        if d_from > horizon:
            errors.append(
                f"Preferred date is beyond the {RULES.max_booking_horizon_days}-day booking horizon"
            )

    if (data.get("urgency") or "NORMAL") not in ("NORMAL", "HIGH", "URGENT"):
        errors.append("Urgency must be NORMAL, HIGH or URGENT")
    if (data.get("time_band") or "ANY") not in ("MORNING", "AFTERNOON", "ANY"):
        errors.append("Time band must be MORNING, AFTERNOON or ANY")

    return errors


def _as_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    return date.fromisoformat(str(v)[:10])


# ---------------------------------------------------------------------------
# Patients
# ---------------------------------------------------------------------------
def find_patient_by_phone(phone: str) -> Patient | None:
    """Phone is the dedup key. Name matching is never used to auto-merge."""
    return Patient.query.filter_by(phone=(phone or "").strip()).first()


def register_patient(
    full_name: str,
    phone: str,
    email: str | None = None,
    date_of_birth: date | None = None,
    gender: str = "O",
    preferred_channel: str = "SMS",
) -> tuple[Patient | None, str | None]:
    if not PHONE_RE.match((phone or "").strip()):
        return None, "VALIDATION_ERROR"
    if find_patient_by_phone(phone):
        return None, "DUPLICATE_PHONE"

    p = Patient(
        mrn=next_mrn(),
        full_name=full_name.strip(),
        phone=phone.strip(),
        email=(email or "").strip() or None,
        date_of_birth=date_of_birth or date(1990, 1, 1),
        gender=gender or "O",
        preferred_channel=preferred_channel or "SMS",
        sms_consent=True,
        email_consent=bool(email),
    )
    db.session.add(p)
    db.session.commit()
    return p, None


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------
def derive_priority(urgency: str, patient: Patient | None, is_follow_up: bool) -> str:
    """Mirrors rules.yaml priority.derivation — first match wins."""
    if urgency == "URGENT":
        return "URGENT"
    if urgency == "HIGH":
        return "HIGH"
    if patient is not None and patient.is_senior:
        return "HIGH"
    if is_follow_up:
        return "HIGH"
    return "NORMAL"


# ---------------------------------------------------------------------------
# Slot queries
# ---------------------------------------------------------------------------
def search_slots(
    department_code: str | None = None,
    doctor_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: str | None = "AVAILABLE",
    include_emergency_reserve: bool = False,
    limit: int = 500,
) -> list[Slot]:
    q = Slot.query.join(Doctor).join(Department)

    if department_code:
        q = q.filter(Department.code == department_code)
    if doctor_id:
        q = q.filter(Slot.doctor_id == doctor_id)
    if date_from:
        q = q.filter(Slot.slot_date >= date_from)
    if date_to:
        q = q.filter(Slot.slot_date <= date_to)
    if status:
        q = q.filter(Slot.status == status)
    if not include_emergency_reserve:
        q = q.filter(Slot.is_emergency_reserve.is_(False))

    q = q.filter(Doctor.is_active.is_(True))
    return q.order_by(Slot.slot_date, Slot.start_time, Slot.slot_id).limit(limit).all()


def doctor_daily_count(doctor_id: int, on: date) -> int:
    return (
        db.session.query(func.count(Appointment.appointment_id))
        .filter(
            Appointment.doctor_id == doctor_id,
            func.date(Appointment.appointment_datetime) == on.isoformat(),
            Appointment.status.in_(Appointment.ACTIVE_STATUSES),
        )
        .scalar()
        or 0
    )


def department_daily_count(department_id: int, on: date) -> int:
    return (
        db.session.query(func.count(Appointment.appointment_id))
        .filter(
            Appointment.department_id == department_id,
            func.date(Appointment.appointment_datetime) == on.isoformat(),
            Appointment.status.in_(Appointment.ACTIVE_STATUSES),
        )
        .scalar()
        or 0
    )


# ---------------------------------------------------------------------------
# Slot locking — guard layer 1 and 2
# ---------------------------------------------------------------------------
def lock_slot(slot_id: int, locked_by: str) -> tuple[Slot | None, str | None]:
    slot = db.session.get(Slot, slot_id)
    if slot is None:
        return None, "SLOT_NOT_FOUND"
    if slot.status == "BLOCKED":
        return None, "SLOT_BLOCKED"
    if slot.status != "AVAILABLE":
        return None, "SLOT_TAKEN"
    if slot.is_lock_active and slot.locked_by != locked_by:
        return None, "SLOT_LOCKED"

    slot.locked_until = utcnow() + timedelta(seconds=RULES.lock_ttl_seconds)
    slot.locked_by = locked_by
    slot.version += 1
    db.session.commit()
    return slot, None


def release_slot(slot_id: int, locked_by: str | None = None) -> tuple[Slot | None, str | None]:
    """Called from the bot's FINALLY block. Always safe to call."""
    slot = db.session.get(Slot, slot_id)
    if slot is None:
        return None, "SLOT_NOT_FOUND"
    if locked_by and slot.locked_by and slot.locked_by != locked_by:
        return slot, None  # someone else's lock — leave it alone
    slot.locked_until = None
    slot.locked_by = None
    slot.version += 1
    db.session.commit()
    return slot, None


# ---------------------------------------------------------------------------
# Booking — guard layer 3 is the DB index behind this
# ---------------------------------------------------------------------------
def book_appointment(
    patient_id: int,
    slot_id: int,
    priority: str = "NORMAL",
    reason: str | None = None,
    is_follow_up: bool = False,
    queue_item_ref: str | None = None,
    booked_by: str = "SchedulingBot",
    source: str = "BOT",
    expected_version: int | None = None,
) -> tuple[Appointment | None, str | None]:
    patient = db.session.get(Patient, patient_id)
    if patient is None:
        return None, "PATIENT_NOT_FOUND"

    slot = db.session.get(Slot, slot_id)
    if slot is None:
        return None, "SLOT_NOT_FOUND"
    if slot.status == "BLOCKED":
        return None, "SLOT_BLOCKED"
    if slot.status != "AVAILABLE":
        return None, "SLOT_TAKEN"
    if expected_version is not None and slot.version != expected_version:
        return None, "SLOT_TAKEN"
    if slot.is_lock_active and slot.locked_by not in (None, booked_by):
        return None, "SLOT_LOCKED"

    doctor = slot.doctor
    if doctor is None or not doctor.is_active:
        return None, "DOCTOR_INACTIVE"

    if slot.is_emergency_reserve and priority not in RULES.emergency_reserve_priorities:
        return None, "SLOT_BLOCKED"

    if RULES.respect_doctor_daily_cap:
        if doctor_daily_count(doctor.doctor_id, slot.slot_date) >= doctor.daily_slot_cap:
            return None, "DAILY_CAP_REACHED"
    if RULES.respect_department_daily_cap:
        dept = doctor.department
        if department_daily_count(dept.department_id, slot.slot_date) >= dept.max_daily_appointments:
            return None, "DAILY_CAP_REACHED"

    appt = Appointment(
        reference_no=next_reference_no(),
        patient_id=patient.patient_id,
        slot_id=slot.slot_id,
        doctor_id=doctor.doctor_id,
        department_id=doctor.department_id,
        appointment_datetime=slot.start_datetime,
        status="SCHEDULED",
        priority=priority,
        reason=reason,
        is_follow_up=is_follow_up,
        source=source,
        queue_item_ref=queue_item_ref,
        booked_by=booked_by,
    )
    slot.status = "BOOKED"
    slot.locked_until = None
    slot.locked_by = None
    slot.version += 1

    db.session.add(appt)
    try:
        db.session.commit()
    except Exception:
        # The partial unique index fired: another transaction won the race
        # between our status check and this commit. This is the guard of last
        # resort, and reaching it is not an error in the bot.
        db.session.rollback()
        return None, "SLOT_TAKEN"

    audit(appt.appointment_id, "CREATED", booked_by, to_value=appt.reference_no)
    return appt, None


def cancel_appointment(
    appointment_id: int, actor: str = "ConflictResolutionBot", note: str | None = None
) -> tuple[Appointment | None, str | None]:
    appt = db.session.get(Appointment, appointment_id)
    if appt is None:
        return None, "APPOINTMENT_NOT_FOUND"
    if appt.status == "CANCELLED":
        return appt, None

    old = appt.status
    appt.status = "CANCELLED"
    if appt.slot and appt.slot.status == "BOOKED":
        appt.slot.status = "AVAILABLE"
        appt.slot.version += 1
    db.session.commit()

    audit(appt.appointment_id, "CANCELLED", actor, from_value=old, to_value="CANCELLED", note=note)
    return appt, None


def reschedule_appointment(
    appointment_id: int,
    new_slot_id: int,
    actor: str = "ConflictResolutionBot",
    note: str | None = None,
    force: bool = False,
) -> tuple[Appointment | None, str | None]:
    """Move an appointment to a new slot, enforcing the safety rails.

    `force=True` is for a clerk acting on an escalated item — it bypasses the
    notice period and the reschedule cap, which is exactly why those cases are
    escalated to a human in the first place.
    """
    appt = db.session.get(Appointment, appointment_id)
    if appt is None:
        return None, "APPOINTMENT_NOT_FOUND"
    if appt.status in ("CANCELLED", "COMPLETED", "NO_SHOW"):
        return None, "VALIDATION_ERROR"

    if not force:
        # Rail 1: never move an appointment the patient may already be travelling to.
        cutoff = utcnow() + timedelta(hours=RULES.min_reschedule_notice_hours)
        if appt.appointment_datetime < cutoff:
            return None, "TOO_CLOSE_TO_RESCHEDULE"
        # Rail 2: clinical urgency is not a bot's call.
        if RULES.never_auto_move_urgent and appt.priority == "URGENT":
            return None, "URGENT_NEVER_AUTO_MOVED"
        # Rail 3: stop a patient being bounced by successive conflicts.
        if appt.reschedule_count >= RULES.max_reschedules_per_appointment:
            return None, "AMBIGUOUS_PRIORITY"

    new_slot = db.session.get(Slot, new_slot_id)
    if new_slot is None:
        return None, "SLOT_NOT_FOUND"
    if new_slot.status != "AVAILABLE":
        return None, "SLOT_TAKEN"

    old_slot = appt.slot
    old_desc = f"{appt.appointment_datetime.isoformat()} slot={appt.slot_id}"

    if old_slot and old_slot.status == "BOOKED":
        old_slot.status = "AVAILABLE"
        old_slot.version += 1

    appt.slot_id = new_slot.slot_id
    appt.doctor_id = new_slot.doctor_id
    appt.department_id = new_slot.doctor.department_id
    appt.appointment_datetime = new_slot.start_datetime
    appt.status = "RESCHEDULED"
    appt.reschedule_count += 1
    appt.synced_to_excel_at = None  # Excel mirror is now stale
    new_slot.status = "BOOKED"
    new_slot.version += 1

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None, "SLOT_TAKEN"

    audit(
        appt.appointment_id,
        "RESCHEDULED",
        actor,
        from_value=old_desc,
        to_value=f"{appt.appointment_datetime.isoformat()} slot={new_slot.slot_id}",
        note=note,
    )
    return appt, None


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------
def audit(appointment_id, action, actor, from_value=None, to_value=None, note=None):
    row = AppointmentAudit(
        appointment_id=appointment_id,
        action=action,
        actor=actor,
        from_value=(str(from_value)[:255] if from_value is not None else None),
        to_value=(str(to_value)[:255] if to_value is not None else None),
        note=(str(note)[:255] if note else None),
    )
    db.session.add(row)
    db.session.commit()
    return row


# ---------------------------------------------------------------------------
# Conflicts
# ---------------------------------------------------------------------------
def raise_conflict(
    conflict_type: str,
    severity: str = "MEDIUM",
    appointment_id: int | None = None,
    related_appointment_id: int | None = None,
    slot_id: int | None = None,
    detected_by: str = "ReconcileSweep",
    detail: str | None = None,
) -> ConflictLog:
    """Idempotent: an identical OPEN conflict is returned rather than duplicated."""
    existing = ConflictLog.query.filter_by(
        conflict_type=conflict_type,
        appointment_id=appointment_id,
        slot_id=slot_id,
        status="OPEN",
    ).first()
    if existing:
        return existing

    c = ConflictLog(
        conflict_type=conflict_type,
        severity=severity,
        appointment_id=appointment_id,
        related_appointment_id=related_appointment_id,
        slot_id=slot_id,
        detected_by=detected_by,
        detail=(detail or "")[:255] or None,
    )
    db.session.add(c)
    db.session.commit()
    return c


def detect_double_bookings() -> list[dict]:
    """Slots holding more than one non-cancelled appointment."""
    rows = (
        db.session.query(Appointment.slot_id, func.count(Appointment.appointment_id).label("n"))
        .filter(Appointment.status.in_(Appointment.ACTIVE_STATUSES))
        .group_by(Appointment.slot_id)
        .having(func.count(Appointment.appointment_id) > 1)
        .all()
    )

    findings = []
    for slot_id, n in rows:
        appts = (
            Appointment.query.filter(
                Appointment.slot_id == slot_id,
                Appointment.status.in_(Appointment.ACTIVE_STATUSES),
            )
            .order_by(Appointment.created_at, Appointment.appointment_id)
            .all()
        )
        # Rank by who has the strongest claim (rules.yaml keeper_ranking).
        ranked = sorted(
            appts,
            key=lambda a: (
                RULES.priority_rank(a.priority),
                0 if a.is_follow_up else 1,
                a.created_at,
                a.appointment_id,
            ),
        )
        findings.append(
            {
                "slot_id": slot_id,
                "count": n,
                "keeper": ranked[0].to_dict(),
                "losers": [a.to_dict() for a in ranked[1:]],
            }
        )
    return findings


def detect_unprocessed_unavailability() -> list[DoctorUnavailability]:
    return DoctorUnavailability.query.filter_by(is_processed=False).all()


def appointments_affected_by(unavail: DoctorUnavailability) -> list[Appointment]:
    return (
        Appointment.query.filter(
            Appointment.doctor_id == unavail.doctor_id,
            Appointment.appointment_datetime >= unavail.from_datetime,
            Appointment.appointment_datetime < unavail.to_datetime,
            Appointment.status.in_(Appointment.ACTIVE_STATUSES),
        )
        .order_by(Appointment.appointment_datetime)
        .all()
    )


def block_slots_in_range(doctor_id: int, frm: datetime, to: datetime) -> int:
    """Block first, reschedule second — otherwise the Scheduling Bot can book
    a new patient into the gap while the cascade is still being worked."""
    slots = Slot.query.filter(
        Slot.doctor_id == doctor_id,
        Slot.slot_date >= frm.date(),
        Slot.slot_date <= to.date(),
    ).all()
    n = 0
    for s in slots:
        if frm <= s.start_datetime < to and s.status in ("AVAILABLE", "LOCKED"):
            s.status = "BLOCKED"
            s.version += 1
            n += 1
    db.session.commit()
    return n


# ---------------------------------------------------------------------------
# Reminders
# ---------------------------------------------------------------------------
def due_reminders(window: str, now: datetime | None = None) -> list[dict]:
    """Appointments due for a reminder in this window with none already sent.

    The window tolerance comes from rules.yaml: the bot runs on a discrete
    schedule and never lands exactly on T-24h, so it claims everything whose
    target moment falls inside the slice it is responsible for.
    """
    now = now or utcnow()
    cfg = RULES.reminder_window(window)
    if cfg is None:
        return []

    offset = timedelta(hours=int(cfg["offset_hours"]))
    tol = timedelta(minutes=int(cfg.get("tolerance_minutes", 15)))
    target_from, target_to = now + offset - tol, now + offset + tol
    rtype = cfg["type"]

    appts = Appointment.query.filter(
        Appointment.appointment_datetime >= target_from,
        Appointment.appointment_datetime <= target_to,
        Appointment.status.in_(Appointment.ACTIVE_STATUSES),
    ).all()

    out = []
    for a in appts:
        # A late booking skips the 24h reminder and goes straight to the 2h one.
        skip_within = cfg.get("skip_if_appointment_within_hours")
        if skip_within and a.appointment_datetime < now + timedelta(hours=int(skip_within)):
            continue

        already = [
            n.type
            for n in a.notifications
            if n.status == "SENT" and n.type == rtype
        ]
        if already:
            continue

        out.append(
            {
                "appointment_id": a.appointment_id,
                "reference_no": a.reference_no,
                "appointment_datetime": a.appointment_datetime.isoformat(),
                "priority": a.priority,
                "patient": a.patient.to_dict() if a.patient else None,
                "doctor_name": a.doctor.full_name if a.doctor else None,
                "department": a.department.name if a.department else None,
                "reminder_type": rtype,
                "already_sent": [n.type for n in a.notifications if n.status == "SENT"],
            }
        )
    return out


def log_notification(
    appointment_id: int,
    ntype: str,
    channel: str,
    recipient: str,
    template_id: str,
    status: str = "SENT",
    provider: str | None = None,
    provider_message_id: str | None = None,
    attempt_no: int = 1,
    error_code: str | None = None,
    body_preview: str | None = None,
) -> tuple[Notification | None, str | None]:
    appt = db.session.get(Appointment, appointment_id)
    if appt is None:
        return None, "APPOINTMENT_NOT_FOUND"

    n = Notification(
        appointment_id=appointment_id,
        patient_id=appt.patient_id,
        type=ntype,
        channel=channel,
        recipient=recipient,
        template_id=template_id,
        status=status,
        provider=provider,
        provider_message_id=provider_message_id,
        attempt_no=attempt_no,
        error_code=error_code,
        body_preview=(body_preview or "")[:255] or None,
        sent_at=utcnow() if status == "SENT" else None,
    )
    db.session.add(n)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        return None, "DUPLICATE_NOTIFICATION"
    return n, None


def already_sent(appointment_id: int, ntype: str) -> bool:
    """The idempotency check that makes the Reminder Bot safe to re-run."""
    return (
        Notification.query.filter_by(
            appointment_id=appointment_id, type=ntype, status="SENT"
        ).first()
        is not None
    )
