# youtube-ics

Create scheduled **YouTube live broadcasts** from a **Google Calendar (ICS)** feed, with
titles and descriptions enriched from a liturgical **typikon API**. Built for St. George
Melkite Catholic Church so the parish's calendar automatically produces each service's
YouTube live event — no more hand-creating them in Studio.

```
Google Calendar (ICS)  ──►  youtube-ics  ──►  YouTube scheduled live broadcast
   (events tagged                                   │ (an ATEM/OBS goes live at
    "(Livestreamed)")                               ▼  service time)
                                          melkite-typikon API
                                     (tone · feast · saints · vespers rank)
```

## How it works

On each run it reconciles the calendar against the channel:

1. **Fetch + expand** the ICS feed (recurring events included) over a look-ahead window.
2. **Select** events whose title carries the **`(Livestreamed)`** tag — the single source
   of truth for "stream this." (So you control it from the calendar; untagged events, e.g.
   ones you don't stream, are simply ignored.)
3. **Assemble** broadcasts: a Sunday Orthros + the back-to-back Divine Liturgy merge into
   one broadcast; a specific feast service supersedes the generic recurring one that day.
4. **Enrich** each one from the typikon API for the service's date (Vespers uses the *next*
   liturgical day, since Vespers opens it): tone, the feast or "Nth Sunday after Pentecost"
   name, the day's saints, and whether Great Vespers is appointed.
5. **Title & describe** to match the parish's format, e.g.
   `Orthros (Tone 5) and Divine Liturgy - 6th Sunday after Pentecost - 5 July 2026`.
6. **Create / update / cancel** on YouTube (public, bound to a reusable RTMP stream), using
   a small SQLite store for idempotency. Events removed from the calendar are cancelled;
   existing channel broadcasts are **adopted** rather than duplicated if the store is lost.

## Configuration

All via environment (see [`.env.example`](.env.example)):

| Variable | Purpose |
|---|---|
| `GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` / `_REFRESH_TOKEN` | OAuth for the YouTube write API (minted once with `auth`, below) |
| `YOUTUBE_STREAM_KEY` | The reusable RTMP stream key; each broadcast binds to its `liveStream` |
| `TYPIKON_API_URL` | Base URL of the melkite-typikon `/api/liturgical-day/:date` API |
| `ICS_URL` / `ICS_CALENDAR_ID` | Calendar to read (prefer the private "secret iCal" URL for freshness) |
| `LOOKAHEAD_DAYS` | How far ahead to keep populated (1–14, default 14) |
| `DB_PATH` | SQLite state file |

Parish-specific bits live in the code and are easy to change for another parish: the default
calendar id in `config.py` and the description footer in `titles.py`.

## Usage

```bash
pip install .

# One-time: consent as a channel manager/owner → writes GOOGLE_OAUTH_* into .env
youtube-ics auth --client-secrets client_secret.json

youtube-ics list-streams          # confirm YOUTUBE_STREAM_KEY resolves to a liveStream
youtube-ics sync --dry-run        # preview create/adopt/update/cancel actions
youtube-ics sync                  # apply them
youtube-ics run                   # loop: reconcile, then sleep to 15 min before next event
```

`run` is the long-lived entrypoint: it keeps the window populated and re-checks each event
shortly before it starts, so a last-minute cancellation removes the YouTube event in time.

**Channel note:** the channel must have live streaming enabled, and the OAuth consent must
be given by an account that owns the channel in the OAuth sense. For a **Brand Account**,
consent as the Brand Account (or a Google-level manager of it) and pick it in the account
chooser — a YouTube Studio "delegated" manager will authenticate with no channels.

## Deploy (Docker)

```bash
docker compose up -d --build      # runs `youtube-ics run`; state persists in ./data
```

`.env` (with the OAuth + stream secrets) is supplied out-of-band and never committed. On a
Proxmox host it runs as an unprivileged Debian LXC with Docker; it's an outbound-only job,
so it needs no published ports or inbound tunnel.

## Requirements

- Python 3.11+
- A YouTube channel with live streaming enabled + an OAuth client (Desktop type,
  `youtube.force-ssl` scope)
- A reachable melkite-typikon `/api/liturgical-day/:date` API

## License

MIT — see [LICENSE](LICENSE).
