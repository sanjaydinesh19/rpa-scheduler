# rpa-scheduler

**Smart Healthcare Appointment Scheduling — UiPath RPA**

Three UiPath bots automate outpatient appointment booking, reminders and conflict resolution against a mock Hospital Management System, orchestrated through UiPath Orchestrator queues and triggers.

| | |
|---|---|
| **RPA** | UiPath Studio + Orchestrator (Community) |
| **Bot ↔ HMS** | REST (`HTTP Request` activity from `UiPath.Web.Activities`) |
| **Mock HMS** | Flask + SQLAlchemy + SQLite |
| **Notifications** | Twilio (SMS) · SendGrid (Email) |
| **Deploy** | Render (HMS) · Orchestrator Cloud (bots) |

## The bots

| Bot | Trigger | Does |
|---|---|---|
| **Scheduling Bot** | `BookingRequests` queue (or PENDING_PUSH poll in local mode) | Finds/registers patient, allocates the best slot per four-pass algorithm, locks + books + releases, mirrors to Excel, sends confirmation |
| **Reminder Bot** | Orchestrator time trigger | Sends T-24h and T-2h reminders over SMS/email with quiet-hour gate, consent handling, retry and idempotency |
| **Conflict Resolution Bot** | `Conflicts` queue + 15-min sweep | Detects double-bookings, escalates doctor absences, closes cancellation cascades |

## Project status

| Phase | Weeks | Status |
|---|---|---|
| 1 — Process Analysis | 1 | ✅ Complete |
| 2 — RPA Design | 2 | ✅ Complete |
| 3 — Development | 3–4 | ✅ Complete — all XAML workflows implemented, Python reference engine, end-to-end tests passing |
| 4 — Testing & Deployment | 5–6 | ⬜ Not started |

---

## Steps to run and verify

Everything below assumes you are in the repo root (`rpa-scheduler`). Windows PowerShell examples; adjust for bash.

### 1. Install Python 3.10+ and create a venv

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements.txt
```

`requirements.txt` pulls in both the HMS deps and the test deps.

### 2. Run the end-to-end tests (fastest way to verify Phase 3)

```powershell
.\.venv\Scripts\python -m pytest tests -v
```

22 tests. They spawn an isolated Mock HMS subprocess, reseed before every test, and drive the same REST flow the bots do — booking, reminder, and conflict resolution end to end. **This is the acceptance test for Phase 3.**

### 3. Run the Mock HMS on its own to poke it

```powershell
$env:REQUIRE_API_KEY = "false"
.\.venv\Scripts\python -m mock_hms.seed
.\.venv\Scripts\python -m mock_hms.app
```

Open <http://localhost:5000>.

| Path | Purpose |
|---|---|
| `/` | Patient booking form |
| `/admin/patients` | Patient search & registration |
| `/admin/schedule` | Doctor schedule |
| `/admin/book` | Admin booking form |
| `/admin/conflicts` | Conflict console (seed plants one) |
| `/admin/dashboard` | Live counts |
| `/api/v1/health` | Health probe |

Demo helpers (with `REQUIRE_API_KEY=false`):

```powershell
Invoke-RestMethod http://localhost:5000/api/v1/health
Invoke-RestMethod -Method Post http://localhost:5000/api/v1/admin/reseed
Invoke-RestMethod -Method Post http://localhost:5000/api/v1/admin/plant-double-booking
Invoke-RestMethod http://localhost:5000/api/v1/admin/stats
```

### 4. Open the bots in UiPath Studio

Requires **UiPath Studio Community** on Windows and a **UiPath Automation Cloud** account.

1. In Studio: **Open Local Project** → point at `bots\SchedulingBot\project.json`. Repeat for `ReminderBot` and `ConflictResolutionBot`.
2. Studio restores `UiPath.Web.Activities`, `UiPath.System.Activities`, `UiPath.Excel.Activities`, `UiPath.Mail.Activities` per each project's `project.json`.
3. Open `Main.xaml`. The workflow is complete — no `TODO(Studio)` markers remain.
4. **Test the bots against a locally running HMS:**
   - Start the HMS (step 3 above)
   - In Studio, click **Debug** on `SchedulingBot/Main.xaml`
   - Provide arguments: `in_BaseUrl = "http://localhost:5000"`, `in_ApiKey = ""` (SecureString from empty), `in_MaxTransactions = 5`, `in_MaxRetryAttempts = 3`, `in_DryRunNotifications = True`, `in_ExcelPath = "path\to\data\appointments.xlsx"`, `in_HospitalName = "Meridian General Hospital"`
   - Watch the transaction succeed; check the HMS dashboard for the new appointment

### 5. Wire up Orchestrator (optional — production path)

Follow `orchestrator/queues.md`, `orchestrator/triggers.md`, `orchestrator/assets.md`:

1. Create queues `BookingRequests`, `Conflicts`, `Conflicts_Exceptions`
2. Create credential assets `HMS_ApiKey`, `Twilio_Credentials`, `SendGrid_ApiKey`; text assets `HMS_BaseUrl`, `DryRunNotifications`, etc.
3. Publish each bot from Studio → Orchestrator
4. Attach the time and queue triggers per `triggers.md`

To have the HMS push booking requests to Orchestrator, set `QUEUE_PUSH_ENABLED=true` plus the `UIPATH_*` env vars from `orchestrator/queues.md`.

---

## Architecture note: REST-first, not UI-first

Phase 2's original design had the Scheduling Bot use `Click` / `Type Into` against the mock HMS UI. Phase 3 pivoted to REST calls (`HTTP Request` activity) for three reasons:

1. **Testable without a browser.** Every business rule — allocation, priority derivation, channel resolution, conflict routing — is exercised by the pytest suite that ships in `tests/`.
2. **No captured selectors to break** when the HTML changes. The API has a machine-facing contract.
3. **Studio-buildable without a live session.** UI selectors can only be captured against a running browser; REST XAML can be authored in the editor.

The UI is still fully functional and remains the human interface. Anyone wanting the "watch the browser click" demo can drop `Click` activities alongside the existing REST calls — the design docs still describe both paths.

## Repository layout

```
rpa-scheduler/
├── bots/
│   ├── SchedulingBot/
│   │   ├── Main.xaml               REST-driven booking flow
│   │   ├── Workflows/AllocateSlot.xaml   Four-pass scoring algorithm
│   │   ├── project.json
│   │   └── Data/{rules,Config,templates}.xlsx
│   ├── ReminderBot/Main.xaml       T-24h/T-2h with idempotency
│   └── ConflictResolutionBot/Main.xaml   SWEEP + QUEUE modes
├── shared/                         HMS_Client.xaml, SendSMS.xaml, SendEmail.xaml
├── mock_hms/                       Flask app — models, api, services, seed, views
├── tests/
│   ├── conftest.py                 Spawns isolated HMS subprocess
│   ├── bot_engine.py               Python reference impl of the same flow
│   └── test_end_to_end.py          22 tests, all green
├── data/                           seed_patients.csv, appointments.xlsx
├── tools/                          yaml_to_xlsx.py, excel_mirror.py
├── orchestrator/                   queues.md, triggers.md, assets.md
└── docs/
    ├── phase1/                     process analysis, data model, HMS interfaces
    └── phase2/                     bot specs, rules.yaml, architecture
```

## Documentation

**Phase 1 — Process Analysis** — `docs/phase1/`
- [`workflow_current.md`](docs/phase1/workflow_current.md), [`workflow_automated.md`](docs/phase1/workflow_automated.md), [`automation_targets.csv`](docs/phase1/automation_targets.csv), [`data_model.md`](docs/phase1/data_model.md), [`hms_interfaces.md`](docs/phase1/hms_interfaces.md)

**Phase 2 — RPA Design** — `docs/phase2/`
- [`bot_scheduling.md`](docs/phase2/bot_scheduling.md), [`bot_reminder.md`](docs/phase2/bot_reminder.md), [`bot_conflict.md`](docs/phase2/bot_conflict.md), [`rules.yaml`](docs/phase2/rules.yaml), [`architecture.md`](docs/phase2/architecture.md)

**Phase 3 — Orchestrator configuration**
- [`queues.md`](orchestrator/queues.md), [`triggers.md`](orchestrator/triggers.md), [`assets.md`](orchestrator/assets.md)

[`PROJECT_BRIEF_v2.md`](PROJECT_BRIEF_v2.md) — scope, tech stack and phase plan.
