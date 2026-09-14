# Orchestrator Assets

Create these in Orchestrator → folder `RPA-Scheduler` → **Assets**. Bots read them with **Get Asset** / **Get Credential** in `InitAllSettings.xaml`.

**Rule:** no secret lives in a `.xaml`, `Config.xlsx`, or anywhere in this repository. If a value would let someone send SMS on your account, it is a Credential asset.

---

## Credentials

| Asset | Type | Username field | Password field | Used by |
|---|---|---|---|---|
| `HMS_ApiKey` | Credential | `api` | Value of `HMS_API_KEY` on Render | All bots via `shared/HMS_Client.xaml` |
| `Twilio_Credentials` | Credential | Account SID (`AC…`) | Auth Token | `shared/SendSMS.xaml` |
| `SendGrid_ApiKey` | Credential | `apikey` | API key (`SG.…`) | `shared/SendEmail.xaml` |

Get Credential returns the password as a `SecureString`. Pass it straight into the shared workflow's `SecureString` argument and convert it only inside the HTTP call. `project.json` excludes `*password*`, `*token*` and `*apikey*` from logged data as a second line of defence, not the first.

---

## Configuration

| Asset | Type | Example value | Notes |
|---|---|---|---|
| `HMS_BaseUrl` | Text | `https://rpa-scheduler.onrender.com` | No trailing slash. Use `http://localhost:5000` when developing against a local HMS |
| `Twilio_FromNumber` | Text | `+15005550006` | The trial number Twilio assigns you, E.164 |
| `SendGrid_FromEmail` | Text | `you@yourdomain.com` | Must be a **verified Single Sender** in SendGrid or every send returns 403 |
| `Hospital_Name` | Text | `Meridian General Hospital` | Substituted into `{HospitalName}` in templates |
| `Excel_FilePath` | Text | `C:\RPA\rpa-scheduler\data\appointments.xlsx` | Path on the **robot machine**, not your laptop |
| `MaxRetryAttempts` | Integer | `3` | Overrides `booking.max_attempts_per_transaction` |
| `DryRunNotifications` | Boolean | `True` | **Leave True until the demo.** Every notification is logged with provider `DRYRUN` instead of sent |
| `RulesVersion` | Text | `1.0` | The bot logs a warning if this differs from the version inside `Data/rules.xlsx` — catches a robot running stale rules |

---

## Precedence

When the same setting exists in more than one place, the bot uses the first it finds:

1. **Orchestrator Asset** — changeable without republishing, so it wins
2. `Data/Config.xlsx` in the process package
3. `Data/rules.xlsx`, generated from `docs/phase2/rules.yaml`

Only the keys listed under `runtime.asset_overrides` in `rules.yaml` may be overridden by an asset. Business rules — weights, notice periods, reschedule caps — deliberately cannot be changed from Orchestrator: they change through `rules.yaml`, where the coordinator who owns the policy can review the diff.

---

## Environment variables on Render (HMS side)

These are the HMS's counterpart to the assets above. Set them in the Render dashboard, never in the repository.

| Variable | Value |
|---|---|
| `HMS_API_KEY` | A long random string — must match the `HMS_ApiKey` credential asset |
| `SECRET_KEY` | Another long random string (Flask session signing) |
| `REQUIRE_API_KEY` | `true` |
| `ALLOW_DEMO_ENDPOINTS` | `true` for the demo, `false` otherwise — they include a full database reset |
| `QUEUE_PUSH_ENABLED` | `true` once the Orchestrator variables in `queues.md` are set |
| `UIPATH_*` | See `queues.md` |

Generate the random values with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
