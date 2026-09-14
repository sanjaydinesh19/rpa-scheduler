# Target Automated Booking Process (To-Be)

**Scope:** The same outpatient booking process after automation, showing exactly where each of the three bots acts and what stays human.

**Actors:**

| Actor | Role in the automated process |
|---|---|
| Patient | Submits a request via the Mock HMS web form; receives SMS/email confirmations and reminders |
| **Scheduling Bot** | Consumes booking requests from the queue, finds the best slot, books it via the HMS UI, mirrors to Excel |
| **Reminder Bot** | Runs on an Orchestrator time trigger; sends T-24h and T-2h reminders |
| **Conflict Resolution Bot** | Detects and resolves double-bookings, doctor unavailability and cancellation cascades |
| Front-desk clerk | Handles exceptions only — items the bots push to the exception queue |
| Department coordinator | Maintains `rules.yaml` / rules workbook instead of deciding case by case |

---

## Flow

### Step 1 — Request intake *(Patient → Mock HMS → Orchestrator queue)*
Patient submits the booking form on the Mock HMS. The HMS validates the fields (phone format, required fields, date in the future) and posts the request into the **`BookingRequests`** Orchestrator queue as a queue item.
**Automated:** validation and hand-off. **Replaces:** manual slip-taking in Step 1 of the as-is flow.
**Queue item payload:** `PatientName, Phone, Email, Department, PreferredDoctor, PreferredDateFrom, PreferredDateTo, Urgency, Reason, RequestId`

### Step 2 — Queue pickup *(Scheduling Bot ← Orchestrator)*
Scheduling Bot runs unattended, calls `Get Transaction Item` and takes the next `New` item. Orchestrator guarantees each item is processed once and tracks its status.
**Automated:** work distribution. **Replaces:** a clerk deciding what to work on next.

### Step 3 — Patient lookup / registration *(Scheduling Bot → HMS UI)*
Bot opens the HMS patient search screen and looks the patient up by phone number — the one reliably unique field. If no match, it registers a new patient through the HMS UI form and captures the new `PatientId`.
**Automated:** deduplication by phone. **Fixes:** duplicate-record problem from the as-is Step 2.

### Step 4 — Slot allocation *(Scheduling Bot → HMS UI + rules)*
Bot loads the allocation rules (`Data/rules.xlsx`, generated from `rules.yaml`), scrapes the doctor schedule screen for the department, and selects a slot by the documented algorithm — preferred doctor first, then preference matching within the patient's window, then first-fit across the department, with priority weighting for urgent and senior cases.
**Automated:** the biggest manual time sink. **Fixes:** as-is Steps 3 and 4 together — the coordinator phone call disappears because the priority rules are now machine-readable.
**No slot found →** item is pushed to the exception queue with reason `NO_SLOT_AVAILABLE` for the clerk to handle.

### Step 5 — Booking entry with slot lock *(Scheduling Bot → HMS UI)*
Bot re-checks the chosen slot is still free immediately before submitting (optimistic lock), fills the HMS booking form and saves. If the slot was taken between selection and submit, the bot loops back to Step 4 and picks the next-best slot — up to 3 attempts.
**Automated:** entry + race handling. **Fixes:** the double-booking cause in as-is Step 5.

### Step 6 — Excel mirror *(Scheduling Bot → appointments.xlsx)*
Bot appends the confirmed appointment to `data/appointments.xlsx`, the second "system" standing in for the billing workbook, and stamps it `SyncedAt`.
**Automated:** the duplicate data entry from as-is Step 6, now a by-product of the same transaction rather than a separate human task.

### Step 7 — Confirmation notification *(Scheduling Bot → Twilio / SendGrid)*
Bot sends the patient an SMS and/or email confirmation with the appointment reference, doctor, date and time, via `SendSMS.xaml` / `SendEmail.xaml`. The send is logged to the `Notification` table.
**Automated:** new capability. **Fixes:** the "no digital trail" gap and the repeat-confirmation calls in as-is Step 7.

### Step 8 — Transaction close *(Scheduling Bot → Orchestrator)*
Bot sets the queue item to `Successful` with the output data (`AppointmentId`, `SlotId`, `DoctorId`). Any failure sets `Failed` with a business or application exception and a human-readable reason.
**Automated:** audit trail. **New:** every booking is now traceable in Orchestrator, which the manual process never had.

---

### Step 9 — Scheduled reminders *(Reminder Bot, time-triggered)*
An Orchestrator time trigger starts the Reminder Bot on a fixed cron cadence. Each run it queries the HMS for appointments falling in the **T-24h** and **T-2h** windows that have no successful reminder logged, then sends SMS or email per the channel decision tree, retries transient failures, and writes the outcome to the `Notification` table.
**Automated:** the evening call-round. **Fixes:** as-is Step 8 entirely — coverage goes from best-effort to every appointment, and it never gets dropped because the desk is busy.

### Step 10 — Conflict detection and resolution *(Conflict Resolution Bot)*
Bot is fed from the **`Conflicts`** queue, which receives items from three sources: the HMS when a doctor marks unavailability, the Scheduling Bot when it detects an anomaly, and a periodic sweep that reconciles the HMS against `appointments.xlsx`. Per conflict type it applies the documented strategy — rebook the lower-priority patient into the next viable slot, cascade-reschedule a leave day's patients, or flag to the clerk when no automated resolution is safe. Every affected patient is notified of the change.
**Automated:** the firefighting in as-is Step 9, now proactive rather than discovered on the clinic morning.

### Step 11 — Exception handling *(Bots → Exception queue → Front-desk clerk)*
Anything a bot cannot resolve — no slot in range, patient data that fails validation, a repeatedly failing notification, an unsafe reschedule — lands in the exception queue with a clear reason code. The clerk works a short exception list instead of the whole booking volume.
**Human-in-the-loop:** this is the deliberate boundary of the automation.

---

## Bot responsibility map

| As-is step | To-be step | Owner |
|---|---|---|
| 1. Request intake | 1 | Mock HMS (validation) |
| 2. Patient lookup | 3 | **Scheduling Bot** |
| 3. Slot search | 4 | **Scheduling Bot** |
| 4. Priority check | 4 (rules-driven) | **Scheduling Bot** |
| 5. Booking entry | 5 | **Scheduling Bot** |
| 6. Excel entry | 6 | **Scheduling Bot** |
| 7. Confirmation | 7 | **Scheduling Bot** |
| 8. Reminder calls | 9 | **Reminder Bot** |
| 9. Conflict firefighting | 10 | **Conflict Resolution Bot** |
| — | 11 | Front-desk clerk (exceptions only) |

---

## What stays human

- Approving reschedules the Conflict Bot flags as unsafe (e.g. a patient mid-treatment-cycle).
- Maintaining the business rules in `rules.yaml` when clinical policy changes.
- Working the exception queue.
- Any clinical judgement — the bots move appointments, never make care decisions.

---

## Target metrics

| Metric | Current | Target after automation |
|---|---|---|
| Time to book one appointment | 5–8 min | < 60 s (unattended) |
| Clerk time per booking | 5–8 min | ~0 min (exceptions only) |
| Reminder coverage | ~40% | > 98% |
| No-show rate | ~22% | ~12% |
| Double-bookings per week | 8–12 | 0 undetected |
| HMS ↔ Excel mismatch | 5–10% | 0 (same transaction) |

> Next: [`automation_targets.csv`](automation_targets.csv) scores each task for automation fit.
