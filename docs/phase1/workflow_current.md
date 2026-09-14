# Current Manual Booking Process (As-Is)

**Scope:** How a mid-size multi-department hospital books, confirms and reminds outpatient appointments today, without automation.

**Actors:**

| Actor | Role in the process |
|---|---|
| Patient | Requests an appointment by phone, walk-in or web form |
| Front-desk clerk | Takes the request, searches for a slot, books it in the HMS |
| Department coordinator | Owns the doctor roster, approves overrides and priority cases |
| Doctor | Consumes the schedule, marks leave/unavailability |
| Billing clerk | Maintains a parallel appointments workbook for reconciliation and reporting |

---

## Step-by-step flow

### Step 1 — Request intake *(Patient → Front-desk clerk)*
Patient calls the helpline, walks in, or submits the hospital's web form. The clerk records name, phone, department, preferred doctor, preferred date/time window and urgency on a paper slip or a notepad tab. **Pain:** no validation at intake — misspelled names and wrong phone numbers enter here and are never caught.

### Step 2 — Patient lookup *(Front-desk clerk → HMS)*
Clerk searches the HMS by phone number or name to find an existing patient record. If none exists, a new patient is registered manually. **Pain:** duplicate patient records are created constantly because search is exact-match and names are entered inconsistently.

### Step 3 — Slot search *(Front-desk clerk → HMS)*
Clerk opens the doctor's schedule screen for the requested department and visually scans for a free slot inside the patient's preferred window. If the preferred doctor is full, the clerk scans other doctors in the same department one screen at a time. **Pain:** this is the single biggest time sink — 4 to 7 minutes per booking, entirely manual screen-reading, and the clerk picks the first slot they happen to see rather than the best fit.

### Step 4 — Priority / override check *(Front-desk clerk → Department coordinator)*
For urgent cases, senior citizens, or follow-ups within a treatment cycle, the clerk phones or messages the department coordinator to ask whether a slot can be freed or over-allocated. The coordinator decides case by case. **Pain:** the priority rules live in people's heads, are applied inconsistently, and add an unpredictable wait to the booking.

### Step 5 — Booking entry *(Front-desk clerk → HMS)*
Clerk fills the HMS booking form — patient ID, doctor, department, date, slot, reason for visit — and saves. The HMS returns an appointment reference number. **Pain:** the HMS does not lock the slot during entry, so two clerks working simultaneously can and do book the same slot.

### Step 6 — Parallel workbook entry *(Front-desk clerk → Excel)*
The same appointment is re-typed into the billing team's shared `appointments.xlsx` workbook, which drives daily reporting and revenue tracking. **Pain:** pure duplicate data entry; the workbook and the HMS drift apart within days, and nobody reconciles them until month-end.

### Step 7 — Confirmation to patient *(Front-desk clerk → Patient)*
Clerk reads the appointment details back over the phone, or hands the walk-in patient a printed slip. No digital confirmation is sent. **Pain:** patients have nothing to refer back to, so the helpline receives a second call from a large share of them asking to re-confirm details.

### Step 8 — Reminder calls *(Front-desk clerk → Patient)*
The evening before each clinic day, a clerk works down the next day's printed appointment list and calls each patient. Calls that go unanswered are, in practice, not retried. **Pain:** 60–90 minutes of clerk time per day, poor coverage, and it is the first task dropped whenever the desk is busy — which is the direct cause of the no-show rate.

### Step 9 — Conflict firefighting *(Doctor / Coordinator → Front-desk clerk → Patient)*
When a doctor marks unplanned leave, or a double-booking surfaces on the clinic morning, the coordinator prints the affected list and the clerk phones every patient to reschedule, one at a time. **Pain:** reactive, happens under time pressure, and cascading cancellations (a half-day of leave = 12–16 patients) can consume an entire morning at the desk.

---

## Where the process breaks

| # | Problem | Business impact |
|---|---|---|
| 1 | Slot search is manual screen-scanning | 4–7 min per booking; clerk is the bottleneck at peak hours |
| 2 | No slot locking during entry | Recurring double-bookings discovered only on the clinic day |
| 3 | Data entered twice (HMS + Excel) | Systems drift; month-end reconciliation is a manual audit |
| 4 | Reminders are manual phone calls | Skipped when busy → high no-show rate → wasted doctor capacity |
| 5 | Priority rules are undocumented | Inconsistent, unauditable decisions on who gets the urgent slot |
| 6 | Conflict handling is reactive | A single doctor's leave burns a morning of desk time |
| 7 | No digital confirmation trail | Repeat "please confirm my appointment" calls inflate call volume |

---

## Baseline metrics (assumed for this project)

These are the reference numbers the automated process is measured against.

| Metric | Current (manual) |
|---|---|
| Time to book one appointment | 5–8 minutes |
| Bookings handled per clerk per day | ~60 |
| Reminder coverage | ~40% of appointments |
| No-show rate | ~22% |
| Double-bookings per week | 8–12 |
| HMS ↔ Excel mismatch at month-end | 5–10% of rows |

> Next: [`workflow_automated.md`](workflow_automated.md) maps each of these nine steps onto the three bots.
