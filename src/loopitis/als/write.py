"""Write an arrangement plan into a copy of a Live Set.

Session clips are copied onto the arrangement timeline. The existing
arrangement (clips, take lanes, track automation and locators) is cleared in
the copy, so the result contains only the planned arrangement. The source
file is never written to.
"""

import copy
import gzip
import xml.etree.ElementTree as ET
from pathlib import Path

from loopitis.als.model import Clip, LiveSet
from loopitis.als.parse import (
    arrangement_events,
    load_xml,
    parse_root,
    session_clip_element,
)
from loopitis.plan import ArrangementPlan, check_plan, layout


class PlanError(ValueError):
    pass


def write_arrangement(source: Path, plan: ArrangementPlan, output: Path | None = None) -> Path:
    source = Path(source).expanduser().resolve()
    output = Path(output) if output else default_output_path(source)
    if output.resolve() == source:
        raise PlanError("Refusing to overwrite the source set")

    root = load_xml(source)
    live_set = parse_root(root, source)
    check = check_plan(plan, live_set)
    if not check.ok:
        raise PlanError(check.report())

    apply_plan(root, live_set, plan)
    save(root, output)
    return output


def strip_arrangement(root: ET.Element) -> None:
    """Remove every arrangement clip, take lane, track automation and locator."""
    live_set_el = root.find("LiveSet")
    for track_el in live_set_el.find("Tracks"):
        _clear_arrangement(track_el)
    _set_locators(live_set_el, [])


def apply_plan(root: ET.Element, live_set: LiveSet, plan: ArrangementPlan) -> None:
    strip_arrangement(root)
    live_set_el = root.find("LiveSet")
    track_els = list(live_set_el.find("Tracks"))

    bpb = live_set.beats_per_bar
    clips = {clip.id: clip for clip in live_set.clips}
    # Each looping clip that continues into the next section is extended rather
    # than restarted, so phrases longer than a section stay intact.
    open_clips: dict[str, ET.Element] = {}
    for section in layout(plan):
        start = (section.start_bar - 1) * bpb
        end = start + section.bars * bpb
        still_open = {}
        for clip_id in section.clips:
            clip = clips[clip_id]
            if clip.looping and clip_id in open_clips:
                el = open_clips[clip_id]
                _set_value(el, "CurrentEnd", end)
                still_open[clip_id] = el
                continue
            events = arrangement_events(track_els[clip.track_index])
            source_el = session_clip_element(track_els[clip.track_index], clip.slot_index)
            for seg_start, seg_end in _segments(clip, start, end):
                el = _placed_copy(source_el, seg_start, seg_end)
                events.append(el)
            if clip.looping:
                still_open[clip_id] = el
        open_clips = still_open

    for track_el in track_els:
        events = arrangement_events(track_el)
        if events is not None:
            for index, el in enumerate(events):
                el.set("Id", str(index))

    _set_locators(live_set_el, [((s.start_bar - 1) * bpb, s.name) for s in layout(plan)])


def default_output_path(source: Path) -> Path:
    """'<name> (arranged).als' next to the source, so relative sample paths still resolve."""
    candidate = source.with_name(f"{source.stem} (arranged).als")
    n = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem} (arranged {n}).als")
        n += 1
    return candidate


def save(root: ET.Element, output: Path) -> None:
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
    with gzip.open(output, "wb") as f:
        f.write(xml.encode("utf-8"))


def _segments(clip: Clip, start: float, end: float) -> list[tuple[float, float]]:
    """A looping clip spans the whole section; a one-shot is repeated back to back."""
    if clip.looping or clip.length <= 0:
        return [(start, end)]
    segments, t = [], start
    while t < end:
        segments.append((t, min(t + clip.length, end)))
        t += clip.length
    return segments


def _placed_copy(source_el: ET.Element, start: float, end: float) -> ET.Element:
    el = copy.deepcopy(source_el)
    el.set("Time", _num(start))
    _set_value(el, "CurrentStart", start)
    _set_value(el, "CurrentEnd", end)
    _set_value(el, "TakeId", 1)  # arrangement clips on tracks without take lanes use 1
    return el


def _clear_arrangement(track_el: ET.Element) -> None:
    events = arrangement_events(track_el)
    if events is not None:
        events.clear()
    take_lanes = track_el.find("TakeLanes/TakeLanes")
    if take_lanes is not None:
        take_lanes.clear()
    envelopes = track_el.find("AutomationEnvelopes/Envelopes")
    if envelopes is not None:
        envelopes.clear()


def _set_locators(live_set_el: ET.Element, markers: list[tuple[float, str]]) -> None:
    container = live_set_el.find("Locators/Locators")
    if container is None:
        return
    container.clear()
    for index, (time, name) in enumerate(markers):
        locator = ET.SubElement(container, "Locator", Id=str(index))
        for tag, value in (
            ("LomId", "0"),
            ("Time", _num(time)),
            ("Name", name),
            ("Annotation", ""),
            ("IsSongStart", "false"),
        ):
            ET.SubElement(locator, tag, Value=value)


def _set_value(el: ET.Element, path: str, value: float | str) -> None:
    target = el.find(path)
    if target is None:
        raise PlanError(f"Clip is missing <{path}>")
    target.set("Value", _num(value) if isinstance(value, (int, float)) else value)


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))
