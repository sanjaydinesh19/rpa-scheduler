"""End-to-end tests. Each drives the same REST flow the corresponding .xaml
would drive, and asserts the HMS ends up in the expected state.

Run:
    pytest tests -v
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from tests.bot_engine import (
    BusinessRule,
    HmsClient,
    allocate_slot,
    conflict_resolve,
    conflict_sweep,
    process_booking_request,
    reminder_run,
    resolve_channels,
    scheduling_run,
    _derive_priority,
)


# ---------------------------------------------------------------------------
# HMS smoke
# ---------------------------------------------------------------------------
def test_health_ok(hms: HmsClient):
    r = hms.request("GET", "/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["counts"]["doctors"] == 10
    assert body["counts"]["patients"] >= 50


# ---------------------------------------------------------------------------
# Priority derivation (unit)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("urgency,age,follow,expected", [
    ("URGENT", 30, False, "URGENT"),
    ("HIGH", 30, False, "HIGH"),
    ("NORMAL", 71, False, "HIGH"),
    ("NORMAL", 30, True, "HIGH"),
    ("NORMAL", 30, False, "NORMAL"),
])
def test_derive_priority(urgency, age, follow, expected):
    assert _derive_priority(urgency, age, follow) == expected


# ---------------------------------------------------------------------------
# Channel resolution (unit)
# ---------------------------------------------------------------------------
def test_resolve_channels_prefers_sms():
    assert resolve_channels("REMINDER_24H", "NORMAL", "SMS", True, True,
                            "+919876543210", "a@b.com") == ["SMS"]


def test_resolve_channels_urgent_2h_escalates_to_both():
    assert resolve_channels("REMINDER_2H", "URGENT", "SMS", True, True,
                            "+919876543210", "a@b.com") == ["SMS", "EMAIL"]


def test_resolve_channels_no_consent_returns_empty():
    assert resolve_channels("REMINDER_24H", "NORMAL", "SMS", False, False,
                            "+919876543210", "a@b.com") == []


def test_resolve_channels_falls_back_when_preferred_missing():
    # Preferred SMS but no phone consent -> EMAIL
    assert resolve_channels("REMINDER_24H", "NORMAL", "SMS", False, True,
                            "+919876543210", "a@b.com") == ["EMAIL"]


# ---------------------------------------------------------------------------
# Allocation (unit)
# ---------------------------------------------------------------------------
def _fake_slot(slot_id: int, doctor_id: int, days_out: int, hour: int, version: int = 0):
    d = (datetime.today() + timedelta(days=days_out)).date().isoformat()
    return {
        "slot_id": slot_id,
        "doctor_id": doctor_id,
        "slot_date": d,
        "start_time": f"{hour:02d}:00",
        "end_time": f"{hour + 1:02d}:00",
        "status": "AVAILABLE",
        "version": version,
    }


def test_allocate_prefers_preferred_doctor_and_band():
    slots = [
        _fake_slot(1, doctor_id=7, days_out=3, hour=14),  # AFTERNOON, preferred
        _fake_slot(2, doctor_id=8, days_out=1, hour=10),  # MORNING, other doctor
    ]
    slot, pass_no = allocate_slot(slots, set(), "NORMAL", 7, "AFTERNOON")
    assert slot["slot_id"] == 1
    assert pass_no == 1


def test_allocate_first_fit_when_no_preference_matches():
    slots = [
        _fake_slot(1, doctor_id=8, days_out=5, hour=15),
        _fake_slot(2, doctor_id=8, days_out=1, hour=10),
    ]
    slot, pass_no = allocate_slot(slots, set(), "NORMAL", 0, "ANY")
    assert slot["slot_id"] == 2  # earlier date wins
    # With no preferred doctor and no preferred band, every pass has the same
    # filter, so the first pass finds a candidate.
    assert pass_no == 1


def test_allocate_excludes_blocked_slots():
    slots = [
        _fake_slot(1, doctor_id=8, days_out=1, hour=10),
        _fake_slot(2, doctor_id=8, days_out=2, hour=10),
    ]
    slot, _ = allocate_slot(slots, {1}, "NORMAL", 0, "ANY")
    assert slot["slot_id"] == 2


def test_allocate_returns_none_when_nothing_eligible():
    slot, pass_no = allocate_slot([], set(), "NORMAL", 0, "ANY")
    assert slot is None
    assert pass_no == 0


# ---------------------------------------------------------------------------
# Scheduling — end to end
# ---------------------------------------------------------------------------
def _new_booking_request(hms: HmsClient, phone: str = "+919000000101") -> dict:
    today = datetime.today().date()
    payload = {
        "patient_name": "Test Patient",
        "phone": phone,
        "email": "test@example.com",
        "department_code": "CARD",
        "preferred_date_from": (today + timedelta(days=1)).isoformat(),
        "preferred_date_to": (today + timedelta(days=7)).isoformat(),
        "time_band": "ANY",
        "urgency": "NORMAL",
        "is_follow_up": False,
        "reason": "Routine follow-up",
    }
    r = hms.request("POST", "/booking-requests", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def test_full_booking_flow(hms: HmsClient):
    br = _new_booking_request(hms)
    assert br["queue_status"] == "PENDING_PUSH"

    summary = scheduling_run(hms, max_transactions=5)
    assert summary["processed"] >= 1
    assert summary["failed"] == 0
    out = next(o for o in summary["outputs"] if True)
    assert out["reference_no"].startswith("APT-")
    assert out["slot_id"] > 0

    # Booking-request marked PROCESSED
    r = hms.request("GET", f"/booking-requests/{br['RequestId']}")
    assert r.status_code == 200
    assert r.json()["queue_status"] == "PROCESSED"

    # Appointment exists and is SCHEDULED, with a confirmation notification
    r = hms.request("GET", f"/appointments/{out['reference_no']}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "SCHEDULED"
    assert body["synced_to_excel_at"] is not None

    r = hms.request("GET", f"/notifications?appointment_id={out['appointment_id']}&type=CONFIRMATION")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["status"] == "SENT"


def test_double_booking_guard_at_hms(hms: HmsClient):
    """The partial unique index refuses two active appointments on one slot,
    even outside the bot flow. This is guard #3 from bot_scheduling.md §4."""
    # Grab any available future slot
    r = hms.request("GET", "/slots?status=AVAILABLE&limit=1")
    slots = r.json()
    assert slots, "expected at least one available slot in the seed"
    slot_id = slots[0]["slot_id"]

    # Grab two different patients
    r = hms.request("GET", "/patients?name=")
    patients = r.json()
    assert len(patients) >= 2
    p1, p2 = patients[0]["patient_id"], patients[1]["patient_id"]

    r = hms.request("POST", "/appointments",
                    json={"patient_id": p1, "slot_id": slot_id, "priority": "NORMAL"})
    assert r.status_code == 201, r.text

    r = hms.request("POST", "/appointments",
                    json={"patient_id": p2, "slot_id": slot_id, "priority": "NORMAL"})
    assert r.status_code in (409, 423)
    assert r.json()["error_code"] in ("SLOT_TAKEN", "SLOT_LOCKED")


def test_business_exception_marks_request_failed(hms: HmsClient):
    """VALIDATION_ERROR (bad phone) is a business exception — the bot should
    mark FAILED and continue, never retry."""
    # A bad-phone request cannot be created via the HMS validator, so simulate
    # by processing a synthetic dict directly.
    bad = {
        "RequestId": "REQ-BAD-0001",
        "PatientName": "Test Bad",
        "Phone": "not-e164",
        "Email": "",
        "Department": "CARD",
        "PreferredDoctor": "Any",
        "PreferredDateFrom": (datetime.today() + timedelta(days=1)).date().isoformat(),
        "PreferredDateTo": (datetime.today() + timedelta(days=3)).date().isoformat(),
        "TimeBand": "ANY",
        "Urgency": "NORMAL",
        "IsFollowUp": False,
        "Reason": "",
    }
    with pytest.raises(BusinessRule):
        process_booking_request(hms, bad)


# ---------------------------------------------------------------------------
# Reminder — end to end (time-travelling)
# ---------------------------------------------------------------------------
def test_reminder_flow_end_to_end(hms: HmsClient):
    # Book something first so there IS a future appointment to remind
    _new_booking_request(hms, phone="+919000000201")
    scheduling_run(hms, max_transactions=5)

    # Pick any active future appointment
    r = hms.request("GET", "/appointments?status=SCHEDULED&limit=200")
    appts = [a for a in r.json() if datetime.fromisoformat(a["appointment_datetime"]) > datetime.now()]
    assert appts, "expected at least one scheduled future appointment"
    target = appts[0]
    appt_dt = datetime.fromisoformat(target["appointment_datetime"])

    # Time-travel to T-24h and run reminder
    now = appt_dt - timedelta(hours=24)
    if now.hour < 7 or now.hour >= 21:
        now = now.replace(hour=10, minute=0, second=0, microsecond=0)
    summary = reminder_run(hms, now=now)
    assert summary["sent"] >= 1

    # Re-run at the same instant. The HMS pre-filters items that already have a
    # SENT notification of the same type, so /reminders/due returns nothing.
    # Zero sent is proof enough of idempotency.
    summary2 = reminder_run(hms, now=now)
    assert summary2["sent"] == 0


def test_reminder_quiet_hours(hms: HmsClient):
    quiet = datetime.now().replace(hour=3, minute=0)
    summary = reminder_run(hms, now=quiet)
    assert summary["sent"] == 0
    assert summary.get("reason") == "QUIET_HOURS"


# ---------------------------------------------------------------------------
# Conflict resolution — end to end
# ---------------------------------------------------------------------------
def test_double_booking_detected_and_resolved(hms: HmsClient):
    # The seed already plants one reachable double-booking; sweep raises it.
    sweep = conflict_sweep(hms)
    assert sweep["detected"] >= 1

    resolution = conflict_resolve(hms)
    assert resolution["resolved"] + resolution["escalated"] >= 1

    # After resolution, no OPEN double-bookings remain
    r = hms.request("GET", "/conflicts?status=OPEN&type=DOUBLE_BOOKING")
    remaining = [c for c in r.json() if c["conflict_type"] == "DOUBLE_BOOKING"]
    assert len(remaining) == 0, f"still open: {remaining}"


def test_cancellation_cascade_marked_resolved(hms: HmsClient):
    # Cancel any active future appointment; the HMS raises CANCELLATION_CASCADE
    r = hms.request("GET", "/appointments?status=SCHEDULED&limit=200")
    appts = [a for a in r.json() if datetime.fromisoformat(a["appointment_datetime"]) > datetime.now() + timedelta(hours=6)]
    assert appts
    target = appts[0]

    r = hms.request("POST", f"/appointments/{target['appointment_id']}/cancel",
                    json={"actor": "test", "note": "e2e"})
    assert r.status_code == 200

    resolution = conflict_resolve(hms)
    assert resolution["resolved"] >= 1

    r = hms.request("GET", "/conflicts?status=OPEN&type=CANCELLATION_CASCADE")
    assert len([c for c in r.json() if c["appointment_id"] == target["appointment_id"]]) == 0


def test_doctor_unavailable_escalates(hms: HmsClient):
    r = hms.request("POST", "/admin/plant-unavailability")
    assert r.status_code == 200, r.text

    resolution = conflict_resolve(hms)
    assert resolution["escalated"] >= 1
