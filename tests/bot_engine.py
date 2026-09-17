"""Python reference implementation of the three UiPath bots.

Each function makes the same REST calls, in the same order, with the same
decision logic as the corresponding .xaml. The tests drive these functions and
assert the HMS ends up in the state the XAML would have produced.

Kept intentionally small and prose-free: the .xaml files carry the design
annotations, this file is only here so pytest can verify the flow end to end
without spinning up UiPath Studio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import requests

E164 = re.compile(r"^\+[1-9]\d{9,14}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class BusinessRule(Exception):
    """Raised for VALIDATION_ERROR, NO_SLOT_AVAILABLE, SLOT_CONTENTION —
    what the XAML would throw as UiPath.Core.BusinessRuleException."""


@dataclass
class HmsClient:
    base_url: str
    api_key: str = ""
    timeout: float = 30.0

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/api/v1{path}"

    def _headers(self) -> dict:
        h = {"Accept": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def request(self, method: str, path: str, json: Any = None) -> requests.Response:
        return requests.request(
            method, self._url(path), json=json, headers=self._headers(), timeout=self.timeout
        )


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
def _derive_priority(urgency: str, age: int, is_follow_up: bool) -> str:
    urgency = (urgency or "NORMAL").upper()
    if urgency == "URGENT":
        return "URGENT"
    if urgency == "HIGH" or age >= 60 or is_follow_up:
        return "HIGH"
    return "NORMAL"


def _score_slot(slot: dict, priority: str, preferred_doctor_id: int, band: str) -> float:
    w_earliest, w_urg, w_band, w_doc = 1.0, 2.0, 0.8, 1.5
    slot_date = datetime.fromisoformat(slot["slot_date"])
    days_out = (slot_date - datetime.now()).days
    hh, mm = [int(x) for x in slot["start_time"].split(":")[:2]]
    start_min = hh * 60 + mm
    band_lo, band_hi = _band_range(band)
    in_band = band_lo <= start_min <= band_hi
    score = w_earliest * (30 - days_out)
    if priority == "URGENT":
        score += w_urg * (30 - days_out)
    if in_band:
        score += w_band
    if preferred_doctor_id and int(slot["doctor_id"]) == preferred_doctor_id:
        score += w_doc
    return score


def _band_range(band: str) -> tuple[int, int]:
    band = (band or "ANY").upper()
    if band == "MORNING":
        return (9 * 60, 12 * 60)
    if band == "AFTERNOON":
        return (12 * 60, 17 * 60)
    return (0, 24 * 60)


def allocate_slot(slots: list[dict], excluded: set[int], priority: str,
                  preferred_doctor_id: int, time_band: str) -> tuple[dict | None, int]:
    """Four-pass allocation. Returns (slot, pass_number) or (None, 0)."""
    now = datetime.now()
    band_lo, band_hi = _band_range(time_band)
    prefers_band = time_band and time_band.upper() != "ANY"
    prefers_doctor = preferred_doctor_id > 0

    def eligible(s: dict, require_doc: bool, require_band: bool) -> bool:
        if s["slot_id"] in excluded or s["status"] != "AVAILABLE":
            return False
        slot_start = datetime.fromisoformat(f"{s['slot_date']}T{s['start_time']}:00")
        if slot_start <= now + timedelta(hours=2):
            return False
        if require_doc and int(s["doctor_id"]) != preferred_doctor_id:
            return False
        if require_band:
            hh, mm = [int(x) for x in s["start_time"].split(":")[:2]]
            if not (band_lo <= hh * 60 + mm <= band_hi):
                return False
        return True

    passes = [
        (prefers_doctor, prefers_band),
        (prefers_doctor, False),
        (False, prefers_band),
        (False, False),
    ]
    for pass_no, (rd, rb) in enumerate(passes, start=1):
        cands = [s for s in slots if eligible(s, rd, rb)]
        if not cands:
            continue
        cands.sort(
            key=lambda s: (
                -_score_slot(s, priority, preferred_doctor_id, time_band),
                s["slot_date"],
                s["start_time"],
                s["slot_id"],
            )
        )
        return cands[0], pass_no
    return None, 0


def process_booking_request(cli: HmsClient, req: dict, max_attempts: int = 3) -> dict:
    """Simulates SchedulingBot/Main.xaml for one BookingRequest."""
    phone = req["Phone"]
    if not E164.match(phone):
        raise BusinessRule("VALIDATION_ERROR: phone not E.164")

    d_from = datetime.fromisoformat(req["PreferredDateFrom"]).date()
    d_to = datetime.fromisoformat(req["PreferredDateTo"]).date()
    if d_from > d_to:
        raise BusinessRule("VALIDATION_ERROR: date range reversed")
    if req["Urgency"] not in ("NORMAL", "HIGH", "URGENT"):
        raise BusinessRule("VALIDATION_ERROR: bad urgency")

    # Find or register patient
    r = cli.request("GET", f"/patients?phone={phone}")
    r.raise_for_status()
    found = r.json()
    if found:
        patient = found[0]
    else:
        r = cli.request("POST", "/patients", json={
            "full_name": req["PatientName"],
            "phone": phone,
            "email": req.get("Email") or None,
            "date_of_birth": (datetime.today() - timedelta(days=35 * 365)).strftime("%Y-%m-%d"),
            "gender": "O",
            "preferred_channel": "SMS",
        })
        if r.status_code >= 400:
            raise BusinessRule(f"PATIENT_REGISTRATION_FAILED: {r.json().get('error_code')}")
        patient = r.json()

    priority = _derive_priority(req["Urgency"], int(patient["age"]), bool(req.get("IsFollowUp")))
    preferred_doctor = 0
    pd = req.get("PreferredDoctor")
    if pd and str(pd).isdigit():
        preferred_doctor = int(pd)

    # Search slots
    include_reserve = "true" if priority == "URGENT" else "false"
    r = cli.request(
        "GET",
        f"/slots?department={req['Department']}&date_from={d_from}&date_to={d_to}"
        f"&status=AVAILABLE&include_reserve={include_reserve}&limit=500",
    )
    r.raise_for_status()
    slots = r.json()
    if not slots:
        raise BusinessRule("NO_SLOT_AVAILABLE")

    # Attempt loop
    excluded: set[int] = set()
    for attempt in range(1, max_attempts + 1):
        slot, pass_no = allocate_slot(
            slots, excluded, priority, preferred_doctor, req.get("TimeBand") or "ANY"
        )
        if not slot:
            raise BusinessRule("NO_SLOT_AVAILABLE")

        slot_id = int(slot["slot_id"])
        # Lock
        r = cli.request("POST", f"/slots/{slot_id}/lock", json={"locked_by": "SchedulingBot"})
        if r.status_code >= 400:
            excluded.add(slot_id)
            continue
        try:
            # Book. No expected_version: lock() bumps it, so the value we read
            # at allocate time is always stale by the time we get here. The lock
            # itself + slot.status check + the partial unique index cover the
            # three race guards.
            r = cli.request("POST", "/appointments", json={
                "patient_id": patient["patient_id"],
                "slot_id": slot_id,
                "priority": priority,
                "reason": req.get("Reason") or "",
                "is_follow_up": bool(req.get("IsFollowUp")),
                "queue_item_ref": req["RequestId"],
                "booked_by": "SchedulingBot",
            })
            if r.status_code < 300:
                appt = r.json()
                # Mark synced-to-excel (real bot appends to xlsx here too)
                cli.request(
                    "PATCH", f"/appointments/{appt['appointment_id']}",
                    json={"synced_to_excel_at": True},
                )
                # Log confirmation notification
                channel = "SMS" if patient["sms_consent"] else (
                    "EMAIL" if patient["email_consent"] and patient["email"] else "NONE"
                )
                if channel != "NONE":
                    cli.request("POST", "/notifications", json={
                        "appointment_id": appt["appointment_id"],
                        "type": "CONFIRMATION",
                        "channel": channel,
                        "recipient": phone if channel == "SMS" else patient["email"],
                        "template_id": "TPL_CONFIRMATION_EN",
                        "status": "SENT",
                        "provider": "DRYRUN",
                        "provider_message_id": f"DRYRUN-{appt['appointment_id']}",
                        "attempt_no": 1,
                        "body_preview": f"Confirmed {appt['reference_no']}",
                    })
                # Mark request PROCESSED
                cli.request(
                    "PATCH", f"/booking-requests/{req['RequestId']}",
                    json={"queue_status": "PROCESSED"},
                )
                return {
                    "appointment_id": appt["appointment_id"],
                    "reference_no": appt["reference_no"],
                    "slot_id": slot_id,
                    "allocation_pass": pass_no,
                    "attempts_used": attempt,
                    "channel": channel,
                }
            err_code = r.json().get("error_code", "UNKNOWN")
            if err_code in ("SLOT_TAKEN", "SLOT_LOCKED"):
                excluded.add(slot_id)
                continue
            raise BusinessRule(f"BOOK_FAILED: {err_code}")
        finally:
            cli.request("POST", f"/slots/{slot_id}/release", json={"locked_by": "SchedulingBot"})

    raise BusinessRule("SLOT_CONTENTION")


def scheduling_run(cli: HmsClient, max_transactions: int = 50) -> dict:
    """Simulates bots/SchedulingBot/Main.xaml — polls PENDING_PUSH and processes."""
    r = cli.request("GET", f"/booking-requests?queue_status=PENDING_PUSH&limit={max_transactions}")
    r.raise_for_status()
    pending = r.json()
    processed = 0
    failed = 0
    outputs = []
    for req in pending:
        if processed + failed >= max_transactions:
            break
        try:
            out = process_booking_request(cli, req)
            outputs.append(out)
            processed += 1
        except BusinessRule as ex:
            cli.request(
                "PATCH", f"/booking-requests/{req['RequestId']}",
                json={"queue_status": "FAILED", "push_error": str(ex)},
            )
            failed += 1
    return {"processed": processed, "failed": failed, "outputs": outputs}


# ---------------------------------------------------------------------------
# Reminder
# ---------------------------------------------------------------------------
def resolve_channels(reminder_type: str, priority: str, pref: str,
                     sms_consent: bool, email_consent: bool,
                     phone: str, email: str) -> list[str]:
    sms_ok = sms_consent and phone and bool(E164.match(phone or ""))
    em_ok = email_consent and email and bool(EMAIL.match(email or ""))
    if reminder_type == "REMINDER_2H" and priority in ("URGENT", "HIGH"):
        out = []
        if sms_ok:
            out.append("SMS")
        if em_ok:
            out.append("EMAIL")
        return out
    if pref == "BOTH":
        return (["SMS"] if sms_ok else []) + (["EMAIL"] if em_ok else [])
    if pref == "EMAIL":
        return ["EMAIL"] if em_ok else (["SMS"] if sms_ok else [])
    # default SMS
    return ["SMS"] if sms_ok else (["EMAIL"] if em_ok else [])


def reminder_run(cli: HmsClient, now: datetime | None = None, dry_run: bool = True) -> dict:
    """Simulates bots/ReminderBot/Main.xaml."""
    if now is None:
        now = datetime.now()
    if now.hour < 7 or now.hour >= 21:
        return {"sent": 0, "skipped": 0, "failed": 0, "reason": "QUIET_HOURS"}

    now_iso = now.isoformat()
    sent, skipped, failed = 0, 0, 0
    for window, rtype in (("24H", "REMINDER_24H"), ("2H", "REMINDER_2H")):
        r = cli.request("GET", f"/reminders/due?window={window}&now={now_iso}")
        r.raise_for_status()
        for item in r.json():
            # Idempotency
            r2 = cli.request(
                "GET",
                f"/notifications?appointment_id={item['appointment_id']}&type={rtype}&status=SENT",
            )
            r2.raise_for_status()
            if r2.json():
                skipped += 1
                continue

            channels = resolve_channels(
                rtype,
                item.get("priority", "NORMAL"),
                item.get("preferred_channel", "SMS"),
                bool(item.get("sms_consent")),
                bool(item.get("email_consent")),
                item.get("phone", ""),
                item.get("email", "") or "",
            )
            if not channels:
                skipped += 1
                continue

            for ch in channels:
                # In dry-run we skip actual Twilio/SendGrid and just log the notification
                r3 = cli.request("POST", "/notifications", json={
                    "appointment_id": item["appointment_id"],
                    "type": rtype,
                    "channel": ch,
                    "recipient": item["phone"] if ch == "SMS" else item["email"],
                    "template_id": f"TPL_{rtype}_EN",
                    "status": "SENT",
                    "provider": "DRYRUN" if dry_run else ("TWILIO" if ch == "SMS" else "SENDGRID"),
                    "provider_message_id": f"DRYRUN-{item['appointment_id']}-{rtype}-{ch}",
                    "attempt_no": 1,
                    "body_preview": f"{rtype} for {item['reference_no']}",
                })
                if r3.status_code < 300:
                    sent += 1
                else:
                    failed += 1
    return {"sent": sent, "skipped": skipped, "failed": failed}


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------
def conflict_sweep(cli: HmsClient) -> dict:
    """SWEEP mode — detect double-bookings, HMS auto-raises them."""
    r = cli.request("GET", "/conflicts/detect/double-bookings?raise=true")
    r.raise_for_status()
    return {"detected": len(r.json())}


def conflict_resolve(cli: HmsClient, max_items: int = 50) -> dict:
    """QUEUE mode — read OPEN conflicts and apply strategy per type."""
    r = cli.request("GET", f"/conflicts?status=OPEN&limit={max_items}")
    r.raise_for_status()
    resolved, escalated = 0, 0
    for c in r.json():
        cid = c["conflict_id"]
        ctype = c["conflict_type"]
        related = c.get("related_appointment_id")

        if ctype == "DOUBLE_BOOKING":
            if not related:
                cli.request("PATCH", f"/conflicts/{cid}", json={
                    "status": "ESCALATED",
                    "resolution": "MISSING_RELATED",
                    "resolution_note": "Need the other appointment id to pick a keeper",
                })
                escalated += 1
                continue
            r2 = cli.request("POST", f"/appointments/{related}/cancel", json={
                "actor": "ConflictResolutionBot",
                "note": "Resolving DOUBLE_BOOKING; keeper wins",
            })
            if r2.status_code < 300:
                cli.request("PATCH", f"/conflicts/{cid}", json={
                    "status": "RESOLVED",
                    "resolution": "CANCELLED_LOSER",
                    "resolution_note": f"Cancelled appointment {related}",
                })
                resolved += 1
            else:
                cli.request("PATCH", f"/conflicts/{cid}", json={
                    "status": "ESCALATED",
                    "resolution": "CANCEL_FAILED",
                    "resolution_note": f"Cancel failed: {r2.json().get('error_code')}",
                })
                escalated += 1
        elif ctype == "CANCELLATION_CASCADE":
            cli.request("PATCH", f"/conflicts/{cid}", json={
                "status": "RESOLVED",
                "resolution": "NO_ACTION_REQUIRED",
                "resolution_note": "Cancel freed slot for backfill",
            })
            resolved += 1
        else:
            cli.request("PATCH", f"/conflicts/{cid}", json={
                "status": "ESCALATED",
                "resolution": "NEEDS_CLERK",
                "resolution_note": f"Type {ctype} not auto-resolvable",
            })
            escalated += 1
    return {"resolved": resolved, "escalated": escalated}
