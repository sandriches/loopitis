"""Compact markdown views of a Live Set, sized for an LLM's context window."""

from loopitis.als.model import Clip, LiveSet
from loopitis.analysis import (
    clip_stats,
    describe_notes,
    detect_project_key,
    pitch_name,
    role_hint,
)


def project_summary(live_set: LiveSet) -> str:
    bpb = live_set.beats_per_bar
    num, den = live_set.time_signature
    key = detect_project_key(live_set)
    lines = [
        f"# {live_set.path.stem}",
        f"{live_set.live_version} · {live_set.tempo:.1f} BPM · {num}/{den}",
        f"Detected key: {key} (correlation {key.confidence})" if key else "Detected key: unknown",
        "",
        f"## Session loops ({len(live_set.clips)})",
        "Only these clips can be placed in the new arrangement.",
        "",
        "| id | track | role hint | bars | notes/bar | range | chords | devices |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for clip in live_set.clips:
        track = live_set.track(clip.track_index)
        lines.append(
            "| {id} | {track} | {role} | {bars} | {density} | {range} | {chords} | {devices} |".format(
                id=clip.id,
                track=track.name + (f" (in {track.group})" if track.group else ""),
                role=role_hint(clip, track, bpb),
                bars=_bars(clip.length, bpb) + ("" if clip.looping else ", one-shot"),
                **_note_columns(clip, bpb),
                devices=", ".join(track.devices) or "-",
            )
        )

    layers = _layer_groups(live_set)
    if layers:
        lines += ["", "## Layers (identical notes on different sounds)"]
        lines += [f"- {', '.join(group)}" for group in layers]

    scenes = _scenes(live_set)
    if len(scenes) > 1:
        lines += ["", "## Session rows (loops the producer grouped together)"]
        for slot, clips in scenes.items():
            name = live_set.scene_names[slot] if slot < len(live_set.scene_names) else ""
            label = f"Scene {slot + 1}" + (f" '{name}'" if name else "")
            lines.append(f"- {label}: {', '.join(c.id for c in clips)}")

    idle = [t.name for t in live_set.tracks if t.kind in ("midi", "audio") and not t.session_clips]
    if idle:
        lines += ["", f"Tracks with no session loops (they stay empty): {', '.join(idle)}"]

    existing = [c for t in live_set.tracks for c in t.arrangement_clips]
    if existing:
        end_bar = max(c.end for c in existing) / bpb
        lines += [
            "",
            (
                f"The set already has an arrangement ({len(existing)} clips, "
                f"about {end_bar:.0f} bars). The output file replaces it; "
                "the original file is never modified."
            ),
        ]
    return "\n".join(lines)


def clip_detail(live_set: LiveSet, clip_id: str) -> str:
    clip = live_set.clip(clip_id)
    track = live_set.track(clip.track_index)
    bpb = live_set.beats_per_bar
    header = (
        f"## {clip.id}: {track.name}" + (f" / {clip.name}" if clip.name else "") + "\n"
        f"{clip.kind} clip, {_bars(clip.length, bpb)}, "
        f"{'loops' if clip.looping else 'plays once'}; devices: {', '.join(track.devices) or 'none'}"
    )
    if clip.kind == "audio":
        return f"{header}\nSample: {clip.sample}\n(Audio content is not analysed.)"
    stats = clip_stats(clip, bpb)
    return "\n".join(
        [
            header,
            f"{stats.note_count} notes, {stats.notes_per_bar}/bar, max polyphony {stats.max_polyphony}"
            + (f", chords: {' → '.join(stats.chords)}" if stats.chords else ""),
            describe_notes(clip, track, bpb),
        ]
    )


def _note_columns(clip: Clip, bpb: float) -> dict[str, str]:
    if clip.kind == "audio":
        return {"density": "audio", "range": "-", "chords": "-"}
    stats = clip_stats(clip, bpb)
    if not stats.note_count:
        return {"density": "0", "range": "-", "chords": "-"}
    return {
        "density": str(stats.notes_per_bar),
        "range": f"{pitch_name(stats.lowest)}–{pitch_name(stats.highest)}",
        "chords": " → ".join(stats.chords[:6]) or "-",
    }


def _layer_groups(live_set: LiveSet) -> list[list[str]]:
    groups: dict[tuple, list[str]] = {}
    for clip in live_set.clips:
        if clip.notes:
            groups.setdefault((clip.length, tuple(clip.notes)), []).append(clip.id)
    return [ids for ids in groups.values() if len(ids) > 1]


def _scenes(live_set: LiveSet) -> dict[int, list[Clip]]:
    scenes: dict[int, list[Clip]] = {}
    for clip in live_set.clips:
        scenes.setdefault(clip.slot_index, []).append(clip)
    return dict(sorted(scenes.items()))


def _bars(beats: float, bpb: float) -> str:
    bars = beats / bpb
    return f"{bars:g} bar" + ("" if bars == 1 else "s")
