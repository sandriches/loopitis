import pytest

from loopitis.als.model import Note
from loopitis.als.parse import parse
from loopitis.analysis import chord_name, detect_key, pitch_name
from loopitis.plan import ArrangementPlan, check_plan, render_timeline
from loopitis.summary import clip_detail, project_summary
from tests.live_set_builder import demo_set


@pytest.fixture
def live_set(tmp_path):
    return parse(demo_set(tmp_path / "demo.als"))


def notes(*pitches: int) -> list[Note]:
    return [Note(p, i, 1, 100) for i, p in enumerate(pitches)]


def test_detect_key_finds_c_major_and_a_minor():
    assert str(detect_key(notes(60, 62, 64, 65, 67, 69, 71, 72, 67, 64, 60))) == "C major"
    assert str(detect_key(notes(57, 60, 64, 57, 59, 60, 62, 64, 65, 64, 57, 69))) == "A minor"


@pytest.mark.parametrize(
    ("pitches", "expected"),
    [({60, 64, 67}, "C"), ({57, 60, 64}, "Am"), ({64, 67, 72}, "C/E"), ({60, 63, 67, 70}, "Cm7")],
)
def test_chord_name(pitches, expected):
    assert chord_name(pitches) == expected


def test_pitch_names_use_live_octaves():
    assert pitch_name(60) == "C3"
    assert pitch_name(36) == "C1"


def test_summary_lists_loops_layers_and_rows(live_set):
    summary = project_summary(live_set)

    assert "| C1 | Drums | drums |" in summary
    assert "| C5 | Empty | empty |" in summary
    assert "- C3, C4" in summary  # the two chord tracks are layers
    assert "Scene 2: C3, C4" in summary


def test_clip_detail_uses_drum_pad_names(live_set):
    assert "1.1.1 Kick 909" in clip_detail(live_set, "C1")
    assert "Cm → Ab" in clip_detail(live_set, "C3")


def test_check_plan_catches_impossible_plans(live_set):
    plan = ArrangementPlan(
        sections=[
            {"name": "A", "bars": 8, "clips": ["C3", "C3"]},
            {"name": "B", "bars": 6, "clips": ["C5", "C9"]},
        ]
    )

    check = check_plan(plan, live_set)

    assert not check.ok
    assert any("more than once" in e for e in check.errors)
    assert any("C5 is an empty clip" in e for e in check.errors)
    assert any("unknown clip id 'C9'" in e for e in check.errors)
    assert any("not a multiple of 4" in w for w in check.warnings)


def test_check_plan_rejects_two_clips_on_one_track(tmp_path):
    from tests.live_set_builder import live_set as build
    from tests.live_set_builder import midi_clip, save, track

    path = save(
        build(
            [
                track(
                    "MidiTrack",
                    "Keys",
                    1,
                    {0: midi_clip([(60, 0, 1)], 4), 1: midi_clip([(62, 0, 1)], 4)},
                )
            ]
        ),
        tmp_path / "two.als",
    )
    plan = ArrangementPlan(sections=[{"name": "A", "bars": 4, "clips": ["C1", "C2"]}])

    assert any("both on track 'Keys'" in e for e in check_plan(plan, parse(path)).errors)


def test_timeline_computes_bar_positions(live_set):
    plan = ArrangementPlan(
        sections=[
            {"name": "Intro", "bars": 16, "clips": ["C1"]},
            {"name": "Drop", "bars": 32, "clips": ["C1", "C2", "C3"]},
        ]
    )

    timeline = render_timeline(plan, live_set)

    assert "| 2 | Drop | 17–48 | 32 bars |" in timeline
    assert "| Drums | C1 | C1 |" in timeline
    assert "| Sub | · | C2 |" in timeline
