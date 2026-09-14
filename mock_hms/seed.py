"""Seeds the Mock HMS with realistic data.

    python -m mock_hms.seed            # rebuild from scratch
    python -m mock_hms.seed --keep     # only add what is missing

Produces what docs/phase1/data_model.md specifies: 3 departments, 10 doctors,
50 patients (from data/seed_patients.csv so the set is identical on every
machine), a 30-day slot horizon, ~80 pre-booked appointments so the schedule
looks realistically busy, and two unavailability rows — one already swept, one
left pending as Conflict Bot demo fodder.
"""
from __future__ import annotations

import argparse
import csv
import random
import sys
from datetime import datetime, date, time, timedelta
from pathlib import Path

from .models import (
    db,
    Department,
    Doctor,
    Patient,
    Slot,
    Appointment,
    DoctorUnavailability,
    ConflictLog,
    utcnow,
)
from . import services as svc
from .rules import RULES

REPO_ROOT = Path(__file__).resolve().parent.parent
PATIENTS_CSV = REPO_ROOT / "data" / "seed_patients.csv"

DEPARTMENTS = [
    {"code": "CARD", "name": "Cardiology", "default_slot_minutes": 20,
     "opens_at": time(9, 0), "closes_at": time(17, 0), "max_daily_appointments": 60},
    {"code": "ORTHO", "name": "Orthopaedics", "default_slot_minutes": 30,
     "opens_at": time(9, 0), "closes_at": time(17, 0), "max_daily_appointments": 40},
    {"code": "GEN", "name": "General Medicine", "default_slot_minutes": 15,
     "opens_at": time(9, 0), "closes_at": time(17, 0), "max_daily_appointments": 80},
]

DOCTORS = [
    ("CARD", "Dr. Meera Raghavan", "KMC-10241", "Interventional cardiology", 20, 20, True),
    ("CARD", "Dr. Anil Deshpande", "KMC-10442", "Electrophysiology", 20, 18, True),
    ("CARD", "Dr. Sunita Kulkarni", "KMC-10788", "Heart failure clinic", 20, 16, False),
    ("ORTHO", "Dr. Rajiv Menon", "KMC-20115", "Joint replacement", 30, 14, True),
    ("ORTHO", "Dr. Farida Qureshi", "KMC-20337", "Sports injury", 30, 12, True),
    ("ORTHO", "Dr. Vikram Shetty", "KMC-20554", "Spine", 30, 12, False),
    ("GEN", "Dr. Priya Nair", "KMC-30162", "Internal medicine", 15, 26, True),
    ("GEN", "Dr. Suresh Iyer", "KMC-30279", "Diabetology", 15, 24, True),
    ("GEN", "Dr. Kavitha Rao", "KMC-30481", "General practice", 15, 24, True),
    ("GEN", "Dr. Harish Prasad", "KMC-30690", "Geriatrics", 15, 20, True),
]

REASONS = [
    "Chest pain follow-up", "Routine check-up", "Knee pain", "Blood pressure review",
    "Post-surgery review", "Diabetes management", "Shoulder injury", "Breathlessness",
    "Back pain", "Medication review", "ECG follow-up", "Fever and fatigue",
]


# ---------------------------------------------------------------------------
def seed_departments() -> list[Department]:
    out = []
    for d in DEPARTMENTS:
        row = Department.query.filter_by(code=d["code"]).first()
        if row is None:
            row = Department(**d)
            db.session.add(row)
        out.append(row)
    db.session.commit()
    return out


def seed_doctors() -> list[Doctor]:
    depts = {d.code: d for d in Department.query.all()}
    out = []
    for code, name, reg, spec, mins, cap, urgent in DOCTORS:
        row = Doctor.query.filter_by(registration_no=reg).first()
        if row is None:
            row = Doctor(
                department_id=depts[code].department_id,
                full_name=name,
                registration_no=reg,
                specialisation=spec,
                consult_minutes=mins,
                daily_slot_cap=cap,
                accepts_urgent=urgent,
                is_active=True,
            )
            db.session.add(row)
        out.append(row)
    db.session.commit()
    return out


def seed_patients() -> list[Patient]:
    if not PATIENTS_CSV.exists():
        raise FileNotFoundError(
            f"{PATIENTS_CSV} is missing — it is committed to the repo and is what makes "
            "the seed identical on every machine."
        )

    out = []
    with open(PATIENTS_CSV, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            row = Patient.query.filter_by(phone=r["phone"]).first()
            if row is None:
                row = Patient(
                    mrn=r["mrn"],
                    full_name=r["full_name"],
                    phone=r["phone"],
                    email=r["email"] or None,
                    date_of_birth=date.fromisoformat(r["date_of_birth"]),
                    gender=r["gender"],
                    preferred_channel=r["preferred_channel"],
                    sms_consent=r["sms_consent"].lower() == "true",
                    email_consent=r["email_consent"].lower() == "true",
                    preferred_language=r["preferred_language"],
                )
                db.session.add(row)
            out.append(row)
    db.session.commit()
    return out


def seed_slots(horizon_days: int = 30) -> int:
    """Generate the bookable calendar: weekdays only, clinic hours, with the
    last `emergency_reserve_per_day` slots of each day held for urgent cases."""
    doctors = Doctor.query.filter_by(is_active=True).all()
    created = 0
    today = date.today()

    for doc in doctors:
        dept = doc.department
        for offset in range(horizon_days):
            day = today + timedelta(days=offset)
            if not RULES.allow_weekend_booking and day.weekday() >= 5:
                continue

            day_slots = []
            cursor = datetime.combine(day, dept.opens_at)
            close = datetime.combine(day, dept.closes_at)
            step = timedelta(minutes=doc.consult_minutes)
            lunch_from = datetime.combine(day, time(13, 0))
            lunch_to = datetime.combine(day, time(13, 30))

            while cursor + step <= close and len(day_slots) < doc.daily_slot_cap:
                if not (lunch_from <= cursor < lunch_to):
                    day_slots.append((cursor.time(), (cursor + step).time()))
                cursor += step

            reserve_from = max(0, len(day_slots) - RULES.emergency_reserve_per_day)
            for idx, (start, end) in enumerate(day_slots):
                exists = Slot.query.filter_by(
                    doctor_id=doc.doctor_id, slot_date=day, start_time=start
                ).first()
                if exists:
                    continue
                db.session.add(
                    Slot(
                        doctor_id=doc.doctor_id,
                        slot_date=day,
                        start_time=start,
                        end_time=end,
                        status="AVAILABLE",
                        is_emergency_reserve=(
                            RULES.emergency_reserve_enabled and idx >= reserve_from
                        ),
                    )
                )
                created += 1
        db.session.commit()
    return created


def seed_appointments(target: int = 80, rng: random.Random | None = None) -> int:
    """Pre-book some slots so the schedule board is not suspiciously empty."""
    rng = rng or random.Random(20260914)
    patients = Patient.query.all()
    booked = 0
    attempts = 0

    while booked < target and attempts < target * 6:
        attempts += 1
        patient = rng.choice(patients)
        # Skip the first 2 days so the reminder windows have something to find
        # but nothing is inside the minimum booking lead time.
        day = date.today() + timedelta(days=rng.randint(1, 20))
        slots = Slot.query.filter(
            Slot.slot_date == day,
            Slot.status == "AVAILABLE",
            Slot.is_emergency_reserve.is_(False),
        ).all()
        if not slots:
            continue

        slot = rng.choice(slots)
        urgency = rng.choices(["NORMAL", "HIGH", "URGENT"], weights=[80, 15, 5])[0]
        is_follow_up = rng.random() < 0.25
        priority = svc.derive_priority(urgency, patient, is_follow_up)

        appt, err = svc.book_appointment(
            patient_id=patient.patient_id,
            slot_id=slot.slot_id,
            priority=priority,
            reason=rng.choice(REASONS),
            is_follow_up=is_follow_up,
            booked_by="seed",
            source="MANUAL" if rng.random() < 0.3 else "BOT",
        )
        if err is None:
            booked += 1
    return booked


def seed_unavailability() -> int:
    """One already swept, one left pending for the Conflict Bot to find."""
    doctors = Doctor.query.filter_by(is_active=True).all()
    if len(doctors) < 2:
        return 0

    made = 0
    past_day = date.today() + timedelta(days=2)
    if not DoctorUnavailability.query.first():
        db.session.add(
            DoctorUnavailability(
                doctor_id=doctors[0].doctor_id,
                from_datetime=datetime.combine(past_day, time(9, 0)),
                to_datetime=datetime.combine(past_day, time(11, 0)),
                reason="Planned leave (already processed)",
                is_processed=True,
                created_by="seed",
            )
        )
        made += 1

        pending_day = date.today() + timedelta(days=5)
        db.session.add(
            DoctorUnavailability(
                doctor_id=doctors[3].doctor_id,
                from_datetime=datetime.combine(pending_day, time(14, 0)),
                to_datetime=datetime.combine(pending_day, time(17, 0)),
                reason="Emergency surgery",
                is_processed=False,
                created_by="seed",
            )
        )
        made += 1
        db.session.commit()

        svc.raise_conflict(
            "DOCTOR_UNAVAILABLE",
            severity="HIGH",
            detected_by="seed",
            detail=f"Dr {doctors[3].full_name} unavailable {pending_day} 14:00-17:00 — unswept",
        )
    return made


def plant_double_booking() -> dict | None:
    """Plant one real double-booking for the Phase 3 acceptance test.

    Note what this does *not* do. It cannot put two appointments on one slot —
    the partial unique index refuses that even from raw SQL, which is the guard
    working exactly as designed. So it creates the kind of double-booking that
    is genuinely reachable: an overlapping slot on the same doctor's calendar,
    booked by a second patient. Two individually legal rows, one doctor, one
    moment in time.
    """
    base = (
        Appointment.query.filter(
            Appointment.status.in_(Appointment.ACTIVE_STATUSES),
            Appointment.appointment_datetime > utcnow() + timedelta(days=3),
        )
        .order_by(Appointment.appointment_datetime)
        .first()
    )
    if base is None:
        return None

    base_slot = db.session.get(Slot, base.slot_id)
    doctor = db.session.get(Doctor, base.doctor_id)

    # Halfway into the existing slot: guaranteed to overlap, and a distinct
    # start_time so uq_doctor_slot_time is satisfied.
    offset = max(5, doctor.consult_minutes // 2)
    new_start = (
        datetime.combine(base_slot.slot_date, base_slot.start_time)
        + timedelta(minutes=offset)
    )
    new_end = new_start + timedelta(minutes=doctor.consult_minutes)
    if Slot.query.filter_by(
        doctor_id=doctor.doctor_id, slot_date=base_slot.slot_date,
        start_time=new_start.time()
    ).first():
        return None

    overlap_slot = Slot(
        doctor_id=doctor.doctor_id,
        slot_date=base_slot.slot_date,
        start_time=new_start.time(),
        end_time=new_end.time(),
        status="AVAILABLE",
    )
    db.session.add(overlap_slot)
    db.session.commit()

    other = Patient.query.filter(Patient.patient_id != base.patient_id).first()
    victim, err = svc.book_appointment(
        patient_id=other.patient_id,
        slot_id=overlap_slot.slot_id,
        priority="NORMAL",
        reason="Planted overlap for the conflict demo",
        booked_by="seed",
        source="MANUAL",
    )
    if err:
        return None

    svc.raise_conflict(
        "DOUBLE_BOOKING",
        severity="HIGH",
        appointment_id=base.appointment_id,
        related_appointment_id=victim.appointment_id,
        slot_id=base.slot_id,
        detected_by="seed",
        detail=(
            f"Time overlap on {doctor.full_name} {base_slot.slot_date}: "
            f"{base.reference_no} at {base_slot.start_time.strftime('%H:%M')} and "
            f"{victim.reference_no} at {new_start.strftime('%H:%M')}"
        ),
    )
    return {
        "kind": "TIME_OVERLAP",
        "doctor": doctor.full_name,
        "date": base_slot.slot_date.isoformat(),
        "appointments": [base.reference_no, victim.reference_no],
        "times": [base_slot.start_time.strftime("%H:%M"), new_start.strftime("%H:%M")],
    }


# ---------------------------------------------------------------------------
def seed_all(app=None, horizon_days: int | None = None, appointments: int = 80,
             plant_conflict: bool = True) -> dict:
    horizon = horizon_days or RULES.max_booking_horizon_days

    depts = seed_departments()
    docs = seed_doctors()
    pats = seed_patients()
    slots = seed_slots(horizon)
    appts = seed_appointments(appointments)
    unavail = seed_unavailability()
    planted = plant_double_booking() if plant_conflict else None

    return {
        "departments": len(depts),
        "doctors": len(docs),
        "patients": len(pats),
        "slots_created": slots,
        "slots_total": Slot.query.count(),
        "appointments_booked": appts,
        "unavailability": unavail,
        "planted_double_booking": planted,
        "open_conflicts": ConflictLog.query.filter_by(status="OPEN").count(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Seed the Mock HMS")
    parser.add_argument("--keep", action="store_true",
                        help="add only what is missing instead of dropping the database")
    parser.add_argument("--days", type=int, default=None, help="slot horizon in days")
    parser.add_argument("--appointments", type=int, default=80)
    parser.add_argument("--no-conflict", action="store_true",
                        help="skip planting the demo double-booking")
    args = parser.parse_args(argv)

    from . import create_app

    app = create_app()
    with app.app_context():
        if not args.keep:
            db.drop_all()
            db.create_all()
        summary = seed_all(
            app,
            horizon_days=args.days,
            appointments=args.appointments,
            plant_conflict=not args.no_conflict,
        )

    print("Seed complete:")
    for k, v in summary.items():
        print(f"  {k:26} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
