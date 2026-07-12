# Handoff: `/api/liturgical-day/:date` is now a real data contract

**Reply to** `docs/briefs/liturgical-day-api-for-youtube-ics.md` (filed into `melkite-synaxis`).
**Status:** ✅ Implemented, additive, all your acceptance criteria pass. **Testable right now against a
local typikon** — see below. Not yet deployed to `melkitetypikon.org` (lives on the branch
`typikon-liturgical-day-api` in `melkite-synaxis`, commit `dfa7cf5`).

You can retire the HTML scraping in `enrich.py` and drop in an `ApiTypikonSource`.

---

## Test it now — localhost:3000

A local typikon is running with the new endpoint:

```bash
curl -s http://localhost:3000/api/liturgical-day/2026-07-05 | jq
```

CORS is open (`Access-Control-Allow-Origin: *`, `GET`). The endpoint is pure-by-date and ignores
`?parish=`. Point `TYPIKON_BASE_URL` (or equivalent) at `http://localhost:3000` to integrate against it
before it ships to prod.

> The **production** site (`https://melkitetypikon.org`) still returns only the original four fields
> (`date, name, day, totalDays, kathismaSchedule`) until this branch deploys. Keep `ScrapeTypikonSource`
> as the fallback until then.

---

## The contract (additive — original fields unchanged)

```jsonc
GET /api/liturgical-day/2026-07-05
{
  // --- original, unchanged ---
  "date": "2026-07-05",
  "name": null, "day": null, "totalDays": null,   // fasting-period info
  "kathismaSchedule": "outside-lent-1",

  // --- new ---
  "tone": 5,
  "season": "after-pentecost",          // stable kebab slug of the season enum
  "weekOfSeason": 6,                    // season-appropriate week counter (Sundays-after-Pentecost here)
  "isSunday": true,
  "dayName": "6th Sunday after Pentecost",   // GENERATED ordinal; null when not an after-Pentecost Sunday
  "namedDays": [],                      // named-Sunday designations (see below); [] when none
  "feasts": [],                         // ranked; [] on ordinary days
  "saints": [
    "Holy Father Athanasios of Athos",
    "Holy Wonderworker Lampados; & the Holy Woman Martha, mother of Simeon the Hermit"
  ],
  "vespersRank": "great"                // "great" | "daily"
}
```

### Real responses for your acceptance dates (copied from the live endpoint)

```jsonc
// Transfiguration — great feast, Great Vespers appointed (served on its eve)
GET /2026-08-06
{ "tone":1, "season":"after-pentecost", "weekOfSeason":11, "isSunday":false,
  "dayName":null, "namedDays":[],
  "feasts":[{"name":"Feast of the Transfiguration of our Lord","rank":"great-feast"}],
  "saints":[], "vespersRank":"great" }

// Ordinary weekday
GET /2026-07-16
{ "tone":6, "season":"after-pentecost", "weekOfSeason":8, "isSunday":false,
  "dayName":null, "namedDays":[], "feasts":[],
  "saints":["Hieromartyr Athenogenes and his ten disciples"], "vespersRank":"daily" }

// Named Sunday in another season — both forms returned, you choose (see "Two decisions")
GET /2026-02-01
{ "tone":1, "season":"pre-lenten", "weekOfSeason":2, "isSunday":true,
  "dayName":null,
  "namedDays":["Sunday of the Prodigal Son","4th Sunday after Theophany"],
  "feasts":[{"name":"Sunday of the Prodigal Son","rank":"fourth-class"},
            {"name":"4th Sunday after Theophany","rank":"fourth-class"}],
  "saints":["Preparation for the Feast of the Encounter","Holy Martyr Tryphon"],
  "vespersRank":"great" }
```

---

## Field → `LiturgicalInfo` mapping

| Your field                     | API field           | Note |
|--------------------------------|---------------------|------|
| `tone`                         | `tone`              | direct |
| `season`                       | `season`            | now a slug (`"after-pentecost"`); your `sunday_after_pentecost` check `"pentecost" in season` still holds |
| `pent_week`                    | `weekOfSeason`      | equals Sundays-after-Pentecost in that season |
| `primary_feast`               | `feasts[0].name` / `feasts` by `rank` | pick by real precedence now, not "first feast else first saint" |
| `saints`                       | `saints`            | direct list, display order, commemorations only (class 4–5) |
| *(new)* vespers rank           | `vespersRank`       | **retires your "a feast exists ⇒ Great" guess** — authoritative from the rubrics engine |
| `sunday_after_pentecost` (property) | `dayName`      | API generates the identical string; use it directly or keep deriving from `weekOfSeason` |

### Two decisions you should know about

1. **`vespersRank` is by the `date+1` convention you proposed.** `vespersRank` for date `D` is the rank of
   the Vespers that *opens* liturgical day `D` (served the evening before) — exactly your "request `date+1`
   for Vespers" model. So the Great Vespers broadcast on the evening of **Aug 5** → query **`2026-08-06`** →
   `vespersRank: "great"`. `"great"` = Great Vespers appointed; `"daily"` = Daily/Lenten Vespers. Handles
   feast eves and Sundays correctly (Sunday always `"great"`).
2. **`dayName` vs `namedDays` are separate on purpose.** `dayName` is the *generated* ordinal
   ("Nth Sunday after Pentecost"); `namedDays` carries the *named* designations from the calendar
   ("Sunday of the Prodigal Son", "Sunday before Nativity"). The API deliberately does **not** decide which
   to display — you pick (e.g. prefer `namedDays[0]` when present, else `dayName`). On a plain Sunday after
   Pentecost you get `dayName` and empty `namedDays`; on a named Sunday you get both.

### Feast `rank` slugs (lower class = higher precedence)

`great-feast` (class 0–1) · `second-class` (2) · `third-class` (3) · `fourth-class` (4) · `simple` (5).

---

## Drop-in `ApiTypikonSource`

```python
import httpx
from datetime import date

class ApiTypikonSource(TypikonSource):
    def __init__(self, base_url: str = "http://localhost:3000", timeout: float = 5.0):
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)
        self._cache: dict[date, LiturgicalInfo] = {}

    def get(self, day: date) -> LiturgicalInfo:
        if day not in self._cache:
            r = self._client.get(f"{self._base}/api/liturgical-day/{day.isoformat()}")
            r.raise_for_status()
            d = r.json()
            self._cache[day] = LiturgicalInfo(
                tone=d.get("tone"),
                season=d.get("season"),
                pent_week=d.get("weekOfSeason"),
                # pick the highest-precedence feast (feasts are ranked; [] on ordinary days)
                primary_feast=(d["feasts"][0]["name"] if d.get("feasts") else None),
                saints=d.get("saints", []),
            )
            # New: authoritative vespers rank — add `vespers_rank` to LiturgicalInfo to use it.
            # self._cache[day].vespers_rank = d.get("vespersRank")
            # Day title: prefer the named Sunday, else the generated ordinal:
            # name = (d["namedDays"][0] if d.get("namedDays") else d.get("dayName"))
        return self._cache[day]
```

Swapping `ScrapeTypikonSource` → `ApiTypikonSource` is the one-line change your brief described; the
`LiturgicalInfo` contract is unchanged except you can now add a `vespers_rank` field and stop guessing it.

---
*Handoff from: melkite-synaxis / apps/typikon · endpoint `apps/typikon/src/server.ts` · branch `typikon-liturgical-day-api`.*
