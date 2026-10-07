"""The arrangement plan the agent proposes, and the deterministic checks on it.

The model only chooses section names, lengths and which loops play. Start
positions are computed here so the LLM never has to add up bars.
"""

from dataclasses import dataclass

from pydantic import BaseModel, Field

from loopitis.als.model import LiveSet


class Section(BaseModel):
    name: str = Field(description="Section name, e.g. 'Intro', 'Drop 1', 'Breakdown'.")
    bars: int = Field(gt=0, le=128, description="Length in bars.")
    clips: list[str] = Field(
        description="Ids of the session loops (e.g. 'C2') that play for the whole section."
    )
    purpose: str = Field(
        default="", description="One sentence on what this section does for the track."
    )


class ArrangementPlan(BaseModel):
    sections: list[Section] = Field(min_length=1)


@dataclass(frozen=True)
class PlacedSection:
    name: str
    start_bar: int  # 1-based, as Live displays it
    bars: int
    clips: list[str]
    purpose: str

    @property
    def end_bar(self) -> int:  # inclusive
        return self.start_bar + self.bars - 1


@dataclass
class PlanCheck:
    errors: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.errors

    def report(self) -> str:
        lines = [f"ERROR: {e}" for e in self.errors] + [f"warning: {w}" for w in self.warnings]
        return "\n".join(lines) or "No problems found."


def layout(plan: ArrangementPlan) -> list[PlacedSection]:
    placed, bar = [], 1
    for section in plan.sections:
        placed.append(
            PlacedSection(section.name, bar, section.bars, list(section.clips), section.purpose)
        )
        bar += section.bars
    return placed


def check_plan(plan: ArrangementPlan, live_set: LiveSet) -> PlanCheck:
    errors, warnings = [], []
    known = {clip.id: clip for clip in live_set.clips}
    bpb = live_set.beats_per_bar
    used = set()

    for section in plan.sections:
        where = f"section '{section.name}'"
        if len(set(section.clips)) != len(section.clips):
            errors.append(f"{where} lists a clip more than once")
        playing_tracks = {}
        for clip_id in section.clips:
            clip = known.get(clip_id)
            if clip is None:
                errors.append(f"{where}: unknown clip id {clip_id!r}")
                continue
            used.add(clip_id)
            if clip.kind == "midi" and not clip.notes:
                errors.append(f"{where}: {clip_id} is an empty clip")
            other = playing_tracks.get(clip.track_index)
            if other:
                track = live_set.track(clip.track_index).name
                errors.append(
                    f"{where}: {other} and {clip_id} are both on track '{track}'; "
                    "a track can only play one clip at a time"
                )
            playing_tracks[clip.track_index] = clip_id
            loop_bars = clip.length / bpb
            if clip.looping and loop_bars and section.bars % loop_bars:
                warnings.append(
                    f"{where} is {section.bars} bars but {clip_id} loops every {loop_bars:g} bars, "
                    "so the loop is cut off mid-phrase"
                )
            if not clip.looping and section.bars > loop_bars:
                warnings.append(
                    f"{where}: {clip_id} is a one-shot of {loop_bars:g} bars; it will be repeated"
                )
        if not section.clips:
            warnings.append(f"{where} is silent")
        if section.bars % 4:
            warnings.append(f"{where} is {section.bars} bars, not a multiple of 4")

    total = sum(s.bars for s in plan.sections)
    if total < 32:
        warnings.append(f"the arrangement is only {total} bars long")
    unused = [c.id for c in live_set.clips if c.id not in used and (c.kind == "audio" or c.notes)]
    if unused:
        warnings.append(f"loops never used: {', '.join(unused)}")
    return PlanCheck(errors, warnings)


def render_timeline(plan: ArrangementPlan, live_set: LiveSet) -> str:
    """A markdown section list plus a track-by-section grid."""
    placed = layout(plan)
    seconds_per_bar = live_set.beats_per_bar * 60 / live_set.tempo
    lines = ["| # | section | bars | length | purpose |", "|---|---|---|---|---|"]
    for i, section in enumerate(placed, 1):
        lines.append(
            f"| {i} | {section.name} | {section.start_bar}–{section.end_bar} | "
            f"{section.bars} bars | {section.purpose} |"
        )
    total_bars = sum(s.bars for s in placed)
    minutes, seconds = divmod(round(total_bars * seconds_per_bar), 60)
    lines += [
        "",
        f"Total: {total_bars} bars ≈ {minutes}:{seconds:02d} at {live_set.tempo:.0f} BPM",
        "",
    ]

    known = {clip.id: clip for clip in live_set.clips}
    rows: dict[str, list[str]] = {}
    for section_index, section in enumerate(placed):
        for clip_id in section.clips:
            if clip_id not in known:
                continue
            track = live_set.track(known[clip_id].track_index).name
            rows.setdefault(track, [""] * len(placed))[section_index] = clip_id
    if rows:
        lines.append(
            "| track | " + " | ".join(f"{i}. {s.name}" for i, s in enumerate(placed, 1)) + " |"
        )
        lines.append("|---|" + "---|" * len(placed))
        for track, cells in rows.items():
            lines.append(f"| {track} | " + " | ".join(c or "·" for c in cells) + " |")
    return "\n".join(lines)
