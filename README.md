# CDS Safety Alerts

Posts weather and road-status alerts for Buncombe County to a Google Chat space, for the director of safety and security. It runs on GitHub Actions; there is no server and nothing to install.

## What it posts

| | When | How |
|---|---|---|
| 🚨 **Act now** | Tornado, severe thunderstorm, flash flood, winter storm and ice storm warnings, plus civil emergencies | Immediately, with a ping to the space |
| ⚠️ **Heads up** | Watches, winter weather and wind advisories, flood warnings, special weather statements | Immediately, no ping |
| 🔄 **Updates** | An alert is extended, revised, or its storm polygon moves | Reply in the same thread |
| ✅ **Ended** | An alert is cancelled or expires | Reply in the same thread |
| 🚧 **Parkway** | A Blue Ridge Parkway section around Asheville opens or closes (Ox Creek Rd at milepost 375.7 to NC-191 at 393.6) | New message, no ping |
| 🚧 **Road closure** | NCDOT reports a full closure or major incident in Buncombe County; a reply follows when it clears | New message, no ping |
| 🧊 **Road conditions** | NCDOT's Snow & Ice report for Buncombe County changes (interstates, US/NC routes, secondary roads) | New message, no ping |
| 🌅 **Morning brief** | 5:00 AM on school days, only if there is an alert, snow or ice, or freezing precipitation in the forecast | Active alerts, campus forecast through the day, snow and ice totals, NCDOT road conditions and closures, Parkway closures |
| 🌙 **Evening outlook** | 7:00 PM Sunday to Thursday, on the same condition | Same content, looking at the next morning |
| 📋 **Conditions update** | Whenever someone asks for one | Same content, as of now |
| ℹ️ **Guide** | Whenever someone asks for it | How to read each kind of message |
| 🛠️ **Monitor notice** | A data source has been unreachable for 15 minutes, and when it recovers | New message |

Tornado, severe thunderstorm and flash flood warnings are drawn as a polygon around the storm. The message says whether campus is inside it, and warnings that miss campus do not ping (a tornado warning anywhere in the county always does).

Sources: [NWS alerts and forecast API](https://www.weather.gov/documentation/services-web-api), the [NPS Blue Ridge Parkway road closures page](https://www.nps.gov/blri/planyourvisit/roadclosures.htm), and the [NCDOT DriveNC API](https://www.drivenc.gov/developers/doc).

## Setup

1. **Create the Chat space.** In Google Chat, create a space and add the people who should receive alerts. Open the space menu → *Apps & integrations* → *Webhooks* → *Add webhook*, name it "Safety Alerts", and copy the URL.
2. **Store the URL as a secret.** In this repository: *Settings* → *Secrets and variables* → *Actions* → *New repository secret*. Name it `GOOGLE_CHAT_WEBHOOK_URL` and paste the URL. Treat the URL like a password: anyone who has it can post to the space.
3. **Store the NCDOT key.** Request a free developer key at <https://www.drivenc.gov/developers/doc> and add it as a second repository secret named `NCDOT_API_KEY`. Without it, NCDOT road data is skipped and everything else still runs.
4. **Confirm the campus location** in [`config.toml`](config.toml) (`latitude` and `longitude`).
5. **Make `main` the default branch** (*Settings* → *General* → *Default branch*). GitHub only runs scheduled workflows from the default branch.
6. **Send a test.** *Actions* → *Safety alerts* → *Run workflow* → choose `test-message`. A test message should appear in the space. The next scheduled run posts a one-time "monitor is online" summary.

## Asking for an update

*Actions* → *On request* → *Run workflow* posts either a conditions update (`update`) or the guide to reading the alerts (`help`) within about half a minute.

To let people type `/update` and `/help` in the Chat space instead, set up the small Google Chat app in [`chat-app/`](chat-app/README.md). It is needed because the webhook this monitor posts through is one-way: it cannot see what people type.

## Changing what gets posted

Everything adjustable is in [`config.toml`](config.toml): which alert types are act-now or heads-up, who gets pinged, brief times and days, which NCDOT incidents are worth a message, and the Parkway milepost range. Edit the file on GitHub and commit; the next run uses it.

## Limits to know about

- **This supplements, and does not replace, phone emergency alerts and a NOAA weather radio.** GitHub usually starts scheduled runs within a few minutes, but can be 10 to 20 minutes late at busy times, and can skip runs entirely. Once a run starts it checks NWS every minute for four minutes; NCDOT and the Parkway are checked once per run.
- **Silence is normal, so it does not prove the monitor is running.** On an ordinary day nothing is posted. To check, ask for a conditions update, or look at the *Actions* tab for recent *Safety alerts* runs. GitHub emails the repository owner when a run fails, and disables schedules in repositories with no activity for 60 days.
- The repository is public, so run logs and `config.toml` are visible to anyone. The webhook URL is not; it lives only in the repository secret.

## How it works

`.github/workflows/alerts.yml` runs `python3 -m safety_alerts` every five minutes. The program compares what the sources report with what it has already posted, which it remembers in `state.json` on the `alert-state` branch, and posts only what changed. It uses the Python standard library only.

```
safety_alerts/
  nws.py        NWS alerts: fetch, tiering, campus-in-polygon check
  forecast.py   campus hourly forecast, snow / ice / gust totals
  parkway.py    Blue Ridge Parkway road status
  ncdot.py      NCDOT closures, incidents and Snow & Ice road conditions
  engine.py     what is new, updated or over; briefs; source health
  messages.py   wording of every message
  chat.py       Google Chat webhook
```

Run locally without posting anything:

```bash
python3 -m safety_alerts --dry-run --state /tmp/state.json
```

```bash
python3 -m safety_alerts --dry-run --brief morning
```

```bash
python3 -m unittest discover -s tests
```
