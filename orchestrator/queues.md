# Orchestrator Queues

Create these in UiPath Automation Cloud → Orchestrator → your folder (`RPA-Scheduler`) → **Queues**.

| Queue | Fed by | Consumed by | Purpose |
|---|---|---|---|
| `BookingRequests` | Mock HMS booking form | Scheduling Bot | One item per patient booking request |
| `Conflicts` | HMS events + Conflict Bot sweep | Conflict Resolution Bot | One item per detected conflict |
| `Conflicts_Exceptions` | Conflict Resolution Bot | Front-desk clerk (Action Center or manual) | Conflicts the bot decided a human must handle |

---

## 1. `BookingRequests`

| Setting | Value | Why |
|---|---|---|
| Unique Reference | **Enforce** | The HMS sends `RequestId` (`REQ-000412`) as the reference. A retried push after a network blip must not create a second booking |
| Auto Retry | **Yes** | |
| Max # Retries | **2** | Retries **system** exceptions only. Business exceptions are never retried — see below |
| Specific Data JSON Schema | Optional, see below | Rejects malformed items at the door instead of inside the bot |
| SLA | 15 minutes | A request should be booked within one quarter-hour of submission |
| Risk SLA | 10 minutes | Orchestrator flags items getting close |

### Item `SpecificContent`

```json
{
  "RequestId": "REQ-000412",
  "PatientName": "R. Krishnan",
  "Phone": "+919876543210",
  "Email": "",
  "Department": "CARD",
  "PreferredDoctor": "Any",
  "PreferredDateFrom": "2026-09-16",
  "PreferredDateTo": "2026-09-20",
  "TimeBand": "MORNING",
  "Urgency": "NORMAL",
  "IsFollowUp": false,
  "Reason": "Chest pain follow-up"
}
```

This is exactly what `BookingRequest.to_queue_payload()` in `mock_hms/models.py` produces.

### Optional JSON schema

```json
{
  "type": "object",
  "required": ["RequestId", "PatientName", "Phone", "Department", "PreferredDateFrom", "PreferredDateTo"],
  "properties": {
    "Phone":   { "type": "string", "pattern": "^\\+[1-9]\\d{9,14}$" },
    "Urgency": { "enum": ["NORMAL", "HIGH", "URGENT"] },
    "TimeBand":{ "enum": ["MORNING", "AFTERNOON", "ANY"] }
  }
}
```

### Priority mapping

The HMS pushes every item at `Normal` priority. When enabling priority-aware pickup, map `Urgency = URGENT` → queue priority `High` so urgent requests are dequeued first. The allocation algorithm still applies its own priority rules after pickup — queue priority only changes *when* an item is processed, not *which slot* it gets.

### Output data on success

```json
{
  "AppointmentId": 412, "ReferenceNo": "APT-2026-000412", "SlotId": 3312,
  "DoctorId": 4, "DoctorName": "Dr. Meera Raghavan",
  "AppointmentDateTime": "2026-09-16T10:20:00",
  "AllocationPass": 3, "AttemptsUsed": 1,
  "NotificationChannel": "SMS", "ExcelSynced": true
}
```

---

## 2. `Conflicts`

| Setting | Value | Why |
|---|---|---|
| Unique Reference | **Enforce** | Reference is `CFL-{conflict_id}`. The 15-minute sweep will re-detect an unresolved conflict; this stops it being enqueued twice |
| Auto Retry | **Yes** | |
| Max # Retries | **2** | |
| SLA | 30 minutes | |

### Item `SpecificContent`

```json
{
  "ConflictId": 88,
  "ConflictType": "DOUBLE_BOOKING",
  "Severity": "HIGH",
  "AppointmentId": 412,
  "RelatedAppointmentId": 498,
  "SlotId": 3312,
  "DetectedBy": "ReconcileSweep",
  "Detail": "Time overlap on Dr. Kavitha Rao 2026-09-18: APT-2026-000003 at 09:30 and APT-2026-000081 at 09:37"
}
```

`ConflictType` is one of `DOUBLE_BOOKING`, `DOCTOR_UNAVAILABLE`, `CANCELLATION_CASCADE`, `EXCEL_MISMATCH` — the router in `ConflictResolutionBot/Main.xaml` switches on it.

Map `Severity` → queue priority: `HIGH` → High, `MEDIUM` → Normal, `LOW` → Low. A double-booking tomorrow morning should not wait behind a spreadsheet typo.

---

## 3. `Conflicts_Exceptions`

| Setting | Value |
|---|---|
| Unique Reference | Enforce (`ESC-{conflict_id}`) |
| Auto Retry | **No** — a human works these |
| SLA | 4 hours |

### Item `SpecificContent`

```json
{
  "ConflictId": 88,
  "ConflictType": "DOUBLE_BOOKING",
  "Reason": "TREATMENT_CYCLE_NEEDS_APPROVAL",
  "AppointmentRef": "APT-2026-000412",
  "PatientName": "R. Krishnan",
  "PatientPhone": "+919876543210",
  "CurrentSlot": "2026-09-16 10:20 · Dr. Meera Raghavan",
  "SuggestedSlot": "2026-09-18 11:00 · Dr. Meera Raghavan",
  "WhyEscalated": "Follow-up in an active treatment cycle; reschedule needs coordinator approval",
  "DetectedAt": "2026-09-15T09:32:11+05:30"
}
```

The clerk actions it on `/admin/reschedule` with **Override the safety rails** ticked, which is exactly the path the HMS reserves for a human.

---

## Business vs system exceptions

This is the setting-level consequence of the most important REFramework rule.

| Kind | Examples | Orchestrator | Retried? |
|---|---|---|---|
| **Business** | `VALIDATION_ERROR`, `NO_SLOT_AVAILABLE`, `SLOT_CONTENTION`, `DAILY_CAP_REACHED` | Status `Failed`, reason Business Exception | **Never** — the process said no; retrying changes nothing |
| **System** | `INTERNAL_ERROR`, `NETWORK_TIMEOUT`, `SELECTOR_NOT_FOUND`, `EXCEL_FILE_LOCKED` | Status `Failed`, reason Application Exception | **Yes**, up to Max # Retries |

Auto Retry applies only to Application exceptions, so setting it to Yes is safe: an invalid phone number will not be retried three times.

---

## HMS push configuration

The HMS pushes to `BookingRequests` using an Orchestrator **External Application** (client credentials). Set these as Render environment variables — never in the repo:

| Variable | Where to find it |
|---|---|
| `QUEUE_PUSH_ENABLED` | `true` |
| `UIPATH_ORG_ID` | The organisation segment of your cloud URL: `cloud.uipath.com/{org}/{tenant}` |
| `UIPATH_TENANT` | The tenant segment of the same URL |
| `UIPATH_CLIENT_ID` / `UIPATH_CLIENT_SECRET` | Admin → External Applications → Add → Confidential, application scope `OR.Queues` |
| `UIPATH_FOLDER_ID` | Open the folder in Orchestrator; the numeric id is in the URL as `fid=` |

With any of these missing, the HMS keeps working: requests are stored with `queue_status = PENDING_PUSH` and appear on the dashboard, so you can develop the HMS before the tenant exists.
