"""SQLAlchemy models for the Mock HMS.

Implements docs/phase1/data_model.md exactly — 9 entities, same field names,
same constraints. The one piece worth pointing at is
`uq_active_appointment_per_slot`: a partial unique index that makes a
double-booking impossible at the database level, not merely unlikely at the
bot level. It is the last of the three guards described in
docs/phase2/bot_scheduling.md section 4.
"""
from datetime import datetime, date, time, timedelta

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, UniqueConstraint, text

db = SQLAlchemy()


def utcnow() -> datetime:
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# 1. Department
# ---------------------------------------------------------------------------
class Department(db.Model):
    __tablename__ = "department"

    department_id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(10), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    default_slot_minutes = db.Column(db.Integer, nullable=False, default=20)
    opens_at = db.Column(db.Time, nullable=False, default=time(9, 0))
    closes_at = db.Column(db.Time, nullable=False, default=time(17, 0))
    max_daily_appointments = db.Column(db.Integer, nullable=False, default=60)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    doctors = db.relationship("Doctor", back_populates="department")

    def to_dict(self):
        return {
            "department_id": self.department_id,
            "code": self.code,
            "name": self.name,
            "default_slot_minutes": self.default_slot_minutes,
            "opens_at": self.opens_at.strftime("%H:%M"),
            "closes_at": self.closes_at.strftime("%H:%M"),
            "max_daily_appointments": self.max_daily_appointments,
            "is_active": self.is_active,
        }


# ---------------------------------------------------------------------------
# 2. Doctor
# ---------------------------------------------------------------------------
class Doctor(db.Model):
    __tablename__ = "doctor"

    doctor_id = db.Column(db.Integer, primary_key=True)
    department_id = db.Column(
        db.Integer, db.ForeignKey("department.department_id"), nullable=False
    )
    full_name = db.Column(db.String(100), nullable=False)
    registration_no = db.Column(db.String(30), unique=True, nullable=False)
    specialisation = db.Column(db.String(100))
    consult_minutes = db.Column(db.Integer, nullable=False, default=20)
    works_days = db.Column(db.String(20), nullable=False, default="MON,TUE,WED,THU,FRI")
    daily_slot_cap = db.Column(db.Integer, nullable=False, default=20)
    accepts_urgent = db.Column(db.Boolean, nullable=False, default=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    department = db.relationship("Department", back_populates="doctors")
    slots = db.relationship("Slot", back_populates="doctor")

    def to_dict(self):
        return {
            "doctor_id": self.doctor_id,
            "department_id": self.department_id,
            "department_code": self.department.code if self.department else None,
            "full_name": self.full_name,
            "registration_no": self.registration_no,
            "specialisation": self.specialisation,
            "consult_minutes": self.consult_minutes,
            "works_days": self.works_days,
            "daily_slot_cap": self.daily_slot_cap,
            "accepts_urgent": self.accepts_urgent,
            "is_active": self.is_active,
        }


# ---------------------------------------------------------------------------
# 3. Patient
# ---------------------------------------------------------------------------
class Patient(db.Model):
    __tablename__ = "patient"

    patient_id = db.Column(db.Integer, primary_key=True)
    mrn = db.Column(db.String(20), unique=True, nullable=False)
    full_name = db.Column(db.String(100), nullable=False)
    # phone is the dedup key the Scheduling Bot searches on.
    phone = db.Column(db.String(20), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), index=True)
    date_of_birth = db.Column(db.Date, nullable=False)
    gender = db.Column(db.String(10), nullable=False)
    preferred_channel = db.Column(db.String(10), nullable=False, default="SMS")
    sms_consent = db.Column(db.Boolean, nullable=False, default=True)
    email_consent = db.Column(db.Boolean, nullable=False, default=True)
    preferred_language = db.Column(db.String(10), nullable=False, default="EN")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    appointments = db.relationship("Appointment", back_populates="patient")

    @property
    def age(self) -> int:
        today = date.today()
        born = self.date_of_birth
        return today.year - born.year - ((today.month, today.day) < (born.month, born.day))

    @property
    def is_senior(self) -> bool:
        """Stored as a derived property so the threshold stays in rules.yaml."""
        from .rules import RULES

        return self.age >= RULES.senior_age_threshold

    def to_dict(self):
        return {
            "patient_id": self.patient_id,
            "mrn": self.mrn,
            "full_name": self.full_name,
            "phone": self.phone,
            "email": self.email,
            "date_of_birth": self.date_of_birth.isoformat(),
            "age": self.age,
            "gender": self.gender,
            "is_senior": self.is_senior,
            "preferred_channel": self.preferred_channel,
            "sms_consent": self.sms_consent,
            "email_consent": self.email_consent,
            "preferred_language": self.preferred_language,
        }


# ---------------------------------------------------------------------------
# 4. Slot
# ---------------------------------------------------------------------------
class Slot(db.Model):
    __tablename__ = "slot"
    __table_args__ = (
        UniqueConstraint("doctor_id", "slot_date", "start_time", name="uq_doctor_slot_time"),
        Index("ix_slot_date_status", "slot_date", "status"),
    )

    STATUSES = ("AVAILABLE", "LOCKED", "BOOKED", "BLOCKED", "COMPLETED", "NO_SHOW")

    slot_id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(
        db.Integer, db.ForeignKey("doctor.doctor_id"), nullable=False, index=True
    )
    slot_date = db.Column(db.Date, nullable=False, index=True)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    status = db.Column(db.String(15), nullable=False, default="AVAILABLE")
    is_emergency_reserve = db.Column(db.Boolean, nullable=False, default=False)
    locked_until = db.Column(db.DateTime)
    locked_by = db.Column(db.String(50))
    # Bumped on every write. The Scheduling Bot re-reads it immediately before
    # submitting; a changed version means someone else touched the slot.
    version = db.Column(db.Integer, nullable=False, default=0)

    doctor = db.relationship("Doctor", back_populates="slots")
    appointments = db.relationship("Appointment", back_populates="slot")

    @property
    def start_datetime(self) -> datetime:
        return datetime.combine(self.slot_date, self.start_time)

    @property
    def is_lock_active(self) -> bool:
        return self.locked_until is not None and self.locked_until > utcnow()

    def to_dict(self):
        return {
            "slot_id": self.slot_id,
            "doctor_id": self.doctor_id,
            "doctor_name": self.doctor.full_name if self.doctor else None,
            "department_code": self.doctor.department.code if self.doctor else None,
            "slot_date": self.slot_date.isoformat(),
            "start_time": self.start_time.strftime("%H:%M"),
            "end_time": self.end_time.strftime("%H:%M"),
            "status": self.status,
            "is_emergency_reserve": self.is_emergency_reserve,
            "locked_until": self.locked_until.isoformat() if self.locked_until else None,
            "locked_by": self.locked_by,
            "version": self.version,
        }


# ---------------------------------------------------------------------------
# 5. Appointment
# ---------------------------------------------------------------------------
class Appointment(db.Model):
    __tablename__ = "appointment"
    __table_args__ = (
        # THE double-booking guard. Partial unique index: at most one
        # non-cancelled appointment may hold a given slot.
        Index(
            "uq_active_appointment_per_slot",
            "slot_id",
            unique=True,
            sqlite_where=text("status NOT IN ('CANCELLED','NO_SHOW')"),
        ),
        Index("ix_appointment_datetime_status", "appointment_datetime", "status"),
    )

    STATUSES = ("SCHEDULED", "CONFIRMED", "RESCHEDULED", "CANCELLED", "COMPLETED", "NO_SHOW")
    ACTIVE_STATUSES = ("SCHEDULED", "CONFIRMED", "RESCHEDULED")

    appointment_id = db.Column(db.Integer, primary_key=True)
    reference_no = db.Column(db.String(20), unique=True, nullable=False)
    patient_id = db.Column(
        db.Integer, db.ForeignKey("patient.patient_id"), nullable=False, index=True
    )
    slot_id = db.Column(db.Integer, db.ForeignKey("slot.slot_id"), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctor.doctor_id"), nullable=False)
    department_id = db.Column(
        db.Integer, db.ForeignKey("department.department_id"), nullable=False
    )
    appointment_datetime = db.Column(db.DateTime, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="SCHEDULED")
    priority = db.Column(db.String(10), nullable=False, default="NORMAL")
    reason = db.Column(db.String(255))
    is_follow_up = db.Column(db.Boolean, nullable=False, default=False)
    previous_appointment_id = db.Column(
        db.Integer, db.ForeignKey("appointment.appointment_id")
    )
    source = db.Column(db.String(20), nullable=False, default="BOT")
    queue_item_ref = db.Column(db.String(50))
    booked_by = db.Column(db.String(50), nullable=False, default="SchedulingBot")
    # Counts automatic moves. Capped by rules.max_reschedules_per_appointment
    # so a patient is never bounced repeatedly by successive conflicts.
    reschedule_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    synced_to_excel_at = db.Column(db.DateTime)

    patient = db.relationship("Patient", back_populates="appointments")
    slot = db.relationship("Slot", back_populates="appointments")
    doctor = db.relationship("Doctor")
    department = db.relationship("Department")
    notifications = db.relationship("Notification", back_populates="appointment")

    def to_dict(self, include_patient=False):
        d = {
            "appointment_id": self.appointment_id,
            "reference_no": self.reference_no,
            "patient_id": self.patient_id,
            "slot_id": self.slot_id,
            "doctor_id": self.doctor_id,
            "doctor_name": self.doctor.full_name if self.doctor else None,
            "department_id": self.department_id,
            "department": self.department.name if self.department else None,
            "appointment_datetime": self.appointment_datetime.isoformat(),
            "status": self.status,
            "priority": self.priority,
            "reason": self.reason,
            "is_follow_up": self.is_follow_up,
            "previous_appointment_id": self.previous_appointment_id,
            "source": self.source,
            "queue_item_ref": self.queue_item_ref,
            "booked_by": self.booked_by,
            "reschedule_count": self.reschedule_count,
            "created_at": self.created_at.isoformat(),
            "synced_to_excel_at": (
                self.synced_to_excel_at.isoformat() if self.synced_to_excel_at else None
            ),
        }
        if include_patient and self.patient:
            d["patient"] = self.patient.to_dict()
        return d


# ---------------------------------------------------------------------------
# 6. Notification
# ---------------------------------------------------------------------------
class Notification(db.Model):
    __tablename__ = "notification"
    __table_args__ = (
        # Stops a re-run of the Reminder Bot double-sending.
        UniqueConstraint(
            "appointment_id", "type", "attempt_no", name="uq_notification_attempt"
        ),
    )

    TYPES = ("CONFIRMATION", "REMINDER_24H", "REMINDER_2H", "RESCHEDULE", "CANCELLATION")

    notification_id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(
        db.Integer, db.ForeignKey("appointment.appointment_id"), nullable=False, index=True
    )
    patient_id = db.Column(db.Integer, db.ForeignKey("patient.patient_id"), nullable=False)
    type = db.Column(db.String(20), nullable=False)
    channel = db.Column(db.String(10), nullable=False)
    recipient = db.Column(db.String(120), nullable=False)
    template_id = db.Column(db.String(30), nullable=False)
    body_preview = db.Column(db.String(255))
    status = db.Column(db.String(15), nullable=False, default="PENDING")
    provider = db.Column(db.String(20))
    provider_message_id = db.Column(db.String(64))
    attempt_no = db.Column(db.Integer, nullable=False, default=1)
    error_code = db.Column(db.String(50))
    sent_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    appointment = db.relationship("Appointment", back_populates="notifications")

    def to_dict(self):
        return {
            "notification_id": self.notification_id,
            "appointment_id": self.appointment_id,
            "patient_id": self.patient_id,
            "type": self.type,
            "channel": self.channel,
            "recipient": self.recipient,
            "template_id": self.template_id,
            "status": self.status,
            "provider": self.provider,
            "provider_message_id": self.provider_message_id,
            "attempt_no": self.attempt_no,
            "error_code": self.error_code,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }


# ---------------------------------------------------------------------------
# 7. DoctorUnavailability
# ---------------------------------------------------------------------------
class DoctorUnavailability(db.Model):
    __tablename__ = "doctor_unavailability"

    unavailability_id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(
        db.Integer, db.ForeignKey("doctor.doctor_id"), nullable=False, index=True
    )
    from_datetime = db.Column(db.DateTime, nullable=False)
    to_datetime = db.Column(db.DateTime, nullable=False)
    reason = db.Column(db.String(100))
    # FALSE means the Conflict Bot has not swept it yet.
    is_processed = db.Column(db.Boolean, nullable=False, default=False)
    created_by = db.Column(db.String(50), nullable=False, default="coordinator")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    doctor = db.relationship("Doctor")

    def to_dict(self):
        return {
            "unavailability_id": self.unavailability_id,
            "doctor_id": self.doctor_id,
            "doctor_name": self.doctor.full_name if self.doctor else None,
            "from_datetime": self.from_datetime.isoformat(),
            "to_datetime": self.to_datetime.isoformat(),
            "reason": self.reason,
            "is_processed": self.is_processed,
            "created_by": self.created_by,
        }


# ---------------------------------------------------------------------------
# 8. AppointmentAudit
# ---------------------------------------------------------------------------
class AppointmentAudit(db.Model):
    __tablename__ = "appointment_audit"

    audit_id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(
        db.Integer, db.ForeignKey("appointment.appointment_id"), nullable=False, index=True
    )
    action = db.Column(db.String(30), nullable=False)
    actor = db.Column(db.String(50), nullable=False)
    from_value = db.Column(db.String(255))
    to_value = db.Column(db.String(255))
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def to_dict(self):
        return {
            "audit_id": self.audit_id,
            "appointment_id": self.appointment_id,
            "action": self.action,
            "actor": self.actor,
            "from_value": self.from_value,
            "to_value": self.to_value,
            "note": self.note,
            "created_at": self.created_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# 9. ConflictLog
# ---------------------------------------------------------------------------
class ConflictLog(db.Model):
    __tablename__ = "conflict_log"

    TYPES = ("DOUBLE_BOOKING", "DOCTOR_UNAVAILABLE", "CANCELLATION_CASCADE", "EXCEL_MISMATCH")

    conflict_id = db.Column(db.Integer, primary_key=True)
    conflict_type = db.Column(db.String(30), nullable=False)
    severity = db.Column(db.String(10), nullable=False, default="MEDIUM")
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointment.appointment_id"))
    related_appointment_id = db.Column(db.Integer, db.ForeignKey("appointment.appointment_id"))
    slot_id = db.Column(db.Integer, db.ForeignKey("slot.slot_id"))
    detected_by = db.Column(db.String(30), nullable=False, default="ReconcileSweep")
    resolution = db.Column(db.String(30))
    resolution_note = db.Column(db.String(255))
    status = db.Column(db.String(15), nullable=False, default="OPEN")
    detail = db.Column(db.String(255))
    detected_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    resolved_at = db.Column(db.DateTime)

    def to_dict(self):
        return {
            "conflict_id": self.conflict_id,
            "conflict_type": self.conflict_type,
            "severity": self.severity,
            "appointment_id": self.appointment_id,
            "related_appointment_id": self.related_appointment_id,
            "slot_id": self.slot_id,
            "detected_by": self.detected_by,
            "resolution": self.resolution,
            "resolution_note": self.resolution_note,
            "status": self.status,
            "detail": self.detail,
            "detected_at": self.detected_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


# ---------------------------------------------------------------------------
# 10. BookingRequest — intake row, mirrors the queue item
# ---------------------------------------------------------------------------
class BookingRequest(db.Model):
    """What the patient submitted, before a bot has done anything with it.

    Kept separately from Appointment so a request that never became a booking
    (no slot, invalid data) is still visible on the dashboard.
    """

    __tablename__ = "booking_request"

    request_id = db.Column(db.Integer, primary_key=True)
    reference = db.Column(db.String(20), unique=True, nullable=False)
    patient_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    email = db.Column(db.String(120))
    department_code = db.Column(db.String(10), nullable=False)
    preferred_doctor_id = db.Column(db.Integer)
    preferred_date_from = db.Column(db.Date, nullable=False)
    preferred_date_to = db.Column(db.Date, nullable=False)
    time_band = db.Column(db.String(15), nullable=False, default="ANY")
    urgency = db.Column(db.String(10), nullable=False, default="NORMAL")
    is_follow_up = db.Column(db.Boolean, nullable=False, default=False)
    reason = db.Column(db.String(255))
    # NEW -> PENDING_PUSH -> QUEUED -> PROCESSED / FAILED
    queue_status = db.Column(db.String(20), nullable=False, default="NEW")
    queue_item_id = db.Column(db.String(50))
    push_error = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    def to_queue_payload(self):
        """The SpecificContent of the Orchestrator queue item."""
        return {
            "RequestId": self.reference,
            "PatientName": self.patient_name,
            "Phone": self.phone,
            "Email": self.email,
            "Department": self.department_code,
            "PreferredDoctor": str(self.preferred_doctor_id) if self.preferred_doctor_id else "Any",
            "PreferredDateFrom": self.preferred_date_from.isoformat(),
            "PreferredDateTo": self.preferred_date_to.isoformat(),
            "TimeBand": self.time_band,
            "Urgency": self.urgency,
            "IsFollowUp": self.is_follow_up,
            "Reason": self.reason or "",
        }

    def to_dict(self):
        d = self.to_queue_payload()
        d.update(
            {
                "request_id": self.request_id,
                "queue_status": self.queue_status,
                "queue_item_id": self.queue_item_id,
                "push_error": self.push_error,
                "created_at": self.created_at.isoformat(),
            }
        )
        return d
