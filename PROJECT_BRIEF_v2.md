# Smart Healthcare Appointment Scheduling — RPA Project Brief

**Duration:** 6 weeks | **Type:** Student portfolio / campus placement project
**Deliverable:** Working RPA prototype with mock HMS + documentation

---

## How to Use This Brief

Send Claude this brief at the start of any phase with the message: **"Start Phase N"**
Claude will produce the phase's deliverables as files. Review, iterate, approve, then move on.

---

## Tech Stack (Locked)

| Layer | Choice |
|---|---|
| RPA platform | UiPath Studio (Community Edition — free) |
| Orchestration | UiPath Orchestrator (Community Cloud — free tier) |
| Mock HMS | Web app (Flask + SQLite) that bots interact with via UI |
| Alt HMS surface | Excel workbook for a second "system" the bots reconcile with |
| Notifications | Twilio (SMS) + SendGrid (Email), both free tier — called via HTTP Request activity |
| Scheduling | Orchestrator Triggers (time-based) for the Reminder Bot |
| Queues | Orchestrator Queues for booking requests + conflict items |
| Version control | GitHub (commit the `.xaml` workflow files + Mock HMS code) |
| Deployment | Bots run via Orchestrator; Mock HMS on Render (free tier) |

**Why this setup:** UiPath is drag-and-drop workflow design in Studio, but Orchestrator + Queues + Triggers is where the real engineering shows — that's what recruiters care about. The Mock HMS as a web app + Excel gives the bots something realistic to automate against (UI clicks, screen scraping, data reconciliation) instead of just calling APIs.

---

## Scope Boundaries

**In scope:**
- Mock HMS database with realistic sample data
- 3 bots: Scheduling, Reminder, Conflict Resolution
- Working notification pipeline (real SMS/email via free tiers)
- REST API for booking + a minimal HTML dashboard
- Deployed demo URL + GitHub repo

**Out of scope:**
- Real hospital integration
- HIPAA compliance (mention in docs, don't implement)
- Multi-tenant / multi-hospital
- Mobile app
- ML-based no-show prediction (mention as future work)

---

## Phase 1 — Process Analysis (Week 1)

**Goal:** Define the booking process we're automating and the data model.

**Deliverables:**
1. `workflow_current.md` — Current manual booking process (5–8 step flow with actors)
2. `workflow_automated.md` — Target automated flow showing where each bot acts
3. `automation_targets.csv` — Columns: `task, frequency, automation_fit (high/med/low), assigned_bot`
4. `data_model.md` — Entities: Patient, Doctor, Department, Slot, Appointment, Notification. Fields + relationships.
5. `hms_interfaces.md` — What the mock HMS must expose (endpoints or DB tables)

**Done when:** Data model is complete enough to write the schema in Phase 3 without new decisions.

---

## Phase 2 — RPA Design (Week 2)

**Goal:** Nail down bot logic so Phase 3 is pure coding.

**Deliverables:**
1. `bot_scheduling.md` — Flowchart + pseudocode. Slot allocation algorithm (first-fit, priority rules, preference matching)
2. `bot_reminder.md` — Reminder schedule (T-24h, T-2h), SMS vs email decision tree, retry logic
3. `bot_conflict.md` — Conflict types (double-booking, doctor unavailable, cancellation cascade), resolution strategy per type
4. `rules.yaml` — Machine-readable business rules the bots will load at runtime
5. `architecture.md` — Component diagram: bots ↔ orchestrator ↔ HMS ↔ notification gateways

**Done when:** Someone else could code Phase 3 from these docs alone.

---

## Phase 3 — Development (Weeks 3–4)

**Goal:** Build it.

**Repo structure:**
```
rpa-scheduler/
├── bots/
│   ├── SchedulingBot/
│   │   ├── Main.xaml
│   │   ├── project.json
│   │   └── Data/rules.xlsx        # Allocation rules the bot reads
│   ├── ReminderBot/
│   │   ├── Main.xaml
│   │   └── project.json
│   └── ConflictResolutionBot/
│       ├── Main.xaml
│       └── project.json
├── shared/
│   ├── HMS_Client.xaml            # Reusable workflow for HMS interactions
│   ├── SendSMS.xaml               # Twilio HTTP Request wrapper
│   └── SendEmail.xaml             # SendGrid HTTP Request wrapper
├── mock_hms/                      # The Flask web app bots automate against
│   ├── app.py
│   ├── models.py
│   ├── seed.py
│   ├── templates/                 # Booking form, admin panel, doctor schedule
│   └── requirements.txt
├── data/
│   ├── appointments.xlsx          # Second "system" for reconciliation
│   └── seed_patients.csv
├── orchestrator/
│   ├── queues.md                  # Queue definitions (BookingRequests, Conflicts)
│   ├── triggers.md                # Trigger schedules (Reminder Bot cron)
│   └── assets.md                  # Credentials, config assets
└── README.md
```

**Milestones:**
- End of Week 3: Mock HMS running + Scheduling Bot picks a queue item, opens the HMS UI, books an appointment, writes to Excel
- End of Week 4: Reminder Bot (triggered) sends real SMS/email + Conflict Resolution Bot handles double-bookings + seed data (50 patients, 10 doctors, 3 departments)

**Done when:** You drop a booking request into the Orchestrator queue, the Scheduling Bot picks it up and books via the HMS UI, a reminder actually sends at the scheduled time, and a deliberately-created double-booking gets caught and resolved.

---

## Phase 4 — Testing & Deployment (Weeks 5–6)

### Week 5 — Testing

**Deliverables:**
1. Test workflows in UiPath (`Tests/` folder with `.xaml` files) covering each bot's happy path + 2–3 edge cases
2. `test_scenarios.md` — Written test cases: input, expected outcome, actual outcome, pass/fail
3. Try/Catch blocks audited across all bots; failed queue items go to Orchestrator's exception queue with clear reasons
4. Batch check: 20 booking requests dropped into the queue, all processed without manual intervention

**Done when:** All 3 bots run unattended through a full batch, exceptions are logged not silently swallowed, and the test scenarios doc shows every critical path passing.

### Week 6 — Deployment

**Deliverables:**
1. Mock HMS deployed to Render (public URL) with seeded data
2. Bots published to Orchestrator Community Cloud with triggers active
3. `README.md` — Setup, architecture diagram, demo walkthrough, screenshots of Studio workflows + Orchestrator dashboard
4. `deployment.md` — How to publish bots + redeploy Mock HMS from scratch
5. `report.md` — Final project report: what was built, decisions made, limitations, future work
6. **Demo video (2–3 min screen recording) — do this, it's the difference-maker for placements.** Screen record: dropping a queue item → bot picks it up in Orchestrator → HMS UI being clicked → SMS arriving on your phone.

**Done when:** A recruiter can click the demo URL, see it work, and read the README in 5 minutes.

---

## Timeline

| Week | Focus | Key output |
|---|---|---|
| 1 | Process Analysis | Data model + workflows |
| 2 | RPA Design | Bot specs + rules.yaml |
| 3 | Dev — Scheduling Bot + HMS | Bookings working end-to-end |
| 4 | Dev — Reminder + Conflict + dashboard | All 3 bots + UI |
| 5 | Testing | pytest suite passing |
| 6 | Deployment + report | Live URL + docs |

---

## Prerequisites Checklist

Before Phase 1:
- [ ] UiPath Studio Community Edition installed (Windows required — VM if on Mac/Linux)
- [ ] UiPath Automation Cloud account (free) — gives Orchestrator access
- [ ] Python 3.10+ installed (for Mock HMS only)
- [ ] GitHub repo created (empty is fine)
- [ ] Twilio account (free trial — needs phone verification)
- [ ] SendGrid account (free — 100 emails/day)
- [ ] Render account (for Mock HMS in Phase 4)

---

## Portfolio Angles (for placement interviews)

Frame the project as any of these depending on the role:
- **RPA / Automation Developer:** UiPath Studio + Orchestrator, queues, triggers, REFramework-style exception handling (this is the strongest pitch — UiPath skills are directly hireable)
- **SDE / Backend:** Mock HMS is a Flask + SQLAlchemy app; talk about the API design and data model
- **Business Analyst / Consulting:** Process analysis (Phase 1), business rules design (Phase 2), ROI story
- **Data / ML:** Mention no-show prediction as extension; show the data model

---

**Ready when you are. Reply "Start Phase 1" to kick off.**
