"""Loads docs/phase2/rules.yaml into a typed accessor.

The HMS and the bots must agree on the rules. The bots read rules.xlsx
(generated from the same YAML by tools/yaml_to_xlsx.py); the HMS reads the
YAML directly. One source, two runtime forms — so a policy change never means
editing logic in two places.
"""
from __future__ import annotations

import os
from datetime import time
from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "docs" / "phase2" / "rules.yaml"


class Rules:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or os.environ.get("RULES_PATH") or _DEFAULT_PATH)
        self.reload()

    def reload(self):
        with open(self.path, "r", encoding="utf-8") as fh:
            self._d = yaml.safe_load(fh)

    # -- raw access --------------------------------------------------------
    def get(self, dotted: str, default=None):
        """rules.get('allocation.weights.weight_earliest')"""
        node = self._d
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # -- meta --------------------------------------------------------------
    @property
    def hospital_name(self) -> str:
        return self.get("meta.hospital_name", "Mock Hospital")

    @property
    def version(self) -> str:
        return str(self.get("meta.version", "0"))

    # -- booking window ----------------------------------------------------
    @property
    def min_booking_lead_hours(self) -> int:
        return int(self.get("booking.min_booking_lead_hours", 2))

    @property
    def max_booking_horizon_days(self) -> int:
        return int(self.get("booking.max_booking_horizon_days", 30))

    @property
    def default_search_window_days(self) -> int:
        return int(self.get("booking.default_search_window_days", 7))

    @property
    def allow_weekend_booking(self) -> bool:
        return bool(self.get("booking.allow_weekend_booking", False))

    # -- priority ----------------------------------------------------------
    @property
    def senior_age_threshold(self) -> int:
        return int(self.get("priority.senior_age_threshold", 60))

    @property
    def priority_levels(self) -> dict:
        return self.get("priority.levels", {"URGENT": 1, "HIGH": 2, "NORMAL": 3})

    def priority_rank(self, priority: str) -> int:
        return self.priority_levels.get(priority, 99)

    @property
    def emergency_reserve_enabled(self) -> bool:
        return bool(self.get("priority.emergency_reserve.enabled", True))

    @property
    def emergency_reserve_per_day(self) -> int:
        return int(self.get("priority.emergency_reserve.slots_per_doctor_per_day", 2))

    @property
    def emergency_reserve_priorities(self) -> list:
        return self.get("priority.emergency_reserve.eligible_priorities", ["URGENT"])

    # -- allocation --------------------------------------------------------
    @property
    def weights(self) -> dict:
        return self.get("allocation.weights", {})

    def weight(self, name: str, default: float = 0.0) -> float:
        return float(self.weights.get(name, default))

    def time_band(self, band: str) -> tuple[time, time]:
        cfg = self.get(f"allocation.time_bands.{band}") or self.get("allocation.time_bands.ANY")
        return (
            time.fromisoformat(cfg["from"]),
            time.fromisoformat(cfg["to"]),
        )

    def in_time_band(self, t: time, band: str) -> bool:
        if band == "ANY" or not band:
            return True
        lo, hi = self.time_band(band)
        return lo <= t <= hi

    # -- capacity ----------------------------------------------------------
    @property
    def respect_doctor_daily_cap(self) -> bool:
        return bool(self.get("capacity.respect_doctor_daily_cap", True))

    @property
    def respect_department_daily_cap(self) -> bool:
        return bool(self.get("capacity.respect_department_daily_cap", True))

    # -- reminders ---------------------------------------------------------
    @property
    def reminder_windows(self) -> list:
        return self.get("reminders.windows", [])

    def reminder_window(self, wtype: str) -> dict | None:
        key = wtype if wtype.startswith("REMINDER_") else f"REMINDER_{wtype}"
        for w in self.reminder_windows:
            if w.get("type") == key:
                return w
        return None

    @property
    def quiet_hours(self) -> tuple[time, time]:
        return (
            time.fromisoformat(self.get("reminders.quiet_hours.send_from", "07:00")),
            time.fromisoformat(self.get("reminders.quiet_hours.send_to", "21:00")),
        )

    def channel_order(self, preferred: str) -> list:
        return self.get(f"reminders.channel.{preferred}", ["SMS", "EMAIL"])

    @property
    def sms_pattern(self) -> str:
        return self.get("reminders.channel.sms_pattern", r"^\+[1-9]\d{9,14}$")

    @property
    def escalate_2h_priorities(self) -> list:
        return self.get("reminders.channel.escalate_on_2h_for_priorities", ["URGENT"])

    # -- conflicts ---------------------------------------------------------
    @property
    def min_reschedule_notice_hours(self) -> int:
        return int(self.get("conflicts.reschedule.min_reschedule_notice_hours", 4))

    @property
    def reschedule_window_days(self) -> int:
        return int(self.get("conflicts.reschedule.reschedule_window_days", 3))

    @property
    def max_reschedules_per_appointment(self) -> int:
        return int(self.get("conflicts.reschedule.max_reschedules_per_appointment", 2))

    @property
    def enable_waitlist_backfill(self) -> bool:
        return bool(self.get("conflicts.backfill.enable_waitlist_backfill", True))

    @property
    def backfill_horizon_days(self) -> int:
        return int(self.get("conflicts.backfill.backfill_horizon_days", 14))

    @property
    def backfill_priorities(self) -> list:
        return self.get("conflicts.backfill.eligible_priorities", ["URGENT", "HIGH"])

    @property
    def never_auto_move_urgent(self) -> bool:
        return bool(self.get("safety.never_auto_move_urgent", True))

    # -- locking -----------------------------------------------------------
    @property
    def lock_ttl_seconds(self) -> int:
        return int(self.get("locking.lock_ttl_seconds", 120))

    # -- exceptions --------------------------------------------------------
    @property
    def business_exception_codes(self) -> list:
        return self.get("exceptions.business_exception_codes", [])

    @property
    def system_exception_codes(self) -> list:
        return self.get("exceptions.system_exception_codes", [])

    def is_retryable(self, code: str) -> bool:
        """A system exception is worth retrying; a business exception is not."""
        return code in self.system_exception_codes


# Module-level singleton. Import as `from .rules import RULES`.
RULES = Rules()
