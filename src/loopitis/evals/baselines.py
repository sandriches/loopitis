"""Non-LLM arrangements: the scores the agent has to beat."""

from loopitis.als.model import Clip, LiveSet
from loopitis.analysis import role_hint
from loopitis.plan import ArrangementPlan

ROLE_ORDER = ["drums", "bass", "chords", "lead", "vocals", "texture", "audio"]


def one_clip_per_track(live_set: LiveSet) -> list[Clip]:
    chosen: dict[int, Clip] = {}
    for clip in live_set.clips:
        if (clip.kind == "audio" or clip.notes) and clip.track_index not in chosen:
            chosen[clip.track_index] = clip
    return list(chosen.values())


def flat(live_set: LiveSet, bars: int = 96) -> ArrangementPlan:
    """Every loop, all the way through."""
    clips = [c.id for c in one_clip_per_track(live_set)]
    return ArrangementPlan(sections=[{"name": "Loop", "bars": bars, "clips": clips}])


def layered(live_set: LiveSet, step: int = 8, peak: int = 32) -> ArrangementPlan:
    """Add one track every `step` bars in role order, hold everything for
    `peak` bars, then remove tracks in reverse order."""
    bpb = live_set.beats_per_bar

    def rank(clip: Clip) -> int:
        role = role_hint(clip, live_set.track(clip.track_index), bpb)
        return ROLE_ORDER.index(role) if role in ROLE_ORDER else len(ROLE_ORDER)

    clips = [c.id for c in sorted(one_clip_per_track(live_set), key=rank)]
    sections = [
        {"name": f"Build {i}", "bars": step, "clips": clips[:i]} for i in range(1, len(clips))
    ]
    sections.append({"name": "Peak", "bars": peak, "clips": clips})
    sections += [
        {"name": f"Outro {i}", "bars": step, "clips": clips[:i]}
        for i in range(len(clips) - 1, 0, -2)
    ]
    return ArrangementPlan(sections=sections)


BASELINES = {"flat": flat, "layered": layered}
