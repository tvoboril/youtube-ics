from youtube_ics.ics import is_livestream_tagged, strip_tag


def test_tagged_events_are_included():
    for s in [
        "Divine Liturgy (Livestreamed)",
        "Orthros (Livestreamed)",
        "Great Vespers for the Dormition (Livestreamed)",
        "Orthros (Livestream)",  # lenient variant
        "Divine Liturgy (Live Streamed)",  # inner space tolerated
        "divine liturgy (LIVESTREAMED)",  # case-insensitive
    ]:
        assert is_livestream_tagged(s), s


def test_untagged_events_are_excluded():
    for s in [
        "Divine Liturgy",  # e.g. the Saturday liturgy they don't stream
        "Orthros",
        "Great Vespers for the Dormition",
        "King's Men",
        "NO Divine Liturgy",
    ]:
        assert not is_livestream_tagged(s), s


def test_strip_tag_cleans_title():
    assert strip_tag("Divine Liturgy (Livestreamed)") == "Divine Liturgy"
    assert strip_tag("Orthros (Livestreamed)") == "Orthros"
    assert strip_tag("Great Vespers for the Dormition (Livestreamed)") == (
        "Great Vespers for the Dormition"
    )
    # tag mid-string, tolerate leftover double spaces
    assert strip_tag("Vespers (Livestreamed) for Palm Sunday") == "Vespers for Palm Sunday"
