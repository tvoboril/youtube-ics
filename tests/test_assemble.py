from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from youtube_ics.config import Config
from youtube_ics.enrich import LiturgicalInfo
from youtube_ics.ics import assemble_broadcasts, load_ics
from youtube_ics.models import Occurrence, Office
from youtube_ics.plan import build_plan

CT = ZoneInfo("America/Chicago")
UTC = ZoneInfo("UTC")

# A Sunday with back-to-back Orthros (14:00–15:30Z) and Divine Liturgy (15:30–17:00Z),
# mirroring the real calendar that produced the Jul-26 duplicates.
_SUNDAY_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:orthros-26
DTSTART:20260726T140000Z
DTEND:20260726T153000Z
SUMMARY:Orthros (Livestreamed)
END:VEVENT
BEGIN:VEVENT
UID:liturgy-26
DTSTART:20260726T153000Z
DTEND:20260726T170000Z
SUMMARY:Divine Liturgy (Livestreamed)
END:VEVENT
END:VCALENDAR
"""


class _NoTypikon:
    def get(self, _date):
        return LiturgicalInfo()


def _plan(now):
    cfg = Config(ics_url="x", lookahead_days=14, typikon_api_url="x", stream_key=None, db_path=":memory:")
    return build_plan(cfg, now=now, cal=load_ics(_SUNDAY_ICS), typikon=_NoTypikon())


def test_started_service_is_dropped_from_the_plan():
    """`between` returns overlapping (in-progress) occurrences; the plan must exclude a service
    once it has started, or a mid-service reconcile duplicates the live broadcast (Jul-26)."""
    # Before it starts: one merged Orthros+Liturgy broadcast.
    before = _plan(datetime(2026, 7, 26, 13, 30, tzinfo=UTC))
    assert len(before) == 1
    assert before[0].broadcast.office is Office.ORTHROS_LITURGY

    # After the Orthros (=merged start) has begun: the whole unit drops.
    assert _plan(datetime(2026, 7, 26, 14, 15, tzinfo=UTC)) == []

    # Past the Orthros→Liturgy seam, only the Liturgy half still overlaps the window; it must NOT
    # be planned as a standalone (that produced the "Divine Liturgy - <weekday saint>" duplicate).
    assert _plan(datetime(2026, 7, 26, 15, 45, tzinfo=UTC)) == []


def _occ(uid, summary, y, m, d, hh, dur_min, recurring):
    start = datetime(y, m, d, hh, 0, tzinfo=CT)
    return Occurrence(uid, summary, start, start + timedelta(minutes=dur_min), recurring)


def test_sunday_orthros_and_liturgy_merge_into_one():
    occ = [
        _occ("o", "Orthros", 2026, 7, 5, 9, 90, True),
        _occ("l", "Divine Liturgy", 2026, 7, 5, 10, 90, True),  # 10:30 back-to-back
    ]
    # make them actually back-to-back
    occ[1].start = occ[0].end
    occ[1].end = occ[1].start + timedelta(minutes=90)
    bcs = assemble_broadcasts(occ)
    assert len(bcs) == 1
    b = bcs[0]
    assert b.office is Office.ORTHROS_LITURGY
    assert b.start == occ[0].start and b.end == occ[1].end
    assert set(b.source_uids) == {"o", "l"}


def test_vespers_takes_next_days_commemoration():
    (b,) = assemble_broadcasts([_occ("v", "Vespers", 2026, 6, 17, 18, 60, True)])
    assert b.office is Office.VESPERS
    assert b.commemoration_date() == datetime(2026, 6, 18).date()


def test_great_vespers_label_preserved():
    (b,) = assemble_broadcasts(
        [_occ("gv", "Great Vespers for the Dormition", 2026, 8, 14, 18, 60, False)]
    )
    assert b.office_label == "Great Vespers"
    assert b.from_feast is True


def test_feast_supersedes_generic_recurring_same_day():
    occ = [
        _occ("recur", "Vespers", 2026, 8, 14, 18, 60, True),  # generic weekly
        _occ("feast", "Great Vespers for the Dormition", 2026, 8, 14, 19, 60, False),
    ]
    bcs = assemble_broadcasts(occ)
    assert len(bcs) == 1
    assert bcs[0].source_uids == ["feast"]
