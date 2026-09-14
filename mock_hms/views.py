"""UI blueprint — the screens the bots actually click.

Implements docs/phase1/hms_interfaces.md Part A. Every automatable element in
the templates carries a `data-automation-id`, which is what UiPath selectors
anchor on, so restyling the pages never breaks a bot.

These views deliberately go through the same `services` functions as the API.
A booking made by a bot clicking the form and one made by an API call hit the
same guards.
"""
from __future__ import annotations

from datetime import datetime, date, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, current_app
from sqlalchemy import func

from .models import (
    db,
    Department,
    Doctor,
    Patient,
    Slot,
    Appointment,
    Notification,
    DoctorUnavailability,
    ConflictLog,
    BookingRequest,
    utcnow,
)
from .rules import RULES
from . import services as svc
from .orchestrator_client import push_booking_request

ui = Blueprint("ui", __name__)


def _ctx(**kw):
    kw.setdefault("hospital_name", current_app.config["HOSPITAL_NAME"])
    kw.setdefault("departments", Department.query.filter_by(is_active=True).order_by(Department.code).all())
    return kw


# ---------------------------------------------------------------------------
# A1. Booking request form (public)
# ---------------------------------------------------------------------------
@ui.route("/", methods=["GET", "POST"])
def index():
    doctors = Doctor.query.filter_by(is_active=True).order_by(Doctor.full_name).all()
    today = date.today()
    defaults = {
        "preferred_date_from": today.isoformat(),
        "preferred_date_to": (today + timedelta(days=RULES.default_search_window_days)).isoformat(),
    }

    if request.method == "GET":
        return render_template("index.html", **_ctx(doctors=doctors, form=defaults))

    form = {
        "patient_name": request.form.get("patient_name", ""),
        "phone": request.form.get("phone", ""),
        "email": request.form.get("email", ""),
        "department_code": request.form.get("department_code", ""),
        "preferred_doctor_id": request.form.get("preferred_doctor_id") or None,
        "preferred_date_from": request.form.get("preferred_date_from", ""),
        "preferred_date_to": request.form.get("preferred_date_to", ""),
        "time_band": request.form.get("time_band", "ANY"),
        "urgency": request.form.get("urgency", "NORMAL"),
        "is_follow_up": request.form.get("is_follow_up") == "on",
        "reason": request.form.get("reason", ""),
    }

    errors = svc.validate_booking_request(form)
    if errors:
        return render_template(
            "index.html", **_ctx(doctors=doctors, form=form, errors=errors)
        ), 400

    br = BookingRequest(
        reference=svc.next_request_reference(),
        patient_name=form["patient_name"].strip(),
        phone=form["phone"].strip(),
        email=form["email"].strip() or None,
        department_code=form["department_code"],
        preferred_doctor_id=int(form["preferred_doctor_id"]) if form["preferred_doctor_id"] else None,
        preferred_date_from=date.fromisoformat(form["preferred_date_from"]),
        preferred_date_to=date.fromisoformat(form["preferred_date_to"]),
        time_band=form["time_band"],
        urgency=form["urgency"],
        is_follow_up=form["is_follow_up"],
        reason=form["reason"][:255] or None,
    )
    db.session.add(br)
    db.session.commit()

    # Push to the Orchestrator queue. A failure here is not the patient's
    # problem: the row is kept as PENDING_PUSH and retried by a sweep.
    pushed, push_err = push_booking_request(current_app._get_current_object(), br)

    return render_template(
        "index.html",
        **_ctx(
            doctors=doctors,
            form=defaults,
            result={
                "reference": br.reference,
                "queue_status": br.queue_status,
                "queued": pushed,
                "push_error": push_err,
            },
        ),
    )


# ---------------------------------------------------------------------------
# A2. Patient search & registration
# ---------------------------------------------------------------------------
@ui.route("/admin/patients", methods=["GET", "POST"])
def patients():
    results, searched, new_patient, errors = [], False, None, []

    if request.method == "POST" and request.form.get("action") == "register":
        dob = None
        if request.form.get("date_of_birth"):
            try:
                dob = date.fromisoformat(request.form["date_of_birth"])
            except ValueError:
                errors.append("Date of birth must be YYYY-MM-DD")

        if not errors:
            p, e = svc.register_patient(
                full_name=request.form.get("full_name", ""),
                phone=request.form.get("phone", ""),
                email=request.form.get("email"),
                date_of_birth=dob,
                gender=request.form.get("gender", "O"),
                preferred_channel=request.form.get("preferred_channel", "SMS"),
            )
            if e:
                errors.append(
                    "Phone already registered" if e == "DUPLICATE_PHONE"
                    else "Phone must be E.164 format, e.g. +919876543210"
                )
            else:
                new_patient = p

    query = request.values.get("q", "").strip()
    if query:
        searched = True
        p = svc.find_patient_by_phone(query)
        if p:
            results = [p]
        else:
            results = Patient.query.filter(
                Patient.full_name.ilike(f"%{query}%")
            ).limit(25).all()

    return render_template(
        "patients.html",
        **_ctx(query=query, results=results, searched=searched,
               new_patient=new_patient, errors=errors),
    )


# ---------------------------------------------------------------------------
# A3. Doctor schedule board — the data-scrape target
# ---------------------------------------------------------------------------
@ui.route("/admin/schedule")
def schedule():
    dept = request.args.get("department", "")
    doctor_id = request.args.get("doctor_id", type=int)
    d_from = _parse_date(request.args.get("date_from"), date.today())
    d_to = _parse_date(
        request.args.get("date_to"),
        date.today() + timedelta(days=RULES.default_search_window_days),
    )
    status = request.args.get("status", "AVAILABLE")

    slots = []
    if request.args:
        slots = svc.search_slots(
            department_code=dept or None,
            doctor_id=doctor_id,
            date_from=d_from,
            date_to=d_to,
            status=status or None,
            include_emergency_reserve=True,
            limit=1000,
        )

    doctors = Doctor.query.filter_by(is_active=True).order_by(Doctor.full_name).all()
    return render_template(
        "schedule.html",
        **_ctx(
            doctors=doctors, slots=slots, loaded=bool(request.args),
            f={"department": dept, "doctor_id": doctor_id, "status": status,
               "date_from": d_from.isoformat(), "date_to": d_to.isoformat()},
        ),
    )


# ---------------------------------------------------------------------------
# A4. Booking form
# ---------------------------------------------------------------------------
@ui.route("/admin/book", methods=["GET", "POST"])
def book():
    result, error_code, error_message = None, None, None
    form = {
        "patient_id": request.values.get("patient_id", ""),
        "slot_id": request.values.get("slot_id", ""),
        "priority": request.values.get("priority", "NORMAL"),
        "reason": request.values.get("reason", ""),
        "is_follow_up": request.values.get("is_follow_up") == "on",
        "queue_item_ref": request.values.get("queue_item_ref", ""),
    }

    if request.method == "POST":
        try:
            patient_id = int(form["patient_id"])
            slot_id = int(form["slot_id"])
        except (TypeError, ValueError):
            error_code, error_message = "VALIDATION_ERROR", "patient_id and slot_id must be numbers"
        else:
            appt, e = svc.book_appointment(
                patient_id=patient_id,
                slot_id=slot_id,
                priority=form["priority"],
                reason=form["reason"] or None,
                is_follow_up=form["is_follow_up"],
                queue_item_ref=form["queue_item_ref"] or None,
                booked_by=request.form.get("booked_by", "SchedulingBot"),
                source="BOT",
            )
            if e:
                error_code = e
                error_message = _explain(e)
            else:
                result = appt

    return render_template("book.html", **_ctx(form=form, result=result,
                                               error_code=error_code,
                                               error_message=error_message))


# ---------------------------------------------------------------------------
# A5. Conflict console
# ---------------------------------------------------------------------------
@ui.route("/admin/conflicts", methods=["GET", "POST"])
def conflicts():
    message = None
    if request.method == "POST":
        c = db.session.get(ConflictLog, request.form.get("conflict_id", type=int))
        if c:
            c.resolution = request.form.get("resolution")
            c.resolution_note = (request.form.get("note") or "")[:255]
            c.status = "ESCALATED" if c.resolution == "ESCALATED" else "RESOLVED"
            c.resolved_at = utcnow()
            db.session.commit()
            message = f"Conflict {c.conflict_id} marked {c.status}"

    status = request.args.get("status", "OPEN")
    q = ConflictLog.query
    if status:
        q = q.filter(ConflictLog.status == status)
    rows = q.order_by(ConflictLog.detected_at.desc()).limit(200).all()

    return render_template("conflicts.html", **_ctx(conflicts=rows, status=status,
                                                    message=message))


# ---------------------------------------------------------------------------
# A6. Reschedule
# ---------------------------------------------------------------------------
@ui.route("/admin/reschedule", methods=["GET", "POST"])
def reschedule():
    result, error_code, error_message = None, None, None
    reference = request.values.get("reference", "")
    new_slot = request.values.get("new_slot_id", "")

    if request.method == "POST":
        appt = Appointment.query.filter_by(reference_no=reference).first()
        if appt is None:
            error_code, error_message = "APPOINTMENT_NOT_FOUND", f"No appointment {reference}"
        else:
            try:
                slot_id = int(new_slot)
            except (TypeError, ValueError):
                error_code, error_message = "VALIDATION_ERROR", "new_slot_id must be a number"
            else:
                a2, e = svc.reschedule_appointment(
                    appt.appointment_id,
                    slot_id,
                    actor=request.form.get("actor", "ConflictResolutionBot"),
                    note=request.form.get("reason"),
                    force=request.form.get("force") == "on",
                )
                if e:
                    error_code, error_message = e, _explain(e)
                else:
                    result = a2

    return render_template("reschedule.html", **_ctx(reference=reference,
                                                     new_slot_id=new_slot, result=result,
                                                     error_code=error_code,
                                                     error_message=error_message))


# ---------------------------------------------------------------------------
# A7. Doctor roster & unavailability — the demo trigger
# ---------------------------------------------------------------------------
@ui.route("/admin/doctors", methods=["GET", "POST"])
def doctors_page():
    message = None
    if request.method == "POST":
        try:
            frm = datetime.fromisoformat(request.form["unavail_from"])
            to = datetime.fromisoformat(request.form["unavail_to"])
            doctor_id = int(request.form["doctor_id"])
        except (KeyError, ValueError):
            message = "Invalid doctor or datetime — use YYYY-MM-DDTHH:MM"
        else:
            u = DoctorUnavailability(
                doctor_id=doctor_id,
                from_datetime=frm,
                to_datetime=to,
                reason=request.form.get("reason"),
                created_by="coordinator",
            )
            db.session.add(u)
            db.session.commit()
            affected = svc.appointments_affected_by(u)
            svc.raise_conflict(
                "DOCTOR_UNAVAILABLE",
                severity="HIGH",
                detected_by="HMS",
                detail=f"Doctor {doctor_id} unavailable {frm} to {to}; {len(affected)} appointments affected",
            )
            message = (
                f"Unavailability saved. {len(affected)} appointment(s) affected — "
                "a DOCTOR_UNAVAILABLE conflict is now open for the bot."
            )

    doctors = Doctor.query.order_by(Doctor.department_id, Doctor.full_name).all()
    unavail = (
        DoctorUnavailability.query.order_by(DoctorUnavailability.from_datetime.desc())
        .limit(25)
        .all()
    )
    return render_template("doctors.html", **_ctx(doctors=doctors, unavail=unavail,
                                                  message=message))


# ---------------------------------------------------------------------------
# A8. Dashboard
# ---------------------------------------------------------------------------
@ui.route("/admin/dashboard")
def dashboard():
    today = date.today()
    start = datetime.combine(today, datetime.min.time())
    end = start + timedelta(days=1)

    todays = Appointment.query.filter(
        Appointment.appointment_datetime >= start,
        Appointment.appointment_datetime < end,
        Appointment.status.in_(Appointment.ACTIVE_STATUSES),
    ).count()

    total_active = Appointment.query.filter(
        Appointment.status.in_(Appointment.ACTIVE_STATUSES)
    ).count()
    by_bot = Appointment.query.filter(
        Appointment.source == "BOT",
        Appointment.status.in_(Appointment.ACTIVE_STATUSES),
    ).count()

    # Reminder coverage: of appointments in the next 24h that are past their
    # T-24h moment, how many have a reminder logged?
    horizon = utcnow() + timedelta(hours=24)
    upcoming = Appointment.query.filter(
        Appointment.appointment_datetime > utcnow(),
        Appointment.appointment_datetime <= horizon,
        Appointment.status.in_(Appointment.ACTIVE_STATUSES),
    ).all()
    reminded = sum(
        1 for a in upcoming if any(n.status == "SENT" and n.type.startswith("REMINDER") for n in a.notifications)
    )
    coverage = round(100 * reminded / len(upcoming), 1) if upcoming else None

    stats = {
        "todays_appointments": todays,
        "total_active": total_active,
        "booked_by_bot": by_bot,
        "booked_manually": total_active - by_bot,
        "bot_share": round(100 * by_bot / total_active, 1) if total_active else 0,
        "open_conflicts": ConflictLog.query.filter_by(status="OPEN").count(),
        "escalated_conflicts": ConflictLog.query.filter_by(status="ESCALATED").count(),
        "unsynced_to_excel": Appointment.query.filter(
            Appointment.synced_to_excel_at.is_(None),
            Appointment.status.in_(Appointment.ACTIVE_STATUSES),
        ).count(),
        "reminder_coverage": coverage,
        "upcoming_24h": len(upcoming),
        "notifications_sent": Notification.query.filter_by(status="SENT").count(),
        "notifications_failed": Notification.query.filter_by(status="FAILED").count(),
        "pending_queue_push": BookingRequest.query.filter_by(queue_status="PENDING_PUSH").count(),
        "requests_total": BookingRequest.query.count(),
        "patients": Patient.query.count(),
        "doctors": Doctor.query.filter_by(is_active=True).count(),
        "available_slots": Slot.query.filter_by(status="AVAILABLE").count(),
    }

    recent = (
        Appointment.query.order_by(Appointment.created_at.desc()).limit(10).all()
    )
    recent_conflicts = (
        ConflictLog.query.order_by(ConflictLog.detected_at.desc()).limit(8).all()
    )
    return render_template("dashboard.html", **_ctx(stats=stats, recent=recent,
                                                    recent_conflicts=recent_conflicts,
                                                    rules_version=RULES.version))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_date(v, default):
    if not v:
        return default
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return default


def _explain(code: str) -> str:
    return {
        "SLOT_TAKEN": "That slot was booked by another transaction",
        "SLOT_LOCKED": "That slot is locked by another robot",
        "SLOT_BLOCKED": "That slot is blocked (doctor unavailable or reserved for urgent cases)",
        "SLOT_NOT_FOUND": "No such slot",
        "PATIENT_NOT_FOUND": "No such patient",
        "APPOINTMENT_NOT_FOUND": "No such appointment",
        "DOCTOR_INACTIVE": "That doctor is not active",
        "DAILY_CAP_REACHED": "The daily cap for that doctor or department is reached",
        "TOO_CLOSE_TO_RESCHEDULE": (
            f"Inside the {RULES.min_reschedule_notice_hours}h notice period — "
            "escalate to a clerk instead"
        ),
        "URGENT_NEVER_AUTO_MOVED": "Urgent appointments are never moved automatically",
        "AMBIGUOUS_PRIORITY": (
            f"Already rescheduled {RULES.max_reschedules_per_appointment} times — "
            "escalate instead of moving again"
        ),
        "VALIDATION_ERROR": "The submitted data is not valid",
    }.get(code, code)
