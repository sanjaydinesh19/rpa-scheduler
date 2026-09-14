# rpa-scheduler

**Smart Healthcare Appointment Scheduling — UiPath RPA**

Three UiPath bots automate outpatient appointment booking, reminders and conflict resolution against a mock Hospital Management System, orchestrated through UiPath Orchestrator queues and triggers.

| | |
|---|---|
| **RPA** | UiPath Studio + Orchestrator (Community) |
| **Mock HMS** | Flask + SQLAlchemy + SQLite |
| **Notifications** | Twilio (SMS) · SendGrid (Email) |
| **Deploy** | Render (HMS) · Orchestrator Cloud (bots) |

## The bots

| Bot | Trigger | Does |
|---|---|---|
| **Scheduling Bot** | `BookingRequests` queue | Looks up the patient, allocates the best slot, books via the HMS UI, mirrors to Excel, sends confirmation |
| **Reminder Bot** | Orchestrator time trigger | Sends T-24h and T-2h reminders over SMS/email with retry and idempotency |
| **Conflict Resolution Bot** | `Conflicts` queue | Handles double-bookings, doctor unavailability, cancellation cascades and HMS↔Excel drift |

## Project status

| Phase | Weeks | Status |
|---|---|---|
| 1 — Process Analysis | 1 | ✅ Complete |
| 2 — RPA Design | 2 | ⬜ Not started |
| 3 — Development | 3–4 | ⬜ Not started |
| 4 — Testing & Deployment | 5–6 | ⬜ Not started |

## Documentation

**Phase 1 — Process Analysis**
- [`workflow_current.md`](docs/phase1/workflow_current.md) — the manual process today and where it breaks
- [`workflow_automated.md`](docs/phase1/workflow_automated.md) — the target flow and what each bot owns
- [`automation_targets.csv`](docs/phase1/automation_targets.csv) — 35 tasks scored for automation fit
- [`data_model.md`](docs/phase1/data_model.md) — full schema for the mock HMS
- [`hms_interfaces.md`](docs/phase1/hms_interfaces.md) — UI selectors and REST contract the bots depend on

[`PROJECT_BRIEF_v2.md`](PROJECT_BRIEF_v2.md) — scope, tech stack and phase plan.

---

*This README is expanded in Phase 4 with the architecture diagram, demo walkthrough and screenshots.*
