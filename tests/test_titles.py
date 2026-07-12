from datetime import datetime
from zoneinfo import ZoneInfo

from youtube_ics.enrich import LiturgicalInfo, _ordinal
from youtube_ics.models import Broadcast, Office
from youtube_ics.titles import build_description, build_title

CT = ZoneInfo("America/Chicago")


def _bc(office, label, y, m, d, hh):
    start = datetime(y, m, d, hh, 0, tzinfo=CT)
    return Broadcast(office=office, office_label=label, start=start, end=None)


def test_ordinals():
    assert [_ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd",
    ]


def test_sunday_title_matches_published_format():
    bc = _bc(Office.ORTHROS_LITURGY, "Orthros and Divine Liturgy", 2026, 7, 5, 9)
    info = LiturgicalInfo(tone=5, season="After Pentecost", week_of_season=6,
                          saints=["Holy Father Athanasios of Athos"])
    assert build_title(bc, info) == (
        "Orthros (Tone 5) and Divine Liturgy - 6th Sunday after Pentecost - 5 July 2026"
    )


def test_feast_takes_precedence_over_sunday_counter():
    bc = _bc(Office.ORTHROS_LITURGY, "Orthros and Divine Liturgy", 2026, 5, 31, 9)
    info = LiturgicalInfo(tone=8, season="After Pentecost", week_of_season=1,
                          primary_feast="Sunday of All Saints")
    assert "Sunday of All Saints" in build_title(bc, info)
    assert "Sunday after Pentecost" not in build_title(bc, info)


def test_weekday_liturgy_uses_feast_then_saint():
    bc = _bc(Office.LITURGY, "Divine Liturgy", 2026, 6, 29, 9)
    info = LiturgicalInfo(saints=["Apostles Peter and Paul"])
    assert build_title(bc, info) == "Divine Liturgy - Apostles Peter and Paul - 29 June 2026"


def test_plain_vespers_upgrades_to_great_vespers_on_feast():
    bc = _bc(Office.VESPERS, "Vespers", 2026, 5, 20, 18)
    feast = LiturgicalInfo(primary_feast="Constantine and Helen")
    ordinary = LiturgicalInfo(saints=["Holy Martyr Leontios"])
    assert build_title(bc, feast).startswith("Great Vespers - ")
    assert build_title(bc, ordinary).startswith("Vespers - ")


def test_other_service_is_name_and_date_only():
    bc = _bc(Office.OTHER, "Paraklisis", 2026, 8, 3, 18)
    info = LiturgicalInfo(saints=["Holy Fathers Isaac, Dalmatos and Faustos"])
    assert build_title(bc, info) == "Paraklisis - 3 August 2026"
    desc = build_description(bc, info)
    assert "Isaac" not in desc  # no day's-saint block for OTHER services
    assert desc.startswith("Paraklisis - 3 August 2026\n")
    assert desc.rstrip().endswith("http://www.melkitepat.org/")


def test_long_title_trims_commemoration_keeps_office_and_date():
    bc = _bc(Office.VESPERS, "Great Vespers", 2026, 7, 22, 18)
    long = ("Transfer of the Remains of the Holy and Illustrious Great-Martyr "
            "Nobody of Somewhereville in Deepest Asia Minor")
    info = LiturgicalInfo(primary_feast=long)
    title = build_title(bc, info)
    assert len(title) <= 100
    assert title.startswith("Great Vespers - ")
    assert title.endswith(" - 22 July 2026")  # date preserved
    assert "…" in title


def test_description_shape():
    bc = _bc(Office.VESPERS, "Vespers", 2026, 6, 17, 18)
    info = LiturgicalInfo(saints=["Holy Martyr Leontios"])
    desc = build_description(bc, info)
    lines = desc.split("\n")
    assert lines[0] == build_title(bc, info)
    assert lines[1] == ""
    assert lines[2] == "Holy Martyr Leontios"
    assert desc.rstrip().endswith("http://www.melkitepat.org/")
