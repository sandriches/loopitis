import os
from pathlib import Path

import pytest

from loopitis.als.parse import decode_time_signature, parse
from loopitis.als.write import PlanError, write_arrangement
from loopitis.plan import ArrangementPlan
from tests.live_set_builder import demo_set


@pytest.fixture
def demo(tmp_path) -> Path:
    return demo_set(tmp_path / "demo.als")


def plan(*sections: tuple[str, int, list[str]]) -> ArrangementPlan:
    return ArrangementPlan(sections=[{"name": n, "bars": b, "clips": c} for n, b, c in sections])


def test_parse_reads_tempo_tracks_and_clips(demo):
    live_set = parse(demo)

    assert live_set.tempo == 170
    assert live_set.time_signature == (4, 4)
    assert [c.id for c in live_set.clips] == ["C1", "C2", "C3", "C4", "C5", "C6"]
    chords = live_set.clip("C3")
    assert (chords.length, chords.looping, len(chords.notes)) == (16, True, 6)
    assert live_set.track(chords.track_index).group == "Synths"
    assert live_set.track(0).drum_pads == {36: "Kick 909", 38: "Snare 909"}
    assert live_set.clip("C6").looping is False


@pytest.mark.parametrize(
    ("encoded", "expected"), [(201, (4, 4)), (200, (3, 4)), (303, (7, 8)), (100, (2, 2))]
)
def test_decode_time_signature(encoded, expected):
    assert decode_time_signature(encoded) == expected


def test_write_places_clips_at_section_positions(demo):
    output = write_arrangement(
        demo,
        plan(("Intro", 4, ["C1"]), ("Drop", 8, ["C1", "C2", "C3"]), ("Outro", 4, ["C3"])),
    )

    result = parse(output)
    by_track = {t.name: [(c.start, c.end) for c in t.arrangement_clips] for t in result.tracks}
    # Drums loop through Intro and Drop as one continuous clip (bars 1-12).
    assert by_track["Drums"] == [(0, 48)]
    assert by_track["Sub"] == [(16, 48)]
    assert by_track["Chords A"] == [(16, 64)]
    assert [(loc.name, loc.time) for loc in result.locators] == [
        ("Intro", 0),
        ("Drop", 16),
        ("Outro", 48),
    ]


def test_write_repeats_one_shots_to_fill_a_section(demo):
    output = write_arrangement(demo, plan(("Rise", 8, ["C6"])))

    riser = next(t for t in parse(output).tracks if t.name == "Riser")
    assert [(c.start, c.end) for c in riser.arrangement_clips] == [(0, 16), (16, 32)]


def test_write_never_touches_the_source(demo):
    before = demo.read_bytes()

    first = write_arrangement(demo, plan(("A", 4, ["C1"])))
    second = write_arrangement(demo, plan(("A", 4, ["C1"])))

    assert demo.read_bytes() == before
    assert first.name == "demo (arranged).als"
    assert second.name == "demo (arranged 2).als"
    with pytest.raises(PlanError, match="overwrite"):
        write_arrangement(demo, plan(("A", 4, ["C1"])), output=demo)


def test_write_rejects_invalid_plans(demo):
    with pytest.raises(PlanError, match="unknown clip id 'C99'"):
        write_arrangement(demo, plan(("A", 4, ["C99"])))


@pytest.mark.skipif(
    not os.getenv("LOOPITIS_SAMPLE_ALS"), reason="set LOOPITIS_SAMPLE_ALS to a real .als file"
)
def test_round_trip_on_a_real_set(tmp_path):
    source = Path(os.environ["LOOPITIS_SAMPLE_ALS"])
    live_set = parse(source)
    usable = [c.id for c in live_set.clips if c.kind == "audio" or c.notes]
    output = write_arrangement(source, plan(("All", 8, usable[:1])), tmp_path / "out.als")

    assert len(parse(output).clips) == len(live_set.clips)
