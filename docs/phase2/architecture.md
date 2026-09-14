# System Architecture

**Scope:** How the bots, Orchestrator, Mock HMS and notification gateways fit together, and why each boundary is where it is.

---

## 1. Component diagram

```mermaid
flowchart TB
    subgraph PATIENT [" "]
        P[Patient<br/>browser / phone]
    end

    subgraph RENDER ["Render · free tier"]
        direction TB
        UI[Mock HMS · Flask<br/>Booking form · Admin screens · Dashboard]
        API[REST API<br/>/api/v1]
        DB[(SQLite<br/>9 tables)]
        UI <--> DB
        API <--> DB
    end

    subgraph ORCH ["UiPath Orchestrator · Community Cloud"]
        direction TB
        Q1[[Queue<br/>BookingRequests]]
        Q2[[Queue<br/>Conflicts]]
        Q3[[Queue<br/>Exceptions]]
        T1{{Trigger<br/>Reminder · 30 min}}
        T2{{Trigger<br/>Conflict sweep · 15 min}}
        AS[(Assets<br/>credentials + config)]
        LOG[Logs · Jobs<br/>Transaction history]
    end

    subgraph ROBOT ["Robot VM · Windows"]
        direction TB
        B1[Scheduling Bot]
        B2[Reminder Bot]
        B3[Conflict Resolution Bot]
        SH[[shared/<br/>HMS_Client · SendSMS · SendEmail]]
        XL[(appointments.xlsx<br/>second system)]
        B1 -.-> SH
        B2 -.-> SH
        B3 -.-> SH
    end

    subgraph GW ["Notification gateways"]
        TW[Twilio · SMS]
        SG[SendGrid · Email]
    end

    CLERK[Front-desk clerk<br/>exceptions only]

    P -->|1 submits request| UI
    UI -->|2 AddQueueItem| Q1
    Q1 -->|3 Get Transaction| B1
    B1 -->|4 UI automation<br/>click · type · scrape| UI
    B1 -->|5 verify · lock| API
    B1 -->|6 mirror row| XL
    B1 -->|7 confirmation| SH
    B1 -->|8 status + output| Q1

    T1 -->|starts| B2
    B2 -->|GET /reminders/due| API
    B2 -->|send| SH

    T2 -->|starts sweep| B3
    B3 -->|detect| API
    B3 -->|reconcile| XL
    B3 -->|enqueue findings| Q2
    Q2 -->|Get Transaction| B3
    B3 -->|reschedule via UI| UI
    B3 -->|notify| SH
    UI -->|doctor marked unavailable| Q2

    SH -->|HTTP Request| TW
    SH -->|HTTP Request| SG
    TW -->|SMS| P
    SG -->|Email| P

    AS -.->|credentials at runtime| ROBOT
    B1 -.->|failures| Q3
    B3 -.->|escalations| Q3
    Q3 --> CLERK
    ROBOT -.->|logs| LOG
```

---

## 2. The layers, and what each is responsible for

| Layer | Component | Responsibility | Deliberately not responsible for |
|---|---|---|---|
| **Presentation** | Mock HMS UI | Patient-facing form, admin screens, the surfaces bots automate | Business rules, scheduling logic |
| **Data** | SQLite via SQLAlchemy | System of record; enforces the double-booking unique index | Deciding *which* slot to book |
| **Integration** | REST API `/api/v1` | Verification reads, bulk queries, lock/release, queue hand-off | Replacing the UI path for booking |
| **Orchestration** | Orchestrator queues, triggers, assets | Work distribution, scheduling, credentials, audit trail | Any business logic |
| **Automation** | The three bots | All process logic and decisions | Storing state between runs |
| **Shared** | `HMS_Client`, `SendSMS`, `SendEmail` | Reusable interaction primitives | Knowing anything about scheduling |
| **Notification** | Twilio, SendGrid | Message delivery | Deciding who to message or when |
| **Human** | Front-desk clerk | Exception queue, clinical judgement | Routine bookings |

---

## 3. Why the bots use both UI and API

This is the design question most worth being able to answer in an interview, because using the API for everything would be *easier* and would make it a worse project.

| Operation | Surface | Reason |
|---|---|---|
| Book an appointment | **UI** | This is the RPA thesis: the bot does what the clerk does — clicks, types, reads the screen. Real hospital systems rarely expose a booking API |
| Register a patient | **UI** | Same |
| Scrape the doctor schedule | **UI** | Table extraction from a rendered screen is the genuine RPA skill here |
| Reschedule | **UI** | It is the clerk's action, automated |
| Slot lock / version re-check | **API** | A concurrency primitive with no UI equivalent — a clerk cannot "hold" a slot either, which is exactly the flaw being fixed |
| Bulk reminder query | **API** | Paging through 120 appointments in a UI would be automation theatre, not automation |
| Conflict detection | **API** | A set-based query; no screen represents it |
| Queue hand-off | **API** | System-to-system by definition |

> The honest framing: **UI automation for what a human does, API for what a human cannot.** A bot that clicked through 120 screens to build a reminder list would be a worse system pretending to be a better demo.

---

## 4. Booking sequence

```mermaid
sequenceDiagram
    autonumber
    participant P as Patient
    participant H as Mock HMS
    participant O as Orchestrator
    participant B as Scheduling Bot
    participant X as appointments.xlsx
    participant T as Twilio

    P->>H: Submit booking request form
    H->>H: Validate fields
    H->>O: AddQueueItem(BookingRequests)
    H-->>P: RequestId REQ-000412

    O->>B: Get Transaction Item
    B->>H: [UI] Search patient by phone
    alt Patient not found
        B->>H: [UI] Register patient → MRN
    end
    B->>H: [UI] Load doctor schedule
    H-->>B: Slot table (scraped DataTable)
    B->>B: AllocateSlot() → 4-pass search + scoring
    B->>H: [API] POST /slots/3312/lock
    B->>H: [API] GET /slots/3312 (re-check status + version)
    alt Slot taken since scrape
        B->>B: Exclude slot, re-allocate (max 3 attempts)
    end
    B->>H: [UI] Fill booking form, submit
    H-->>B: APT-2026-000412
    B->>X: Append row, stamp SyncedAt
    B->>T: Send confirmation SMS
    T-->>P: SMS delivered
    B->>H: [API] POST /notifications (SENT)
    B->>H: [API] POST /slots/3312/release
    B->>O: Set Successful + output data
```

---

## 5. Conflict cascade sequence

The scenario that makes the demo worth watching: one doctor marks half a day of leave.

```mermaid
sequenceDiagram
    autonumber
    participant C as Coordinator
    participant H as Mock HMS
    participant O as Orchestrator
    participant B as Conflict Bot
    participant P as Patients

    C->>H: [UI] Mark Dr. Raghavan unavailable 14:00–18:00
    H->>O: AddQueueItem(Conflicts, DOCTOR_UNAVAILABLE)
    O->>B: Get Transaction Item
    B->>H: [API] Fetch affected appointments (6 found)
    B->>H: [API] Block all slots in range
    Note over B,H: Blocking first stops the Scheduling Bot<br/>booking into the gap mid-resolution
    loop For each appointment, priority order
        B->>B: Find replacement (3 passes)
        alt Slot found
            B->>H: [UI] Reschedule via /admin/reschedule
            B->>P: SMS: appointment moved
            B->>H: [API] Write AppointmentAudit
        else No slot / too close / treatment cycle
            B->>O: Push to Exceptions queue with SuggestedSlot
        end
    end
    B->>H: [API] Mark unavailability processed
    B->>O: Set Successful
```

---

## 6. Data flow and system of record

```
Patient request ──► Mock HMS (SQLite)  ◄── SYSTEM OF RECORD
                          │
                          ├──► Orchestrator queue (work item, transient)
                          │
                          └──► appointments.xlsx (mirror, read-mostly)
                                     ▲
                                     └── Conflict Bot reconciles Excel ← HMS
                                         (one direction only, never HMS ← Excel)
```

**One declared direction of truth.** The HMS is authoritative; Excel is a mirror that gets corrected. Bidirectional sync between two systems with no conflict-resolution protocol is how reconciliation bugs become data loss — and a project this size has no business inventing one.

---

## 7. Deployment topology

| Component | Where | Notes |
|---|---|---|
| Mock HMS | Render free tier | Public URL; cold-starts after 15 min idle — warm it before demoing |
| SQLite file | Render ephemeral disk | Resets on redeploy; `POST /admin/reseed` restores demo state |
| Orchestrator | UiPath Automation Cloud | Community tenant, one folder: `RPA-Scheduler` |
| Robot | Windows VM or local Studio | Community allows one unattended robot — the three bots share it, so they run sequentially, not in parallel |
| Twilio / SendGrid | SaaS | Free tiers; trial numbers must be verified |
| Source | GitHub | `.xaml`, Flask app, docs |

> **The one-robot constraint is load-bearing.** Community Edition gives a single unattended robot, so the Reminder trigger and the Conflict sweep cannot overlap. Triggers are staggered — Reminder on `:00/:30`, Conflict sweep on `:07/:22/:37/:52` — and every job has a stop-if-exceeds timeout so a hung job cannot block the next one indefinitely.

---

## 8. Security boundaries

| Secret | Stored in | Never in |
|---|---|---|
| HMS API key | Orchestrator Asset (Credential) | `.xaml`, `Config.xlsx`, GitHub |
| Twilio SID / token | Orchestrator Asset (Credential) | Anywhere in the repo |
| SendGrid API key | Orchestrator Asset (Credential) | Anywhere in the repo |
| Orchestrator client secret | Render environment variable | The Flask source |

**Patient data handling:** `body_preview` on `Notification` is capped at 255 characters and logs are scrubbed of full phone numbers (`+9198****3210`). This is *not* HIPAA compliance — the brief puts that out of scope — but the report should say so explicitly rather than leave a reader assuming it was considered and handled.

---

## 9. Failure modes and how each is contained

| Failure | Contained by | Result |
|---|---|---|
| Render app asleep / down | System exception + Orchestrator retry | Items stay `New`, processed when it wakes |
| Orchestrator unreachable at intake | HMS persists with `queue_status = PENDING_PUSH`, background retry | No booking request is lost |
| Two bots target one slot | Lock → version re-check → DB unique index | Second bot re-allocates; no double-booking |
| Bot crashes mid-transaction | `FINALLY` releases the lock; item returns to the queue | Slot not stranded; work retried |
| Twilio credit exhausted | Non-retryable error → `FAILED` + LOW conflict | Other reminders continue |
| Excel file locked by a human | System exception `EXCEL_FILE_LOCKED`, retry | Mirror catches up on the next sweep |
| Reminder trigger double-fires | Idempotency check + `max_concurrent_jobs = 1` | No duplicate messages |
| Conflict resolution loops | `max_reschedules_per_appointment = 2` | Escalates instead of bouncing a patient |

---

## 10. Technology choices — the short defence

| Choice | Over | Because |
|---|---|---|
| Orchestrator queues | A database table the bots poll | Queues give once-only delivery, retry, status and an audit trail for free — rebuilding that is the actual engineering content |
| Flask + SQLite | A commercial HMS sandbox | Full control over the UI, so selectors are stable and failure modes are plantable on demand |
| Excel as a second system | A second database | Reconciliation against a spreadsheet is what most real automation projects actually face |
| Time trigger for reminders | A queue | Reminders are due because time passed, not because an event happened |
| `rules.yaml` externalised | Rules inside `.xaml` | Policy changes without a redeploy, and the rules are reviewable by the coordinator who owns them |
| REFramework | A plain sequence | Built-in retry, transaction status and business/system exception split are the patterns UiPath roles interview on |

---

## 11. Phase 2 exit check

- [x] Every bot has a flowchart and pseudocode precise enough to build from
- [x] The slot allocation algorithm is fully specified, including tie-breaks
- [x] Reminder scheduling, channel selection and retry are decided
- [x] All four conflict types have a documented resolution strategy and escalation rule
- [x] Every tunable value is in `rules.yaml`, not buried in prose
- [x] Component, sequence and data-flow diagrams are drawn
- [x] Failure modes are enumerated with their containment

> **Phase 3 can begin.** Someone else could build `mock_hms/` from [`../phase1/data_model.md`](../phase1/data_model.md) + [`../phase1/hms_interfaces.md`](../phase1/hms_interfaces.md), and the three bots from the specs in this folder, without making a new design decision.
