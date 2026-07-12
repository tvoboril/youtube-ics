from datetime import datetime
from zoneinfo import ZoneInfo

from youtube_ics.enrich import liturgical_info_from_api
from youtube_ics.models import Broadcast, Office
from youtube_ics.titles import build_title, office_label

CT = ZoneInfo("America/Chicago")

# Sample payloads copied from docs/typikon-api-handoff.md
ORDINARY_SUNDAY = {
    "date": "2026-07-05", "tone": 5, "season": "after-pentecost", "weekOfSeason": 6,
    "isSunday": True, "dayName": "6th Sunday after Pentecost", "namedDays": [], "feasts": [],
    "saints": ["Holy Father Athanasios of Athos"], "vespersRank": "great",
}
TRANSFIGURATION = {
    "date": "2026-08-06", "tone": 1, "season": "after-pentecost", "weekOfSeason": 11,
    "isSunday": False, "dayName": None, "namedDays": [],
    "feasts": [{"name": "Feast of the Transfiguration of our Lord", "rank": "great-feast"}],
    "saints": [], "vespersRank": "great",
}
ORDINARY_WEEKDAY = {
    "date": "2026-07-16", "tone": 6, "season": "after-pentecost", "weekOfSeason": 8,
    "isSunday": False, "dayName": None, "namedDays": [], "feasts": [],
    "saints": ["Hieromartyr Athenogenes and his ten disciples"], "vespersRank": "daily",
}
NAMED_SUNDAY = {
    "date": "2026-02-01", "tone": 1, "season": "pre-lenten", "weekOfSeason": 2,
    "isSunday": True, "dayName": None,
    "namedDays": ["Sunday of the Prodigal Son", "4th Sunday after Theophany"],
    "feasts": [{"name": "Sunday of the Prodigal Son", "rank": "fourth-class"}],
    "saints": ["Holy Martyr Tryphon"], "vespersRank": "great",
}


def _bc(office, label, y, m, d, hh):
    start = datetime(y, m, d, hh, 0, tzinfo=CT)
    return Broadcast(office=office, office_label=label, start=start, end=None)


def test_mapping_basic_fields():
    info = liturgical_info_from_api(ORDINARY_SUNDAY)
    assert info.tone == 5 and info.pent_week == 6
    assert info.primary_feast is None  # ordinary Sunday: no feast
    assert info.sunday_name == "6th Sunday after Pentecost"
    assert info.vespers_rank == "great"


def test_transfiguration_feast_wins():
    info = liturgical_info_from_api(TRANSFIGURATION)
    assert info.primary_feast == "Feast of the Transfiguration of our Lord"
    bc = _bc(Office.LITURGY, "Divine Liturgy", 2026, 8, 6, 9)
    assert build_title(bc, info) == (
        "Divine Liturgy - Feast of the Transfiguration of our Lord - 6 August 2026"
    )


def test_named_sunday_uses_named_designation():
    info = liturgical_info_from_api(NAMED_SUNDAY)
    bc = _bc(Office.ORTHROS_LITURGY, "Orthros and Divine Liturgy", 2026, 2, 1, 9)
    assert "Sunday of the Prodigal Son" in build_title(bc, info)


def test_vespers_rank_drives_label_authoritatively():
    great = liturgical_info_from_api(TRANSFIGURATION)     # vespersRank great
    daily = liturgical_info_from_api(ORDINARY_WEEKDAY)    # vespersRank daily
    bc = _bc(Office.VESPERS, "Vespers", 2026, 8, 5, 18)
    assert office_label(bc, great) == "Great Vespers"
    assert office_label(bc, daily) == "Vespers"


def test_vespers_rank_overrides_scraper_feast_heuristic():
    # Even if a (weekday) feast is present, an explicit "daily" rank must win.
    info = liturgical_info_from_api(
        {**ORDINARY_WEEKDAY, "feasts": [{"name": "Some Minor Feast", "rank": "simple"}],
         "vespersRank": "daily"}
    )
    info.primary_feast = "Some Minor Feast"
    bc = _bc(Office.VESPERS, "Vespers", 2026, 7, 15, 18)
    assert office_label(bc, info) == "Vespers"
