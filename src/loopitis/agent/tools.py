"""Tools the agent uses to read a Live Set and write an arrangement.

Every tool takes the .als path, so the agent's only state is the
conversation; parsing is cached per file, so repeated calls are cheap.
"""

from pathlib import Path

from langchain_core.tools import tool

from loopitis.als.parse import parse
from loopitis.als.write import PlanError
from loopitis.als.write import write_arrangement as write_set
from loopitis.plan import ArrangementPlan, check_plan, render_timeline
from loopitis.summary import clip_detail, project_summary


@tool
def open_project(path: str) -> str:
    """Load an Ableton Live Set (.als) and return a compact summary: tempo, key,
    and every session loop with its id, track, role hint, length and notes."""
    try:
        return project_summary(parse(_als_path(path)))
    except (OSError, ValueError) as e:
        return f"Could not open {path}: {e}"


@tool
def inspect_clips(path: str, clip_ids: list[str]) -> str:
    """Show the notes of specific session loops (positions as bar.beat.sixteenth),
    with chord names and density. Use it to judge a loop's musical character."""
    try:
        live_set = parse(_als_path(path))
    except (OSError, ValueError) as e:
        return f"Could not open {path}: {e}"
    details = []
    for clip_id in clip_ids:
        try:
            details.append(clip_detail(live_set, clip_id))
        except KeyError as e:
            details.append(f"## {clip_id}\n{e}")
    return "\n\n".join(details)


@tool
def preview_arrangement(path: str, plan: ArrangementPlan) -> str:
    """Check an arrangement plan without writing anything. Returns errors (which
    must be fixed), warnings (which deserve a reason), and the timeline as
    markdown with computed bar positions."""
    try:
        live_set = parse(_als_path(path))
    except (OSError, ValueError) as e:
        return f"Could not open {path}: {e}"
    check = check_plan(plan, live_set)
    return f"{check.report()}\n\n{render_timeline(plan, live_set)}"


@tool
def write_arrangement(path: str, plan: ArrangementPlan) -> str:
    """Write the plan into a NEW .als next to the original (the original is never
    changed). The user is asked to approve, edit or reject before this runs."""
    try:
        source = _als_path(path)
        output = write_set(source, plan)
    except PlanError as e:
        return f"Not written. Fix these problems and try again:\n{e}"
    except (OSError, ValueError) as e:
        return f"Not written: {e}"
    return f"Wrote {output}\n\n{render_timeline(plan, parse(source))}"


def _als_path(path: str) -> Path:
    resolved = Path(path.strip().strip("'\"")).expanduser()
    if resolved.suffix != ".als":
        raise ValueError("expected a path to an Ableton Live Set (.als)")
    return resolved
