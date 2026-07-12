# youtube-ics

Auto-create scheduled **YouTube live broadcasts** ("events") from a Google Calendar
**ICS** feed, so the parish's liturgical schedule automatically produces the corresponding
YouTube live events (which then feed the existing Facebook auto-poster).

> Status: **not started** — this README is the design handoff. See "Open decisions".

## Goal

Read upcoming events from a Google Calendar (ICS), and for each one create a matching
YouTube **scheduled live broadcast** (title, description, start time, public), keeping them
in sync as calendar entries are added/changed/removed. No more manually creating each
Sunday's live event in YouTube Studio.

## How it fits the bigger picture

```
Google Calendar (ICS)  ──►  youtube-ics  ──►  YouTube scheduled live broadcast
                                                     │  (later, ATEM/OBS goes live)
                                                     ▼
                                          youtube-social-poster  ──►  Facebook post
```

- This service **creates** the events; the operator (ATEM Mini via RTMP) goes live into
  them at service time.
- When a broadcast actually goes **live**, the existing
  [`youtube-social-poster`](../youtube-social-poster) detects it (WebSub) and posts to the
  parish Facebook page. **No coordination needed** between the two services — the poster
  only fires on `liveBroadcastContent == "live"`, and already handles `"upcoming"`
  (scheduled) videos with a recheck loop, so scheduling ahead won't cause premature or
  duplicate posts.

## Reusable assets from prior work

- **YouTube channel:** Saint George Melkite Catholic Church
  - channel ID `UCxofnB_k5S2B_k7rVWpSiyA`, handle `@saintgeorgemelkite`
- **Google Cloud project:** the one already used by `youtube-social-poster` has **YouTube
  Data API v3 enabled** — reuse it and add OAuth credentials (below).
- **Deployment pattern:** per-service unprivileged Debian 12 LXC on the **philip** Proxmox
  host with Docker — documented in
  [`../philip/decisions/0002-lxc-service-deployment-pattern.md`](../philip/decisions/0002-lxc-service-deployment-pattern.md).
  Reuse the same `pct create` recipe, read-only deploy key, `docker-compose.yml`,
  `update.sh` conventions.
- Language: **Python** (consistent with the poster).

## ⚠️ Critical difference from the poster: this needs OAuth, not an API key

`youtube-social-poster` only *reads* public data, so a **read-only API key** suffices.
**Creating/updating live broadcasts is a write operation** and requires **OAuth 2.0** with
the channel owner's consent:

- Scope: `https://www.googleapis.com/auth/youtube.force-ssl` (or `.../auth/youtube`).
- Flow: a **one-time** OAuth consent by a channel **manager/owner** → store the resulting
  **refresh token** as a secret; the service uses it to mint access tokens headlessly.
- In Google Cloud: **APIs & Services → Credentials → Create OAuth client ID** (type:
  *Desktop* or *Web*). The OAuth **consent screen** must list the account as a test user
  (or be published) and request the youtube scope.
- Endpoints: `liveBroadcasts.insert` (create), `.update` (edit), `.delete`/`.transition`
  (cancel), `.list` (reconcile). Optionally `liveBroadcasts.bind` to attach a reusable
  `liveStream` (RTMP key) so the ATEM feeds the scheduled event directly.

Quota: `liveBroadcasts.insert` costs ~50 units; default daily quota is 10,000 — fine for a
handful of events/day, but batch/limit the sync frequency.

## Proposed shape

- **Input:** Google Calendar **secret iCal URL** (private ICS address — read-only, no
  OAuth needed to *read* the calendar). Alternatively the Google Calendar API.
- **Parse:** `icalendar` + `recurring-ical-events` (expand `RRULE` recurrences into
  concrete instances within a look-ahead window, e.g. next 30 days).
- **Map** each VEVENT → a broadcast: `SUMMARY`→title, `DESCRIPTION`→description,
  `DTSTART`→`scheduledStartTime` (convert to RFC3339 **UTC**; mind the event TZ),
  privacy `public` (required so the eventual live hits the public feed the poster watches).
- **Idempotency (important):** keep a store (SQLite, like the poster) mapping calendar
  **UID → YouTube broadcast id**. On each sync: create missing, update changed
  (time/title), and cancel/delete broadcasts whose calendar event vanished. Never create a
  second broadcast for the same UID.
- **Schedule:** run on a timer (cron / APScheduler) every N minutes/hours.
- **No public endpoint / no Cloudflare tunnel needed** — this is an *outbound* job (pull
  ICS, call the YouTube API). Unlike the poster, it needs no inbound webhook, so skip the
  tunnel entirely.

## Open decisions (resolve before building)

1. **Which calendar / ICS feed?** URL of the Google Calendar, and which events become
   streams (all? a specific calendar? filtered by title/keyword or a category?).
2. **Stream key binding.** Should each broadcast be auto-**bound to the reusable RTMP
   stream** (the ATEM's persistent key) via `liveBroadcasts.bind`, or will the operator
   pick the event in Studio? (Auto-bind = the ATEM just goes live at service time.)
3. **Title/description templating.** Likely mirror the poster's style (title from calendar,
   a fixed description) — consider sharing wording with the poster's `POST_DESCRIPTION`.
4. **Look-ahead window & sync cadence** (e.g. create events up to 2 weeks out, sync hourly).
5. **Updates & cancellations.** Update the broadcast when the calendar event moves; delete
   or `cancel` it when the event is removed?
6. **Privacy & defaults.** Public (needed for the FB poster) vs unlisted; category;
   default thumbnail; made-for-kids flag; latency/DVR settings.
7. **Timezone source of truth** (parish is US Central / Birmingham AL) and DST handling.

## Suggested libraries

`google-api-python-client`, `google-auth`, `google-auth-oauthlib` (OAuth),
`icalendar`, `recurring-ical-events`, plus `httpx`/`requests` for fetching the ICS.

## Related

- [`../youtube-social-poster`](../youtube-social-poster) — downstream consumer (live → FB).
- [`../philip`](../philip) — host + deployment ADRs (this service follows ADR-0002, but
  does **not** need the ADR-0001 tunnel/edge since it has no inbound endpoint).
