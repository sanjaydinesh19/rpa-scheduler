# Orchestrator Triggers

Create these in Orchestrator → folder `RPA-Scheduler` → **Automations → Triggers**.

## The constraint that shapes everything here

Community Edition gives **one unattended robot**. All three bots share it, so jobs run one at a time. Two jobs scheduled for the same minute do not run in parallel — the second waits, and a hung first job blocks it indefinitely.

So every trigger below is **staggered** so they never land on the same minute, and every one has a **stop-after timeout** so a stuck job cannot starve the others.

```
minute:  :00  :05  :07  :15  :20  :22  :30  :35  :37  :45  :50  :52
Reminder  ●                             ●
Conflict            ●              ●              ●              ●
Booking   ── queue trigger: runs whenever items arrive ──
```

---

## 1. `Scheduling - Queue`

| Setting | Value |
|---|---|
| Type | **Queue trigger** |
| Process | `SchedulingBot` |
| Queue | `BookingRequests` |
| Minimum items to trigger | 1 |
| Other pending jobs allowed | 1 |
| Additional job per | 10 new items |
| Arguments | `in_QueueName = "BookingRequests"`, `in_MaxTransactions = 50` |
| Stop job after | 15 minutes (soft stop) |

A queue trigger starts the bot as soon as a request arrives rather than on a timer, which is what keeps booking latency under a minute. `in_MaxTransactions = 50` bounds each job so it yields the robot back and the reminder run is never delayed by a long batch.

---

## 2. `Reminder - Every 30 min`

| Setting | Value |
|---|---|
| Type | **Time trigger** |
| Process | `ReminderBot` |
| Cron | `0 0,30 7-21 * * ?` |
| Time zone | `(UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi` |
| Max concurrent jobs | **1** |
| Stop job after | 20 minutes |
| Arguments | `in_DryRun = False` in production, `True` for testing |

Runs at :00 and :30 from 07:00 to 21:30.

**Why 30 minutes.** The HMS claims every appointment within ±15 minutes of the T-24h and T-2h marks, so consecutive 30-minute runs cover the timeline with no gaps and no overlaps. Changing the cadence means changing `reminders.windows[*].tolerance_minutes` in `rules.yaml` to half the new interval — otherwise reminders get missed (interval too long) or claimed twice (interval too short, though the idempotency check then catches it).

**Why max concurrent = 1.** The idempotency check protects re-runs, but two runs racing on the same appointment can both pass the check before either writes its notification row.

**Why the window stops at 21:30.** The bot also enforces quiet hours itself. Stopping the trigger early is belt-and-braces: a misconfigured time zone should not be able to message patients at 3 a.m.

---

## 3. `Conflict - Sweep every 15 min`

| Setting | Value |
|---|---|
| Type | **Time trigger** |
| Process | `ConflictResolutionBot` |
| Cron | `0 7,22,37,52 * * * ?` |
| Time zone | `(UTC+05:30) Chennai, Kolkata, Mumbai, New Delhi` |
| Max concurrent jobs | 1 |
| Stop job after | 10 minutes |
| Arguments | `in_Mode = "SWEEP"`, `in_QueueName = "Conflicts"` |

Offset to :07/:22/:37/:52 so it never collides with the reminder run at :00/:30. The sweep detects and enqueues only; resolution happens in the queue trigger below.

---

## 4. `Conflict - Queue`

| Setting | Value |
|---|---|
| Type | **Queue trigger** |
| Process | `ConflictResolutionBot` |
| Queue | `Conflicts` |
| Minimum items to trigger | 1 |
| Other pending jobs allowed | 1 |
| Arguments | `in_Mode = "QUEUE"`, `in_QueueName = "Conflicts"` |
| Stop job after | 15 minutes |

A doctor marked unavailable on the HMS enqueues a conflict immediately; this trigger picks it up without waiting for the next sweep.

---

## Demo-day checklist

- [ ] Hit `https://<render-app>/api/v1/health` 2 minutes before recording — Render's free tier sleeps after 15 idle minutes and the first request takes ~30 s
- [ ] Set `DryRunNotifications = False` and confirm your own phone is verified in the Twilio console (the trial only delivers to verified numbers)
- [ ] `POST /api/v1/admin/reseed` for a clean, known state
- [ ] Temporarily set the reminder cron to `0 0/2 * * * ?` (every 2 minutes) so a reminder fires on camera — **and set it back afterwards**, with the tolerance note above in mind
- [ ] Disable the sweep trigger while recording the booking flow so the robot is not busy when you drop the queue item
