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
| **Conflict Resolution Bot** | `Conflicts` queue + 15-min sweep | Handles double-bookings, doctor unavailability, cancellation cascades and HMS↔Excel drift |

## Project status

| Phase | Weeks | Status |
|---|---|---|
| 1 — Process Analysis | 1 | ✅ Complete |
| 2 — RPA Design | 2 | ✅ Complete |
| 3 — Development | 3–4 | 🟡 Mock HMS complete and tested · bot workflows scaffolded, activities to be built in Studio |
| 4 — Testing & Deployment | 5–6 | ⬜ Not started |

---

## Run the Mock HMS locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r mock_hms/requirements.txt

.venv/bin/python -m mock_hms.seed          # 3 depts, 10 doctors, 50 patients, ~4,100 slots, 80 bookings
REQUIRE_API_KEY=false .venv/bin/python -m mock_hms.app
```

Open <http://localhost:5000>. The seed plants one double-booking and one unprocessed doctor absence, so the conflict console has work in it from the start.

| Page | Path | Used by |
|---|---|---|
| Booking request form | `/` | Patients |
| Patient search & registration | `/admin/patients` | Scheduling Bot |
| Doctor schedule (scrape target) | `/admin/schedule` | Scheduling Bot |
| Booking form | `/admin/book` | Scheduling Bot |
| Conflict console | `/admin/conflicts` | Conflict Bot, clerks |
| Reschedule | `/admin/reschedule` | Conflict Bot, clerks |
| Doctor roster & absence | `/admin/doctors` | Coordinator — the demo trigger |
| Dashboard | `/admin/dashboard` | Everyone |

REST API under `/api/v1` — health check at `/api/v1/health`. Every element a bot touches carries a `data-automation-id`, so UiPath selectors survive restyling.

### Demo helpers

```bash
curl -X POST localhost:5000/api/v1/admin/reseed                 # reset to a known state
curl -X POST localhost:5000/api/v1/admin/plant-double-booking   # create a conflict on demand
curl -X POST localhost:5000/api/v1/admin/plant-unavailability   # half-day absence → cascade
curl        localhost:5000/api/v1/admin/stats                   # numbers for the report
```

### Tools

```bash
.venv/bin/python tools/yaml_to_xlsx.py                  # rules.yaml → bots/*/Data/rules.xlsx
.venv/bin/python tools/excel_mirror.py sync --base http://localhost:5000
.venv/bin/python tools/excel_mirror.py reconcile --base http://localhost:5000
```

---

## What has been verified, and what has not

**Verified against the running HMS:**
- All 8 pages render; the schedule table renders as one clean scrape target
- Validation rejects bad input at intake (for example, a phone number that isn't E.164)
- Registering a patient through the UI; a 71-year-old is correctly flagged as senior, which raises their priority to HIGH
- Lock → book through the UI form → booking the same slot again returns `SLOT_TAKEN`
- The partial unique index blocks two appointments on one slot, **even when the write is raw SQL**
- Reschedule safety rails: `TOO_CLOSE_TO_RESCHEDULE`, `URGENT_NEVER_AUTO_MOVED`, and the two-move cap (`AMBIGUOUS_PRIORITY`), plus the clerk's force-override path
- Cancelling an appointment frees its slot and opens a `CANCELLATION_CASCADE` conflict; every change is written to the audit trail
- Double-booking detection finds the planted time overlap and picks which appointment keeps the slot
- Reminder query with time travel (`?now=`) returns the right appointments along with each patient's consent flags
- Excel reconciliation catches rows missing from Excel, status drift and orphan rows — and escalates the orphan instead of deleting it
- With Orchestrator unreachable, the booking form and cancellations still succeed; the requests and conflicts are kept for a later retry

**Not yet verified — needs Windows + UiPath Studio + an Orchestrator tenant:**
- The `.xaml` files are well-formed XML with correct arguments, variables, try/catch structure and full design annotations, but **they have not been opened in Studio**. Every `TODO(Studio)` comment marks an activity that still has to be built — UI selectors can only be captured against a live browser.
- The sub-workflows that `Main.xaml` invokes (`Workflows/AllocateSlot.xaml`, `LookupOrRegisterPatient.xaml`, …) are specified in the annotations and in `docs/phase2/`, but they have not been created yet.
- Pushing to a real Orchestrator queue, and sending real messages through Twilio and SendGrid.
- Phase 3's end-to-end acceptance test (queue item → bot books through the UI → reminder sends → planted conflict gets resolved).

---

## Repository layout

```
rpa-scheduler/
├── bots/
│   ├── SchedulingBot/          Main.xaml · project.json · Data/{rules,Config,templates}.xlsx
│   ├── ReminderBot/            Main.xaml · project.json · Data/{rules,Config,templates}.xlsx
│   └── ConflictResolutionBot/  Main.xaml · project.json · Data/{rules,Config,templates}.xlsx
├── shared/                     HMS_Client.xaml · SendSMS.xaml · SendEmail.xaml
├── mock_hms/                   Flask app — models, services, api, views, demo, seed, templates/
├── data/                       seed_patients.csv · appointments.xlsx (Excel mirror)
├── tools/                      yaml_to_xlsx.py · excel_mirror.py
├── orchestrator/               queues.md · triggers.md · assets.md
└── docs/
    ├── phase1/                 process analysis, data model, HMS interface contract
    └── phase2/                 bot specs, rules.yaml, architecture
```

## Documentation

**Phase 1 — Process Analysis**
- [`workflow_current.md`](docs/phase1/workflow_current.md) — the manual process today and where it breaks
- [`workflow_automated.md`](docs/phase1/workflow_automated.md) — the target flow and what each bot owns
- [`automation_targets.csv`](docs/phase1/automation_targets.csv) — 35 tasks scored for automation fit
- [`data_model.md`](docs/phase1/data_model.md) — full schema for the mock HMS
- [`hms_interfaces.md`](docs/phase1/hms_interfaces.md) — UI selectors and REST contract the bots depend on

**Phase 2 — RPA Design**
- [`bot_scheduling.md`](docs/phase2/bot_scheduling.md) — flowchart, four-pass slot allocation algorithm, optimistic locking
- [`bot_reminder.md`](docs/phase2/bot_reminder.md) — T-24h/T-2h schedule, channel decision tree, retry and idempotency
- [`bot_conflict.md`](docs/phase2/bot_conflict.md) — four conflict types, resolution strategy each, safety rails
- [`rules.yaml`](docs/phase2/rules.yaml) — every tunable business rule, externalised
- [`architecture.md`](docs/phase2/architecture.md) — component, sequence and data-flow diagrams

**Phase 3 — Orchestrator configuration**
- [`queues.md`](orchestrator/queues.md) — `BookingRequests`, `Conflicts`, `Conflicts_Exceptions`
- [`triggers.md`](orchestrator/triggers.md) — staggered schedules for the single Community robot
- [`assets.md`](orchestrator/assets.md) — credentials and config, plus Render environment variables

[`PROJECT_BRIEF_v2.md`](PROJECT_BRIEF_v2.md) — scope, tech stack and phase plan.
