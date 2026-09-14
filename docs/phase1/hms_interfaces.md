# Mock HMS Interfaces

**What this document fixes:** every screen, selector anchor, endpoint and payload the three bots depend on. Phase 3 builds `mock_hms/` to this contract.

**Two surfaces, on purpose:**
1. **UI screens** — the bots drive these with UiPath UI Automation (click, type, data-scrape). This is what makes the project an RPA project rather than an API-integration project.
2. **REST endpoints** — used for setup, verification, the request intake hand-off and the Reminder Bot's bulk queries, where clicking through a UI would be theatre rather than automation.

> **Rule of thumb applied throughout:** the Scheduling Bot books through the **UI**. The Reminder and Conflict bots read through the **API** and act through the **UI** where a human would.

---

## Part A — UI screens

Every automatable element carries a stable `id` **and** a `data-automation-id` attribute. Bots select on `data-automation-id` so that CSS restyling never breaks a selector.

### A1. `/` — Booking request form *(public, patient-facing)*

| Element | `data-automation-id` | Type |
|---|---|---|
| Full name | `req-name` | text |
| Phone | `req-phone` | text |
| Email | `req-email` | text |
| Department | `req-department` | select |
| Preferred doctor | `req-doctor` | select (`Any` allowed) |
| Preferred date from | `req-date-from` | date |
| Preferred date to | `req-date-to` | date |
| Preferred time band | `req-time-band` | select — `MORNING` / `AFTERNOON` / `ANY` |
| Urgency | `req-urgency` | select — `NORMAL` / `HIGH` / `URGENT` |
| Reason for visit | `req-reason` | textarea |
| Submit | `req-submit` | button |
| Result banner | `req-result` | div — shows `RequestId` on success |

**On submit:** validates, persists a `BookingRequest` row, and posts the item to the `BookingRequests` Orchestrator queue.

### A2. `/admin/patients` — Patient search & registration *(Scheduling Bot, Step 3)*

| Element | `data-automation-id` | Notes |
|---|---|---|
| Search box | `pat-search-input` | Bot always searches by **phone** |
| Search button | `pat-search-btn` | |
| Results table | `pat-results-table` | Empty table still renders — bot checks row count |
| Result row patient id | `pat-row-id` | One per row |
| "No results" marker | `pat-no-results` | Present only when zero matches |
| New patient button | `pat-new-btn` | Opens the registration form |
| Registration fields | `pat-new-name`, `pat-new-phone`, `pat-new-email`, `pat-new-dob`, `pat-new-gender`, `pat-new-channel` | |
| Save | `pat-new-save` | |
| New MRN output | `pat-new-mrn` | Bot scrapes the generated MRN here |

### A3. `/admin/schedule` — Doctor schedule board *(Scheduling Bot, Step 4)*

The screen the bot data-scrapes for slot allocation.

| Element | `data-automation-id` | Notes |
|---|---|---|
| Department filter | `sch-department` | select |
| Doctor filter | `sch-doctor` | select, `Any` allowed |
| Date from / to | `sch-date-from`, `sch-date-to` | |
| Load button | `sch-load-btn` | |
| Slot table | `sch-slot-table` | **Data-scrape target** |
| Slot row | `sch-slot-row` | Columns: `SlotId, DoctorId, DoctorName, Date, StartTime, EndTime, Status, IsEmergencyReserve` |
| Empty marker | `sch-no-slots` | |

**Contract:** the table renders as a plain `<table>` with a header row and consistent column order, so UiPath's Table Extraction produces a clean DataTable in one pass.

### A4. `/admin/book` — Booking form *(Scheduling Bot, Step 5)*

| Element | `data-automation-id` | Notes |
|---|---|---|
| Patient id | `book-patient-id` | |
| Slot id | `book-slot-id` | |
| Priority | `book-priority` | select |
| Reason | `book-reason` | |
| Follow-up flag | `book-is-followup` | checkbox |
| Queue item ref | `book-queue-ref` | Bot writes the Orchestrator item id for traceability |
| Confirm | `book-submit` | |
| Success panel | `book-success` | Contains `book-reference-no` |
| Reference number | `book-reference-no` | **Scraped and returned as queue output data** |
| Error panel | `book-error` | Contains `book-error-code` |

**Error codes surfaced in `book-error-code`:** `SLOT_TAKEN` · `SLOT_BLOCKED` · `PATIENT_NOT_FOUND` · `DOCTOR_INACTIVE` · `DAILY_CAP_REACHED` · `VALIDATION_ERROR`

> `SLOT_TAKEN` is the one the bot retries — it re-runs allocation and picks the next-best slot, up to 3 attempts.

### A5. `/admin/conflicts` — Conflict console *(Conflict Resolution Bot)*

| Element | `data-automation-id` | Notes |
|---|---|---|
| Conflict table | `cfl-table` | Data-scrape target |
| Conflict row | `cfl-row` | `ConflictId, Type, Severity, AppointmentId, RelatedAppointmentId, SlotId, Status` |
| Resolve button | `cfl-resolve-btn` | Per row |
| Resolution select | `cfl-resolution` | `REBOOKED` / `CANCELLED` / `ESCALATED` |
| Resolution note | `cfl-note` | |
| Save resolution | `cfl-save` | |

### A6. `/admin/reschedule` — Reschedule form *(Conflict Resolution Bot)*

| Element | `data-automation-id` |
|---|---|
| Appointment reference | `rsc-reference` |
| New slot id | `rsc-new-slot` |
| Reason | `rsc-reason` |
| Submit | `rsc-submit` |
| Success marker | `rsc-success` |

### A7. `/admin/doctors` — Doctor roster & unavailability *(human; demo trigger)*

Used in the demo to mark a doctor unavailable and watch the Conflict Bot cascade.

| Element | `data-automation-id` |
|---|---|
| Doctor select | `doc-select` |
| Unavailable from / to | `doc-unavail-from`, `doc-unavail-to` |
| Reason | `doc-unavail-reason` |
| Save | `doc-unavail-save` |

### A8. `/admin/dashboard` — Minimal dashboard *(recruiter-facing)*

Read-only tiles: today's appointments, bookings made by bots vs manually, reminder coverage %, open conflicts, HMS↔Excel sync status. No automation IDs needed — this exists for the demo and the README screenshots.

---

## Part B — REST API

Base URL: `https://<render-app>.onrender.com/api/v1`
Auth: `X-API-Key` header, value stored as an Orchestrator **Asset** (`HMS_ApiKey`). All responses JSON; all datetimes ISO-8601.

### Patients

| Method | Path | Purpose | Used by |
|---|---|---|---|
| `GET` | `/patients?phone={phone}` | Lookup by dedup key | SchedulingBot (fallback) |
| `GET` | `/patients/{id}` | Fetch one | All |
| `POST` | `/patients` | Register | Seed / fallback |

### Slots

| Method | Path | Purpose | Used by |
|---|---|---|---|
| `GET` | `/slots?department={code}&doctor_id={id}&date_from=&date_to=&status=AVAILABLE` | Slot search | SchedulingBot (verification), ConflictBot |
| `GET` | `/slots/{id}` | Single slot, current status + `version` | SchedulingBot (pre-submit re-check) |
| `POST` | `/slots/{id}/lock` | Take optimistic lock, TTL 120 s | SchedulingBot |
| `POST` | `/slots/{id}/release` | Release lock | SchedulingBot (finally block) |

### Appointments

| Method | Path | Purpose | Used by |
|---|---|---|---|
| `GET` | `/appointments?from=&to=&status=` | Window query | ReminderBot |
| `GET` | `/appointments/{reference_no}` | Fetch one | All |
| `POST` | `/appointments` | Create (non-UI path) | Seed / tests |
| `PATCH` | `/appointments/{id}` | Status or slot change | ConflictBot |
| `POST` | `/appointments/{id}/cancel` | Cancel and free the slot | ConflictBot |

### Reminders

| Method | Path | Purpose | Used by |
|---|---|---|---|
| `GET` | `/reminders/due?window=24H\|2H` | **Key endpoint.** Appointments due in the window with no `SENT` notification of that type | ReminderBot |
| `POST` | `/notifications` | Log a send attempt | ReminderBot, SchedulingBot |
| `GET` | `/notifications?appointment_id=` | Idempotency check | ReminderBot |

`GET /reminders/due` response item:
```json
{
  "appointment_id": 412,
  "reference_no": "APT-2026-000412",
  "appointment_datetime": "2026-09-16T10:20:00",
  "patient": {
    "patient_id": 88, "full_name": "R. Krishnan", "phone": "+919876543210",
    "email": null, "preferred_channel": "SMS",
    "sms_consent": true, "email_consent": true, "preferred_language": "EN"
  },
  "doctor_name": "Dr. Meera Raghavan",
  "department": "Cardiology",
  "already_sent": []
}
```

### Conflicts

| Method | Path | Purpose | Used by |
|---|---|---|---|
| `GET` | `/conflicts?status=OPEN` | Open conflicts | ConflictBot |
| `POST` | `/conflicts` | Raise one | SchedulingBot, ReconcileSweep |
| `PATCH` | `/conflicts/{id}` | Record resolution | ConflictBot |
| `GET` | `/conflicts/detect/double-bookings` | On-demand scan | ConflictBot |
| `GET` | `/doctors/unavailability?is_processed=false` | Unswept leave | ConflictBot |

### Booking requests (intake hand-off)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/booking-requests` | Called by the form; persists and enqueues to Orchestrator |
| `GET` | `/booking-requests/{id}` | Status for the dashboard |

### Ops

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Render health check |
| `POST` | `/admin/reseed` | Reset to seed state — **demo reset button** |
| `POST` | `/admin/plant-double-booking` | Deliberately create a conflict for the demo |

---

## Part C — Outbound: what the HMS calls

### Orchestrator queue push
On `POST /booking-requests`, the HMS calls the Orchestrator Cloud API `AddQueueItem` against queue `BookingRequests`.
Credentials held as Render environment variables: `UIPATH_TENANT`, `UIPATH_CLIENT_ID`, `UIPATH_CLIENT_SECRET`, `UIPATH_ORG_ID`, `UIPATH_FOLDER_ID`.

**Fallback:** if the push fails, the row is persisted with `queue_status = PENDING_PUSH` and retried by a background sweep. A booking request is never lost because Orchestrator was briefly unreachable.

---

## Part D — Error contract

Every API error returns the same envelope, so bots branch on `error_code` and never parse prose:

```json
{ "error_code": "SLOT_TAKEN",
  "message": "Slot 3312 was booked by another transaction",
  "retryable": true }
```

| `error_code` | HTTP | Retryable | Bot action |
|---|---|---|---|
| `SLOT_TAKEN` | 409 | yes | Re-run allocation, next-best slot |
| `SLOT_LOCKED` | 423 | yes | Wait for lock TTL, retry once |
| `VALIDATION_ERROR` | 400 | no | Business exception → exception queue |
| `PATIENT_NOT_FOUND` | 404 | no | Register the patient, then retry |
| `DAILY_CAP_REACHED` | 409 | no | Try another doctor, else `NO_SLOT_AVAILABLE` |
| `UNAUTHORIZED` | 401 | no | System exception — asset misconfigured |
| `INTERNAL_ERROR` | 500 | yes | Retry with backoff, max 3 |

---

## Part E — Configuration held in Orchestrator Assets

| Asset | Type | Example |
|---|---|---|
| `HMS_BaseUrl` | Text | `https://rpa-scheduler.onrender.com` |
| `HMS_ApiKey` | Credential | — |
| `Twilio_AccountSid` | Credential | — |
| `Twilio_AuthToken` | Credential | — |
| `Twilio_FromNumber` | Text | `+1XXXXXXXXXX` |
| `SendGrid_ApiKey` | Credential | — |
| `SendGrid_FromEmail` | Text | `noreply@…` |
| `Excel_FilePath` | Text | Shared path to `appointments.xlsx` |
| `MaxRetryAttempts` | Integer | `3` |
| `DryRunNotifications` | Boolean | `True` during testing — logs instead of sending |

> `DryRunNotifications` exists so Phase 4 batch testing does not burn the Twilio trial credit.

---

**Phase 1 exit check:** the data model and this interface contract together are sufficient to write `models.py`, the Flask routes and the templates in Phase 3 without any further design decisions.
