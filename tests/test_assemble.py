from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from youtube_ics.ics import assemble_broadcasts
from youtube_ics.models import Occurrence, Office

CT = ZoneInfo("America/Chicago")


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
