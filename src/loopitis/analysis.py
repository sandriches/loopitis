"""Deterministic music analysis: everything the LLM shouldn't have to count."""

import re
from collections import Counter
from dataclasses import dataclass

from loopitis.als.model import Clip, LiveSet, Note, Track

PITCH_CLASSES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]

# Krumhansl-Kessler key profiles.
MAJOR_PROFILE = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
MINOR_PROFILE = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

CHORD_TEMPLATES = {
    "": {0, 4, 7},
    "m": {0, 3, 7},
    "dim": {0, 3, 6},
    "aug": {0, 4, 8},
    "sus2": {0, 2, 7},
    "sus4": {0, 5, 7},
    "7": {0, 4, 7, 10},
    "maj7": {0, 4, 7, 11},
    "m7": {0, 3, 7, 10},
    "m9": {0, 2, 3, 7, 10},
    "(5th)": {0, 7},
}

# General MIDI drum names; Drum Rack pads use the same note numbers by default.
DRUM_NAMES = {
    35: "kick",
    36: "kick",
    37: "rim",
    38: "snare",
    39: "clap",
    40: "snare",
    41: "low tom",
    42: "closed hat",
    43: "low tom",
    44: "pedal hat",
    45: "mid tom",
    46: "open hat",
    47: "mid tom",
    48: "high tom",
    49: "crash",
    50: "high tom",
    51: "ride",
    52: "china",
    53: "ride bell",
    54: "tambourine",
    55: "splash",
    56: "cowbell",
    57: "crash",
    59: "ride",
}

ROLE_NAME_HINTS = [
    ("drums", r"drum|kit|perc|beat|break|hat|kick|snare"),
    ("bass", r"bass|sub|808|reese|wob"),
    ("vocals", r"vox|vocal|voice|choir"),
    ("chords", r"chord|pad|keys|piano|synth"),
    ("lead", r"lead|arp|melody|pluck|bell"),
    ("texture", r"atmos|fx|noise|ambi|drone|texture|riser|sweep"),
]


def pitch_name(pitch: int) -> str:
    return f"{PITCH_CLASSES[pitch % 12]}{pitch // 12 - 2}"  # Live's octave numbering: 60 = C3


@dataclass(frozen=True)
class Key:
    tonic: int
    mode: str  # "major" | "minor"
    confidence: float  # Pearson correlation with the best profile, -1..1

    def __str__(self) -> str:
        return f"{PITCH_CLASSES[self.tonic]} {self.mode}"


def detect_key(notes: list[Note]) -> Key | None:
    """Krumhansl-Schmuckler key finding over a duration-weighted pitch histogram."""
    histogram = [0.0] * 12
    for note in notes:
        histogram[note.pitch % 12] += note.duration
    if sum(histogram) == 0:
        return None
    best = None
    for tonic in range(12):
        rotated = histogram[tonic:] + histogram[:tonic]
        for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            score = correlation(rotated, profile)
            if best is None or score > best.confidence:
                best = Key(tonic, mode, round(score, 2))
    return best


def detect_project_key(live_set: LiveSet) -> Key | None:
    notes = [
        note
        for track in live_set.tracks
        if not is_drum_track(track)
        for clip in track.session_clips
        for note in clip.notes
    ]
    return detect_key(notes)


def chord_name(pitches: set[int]) -> str | None:
    """Name the chord formed by a set of MIDI pitches, if it matches a template."""
    classes = {p % 12 for p in pitches}
    if len(classes) < 2:
        return None
    bass = min(pitches) % 12
    for root in [bass] + sorted(classes - {bass}):
        intervals = {(pc - root) % 12 for pc in classes}
        for suffix, template in CHORD_TEMPLATES.items():
            if intervals == template:
                name = PITCH_CLASSES[root] + suffix
                return name if root == bass else f"{name}/{PITCH_CLASSES[bass]}"
    return None


@dataclass(frozen=True)
class ClipStats:
    note_count: int
    notes_per_bar: float
    lowest: int | None
    highest: int | None
    mean_pitch: float | None
    max_polyphony: int
    chords: list[str]  # chord names in order of onset, consecutive repeats removed


def clip_stats(clip: Clip, beats_per_bar: float) -> ClipStats:
    notes = clip.notes
    bars = max(clip.length / beats_per_bar, 1e-9)
    pitches = [n.pitch for n in notes]
    return ClipStats(
        note_count=len(notes),
        notes_per_bar=round(len(notes) / bars, 1),
        lowest=min(pitches, default=None),
        highest=max(pitches, default=None),
        mean_pitch=round(sum(pitches) / len(pitches), 1) if pitches else None,
        max_polyphony=_max_polyphony(notes),
        chords=_chord_sequence(notes),
    )


def is_drum_track(track: Track) -> bool:
    return "Drum Rack" in track.devices or _name_role(track.name) == "drums"


def role_hint(clip: Clip, track: Track, beats_per_bar: float) -> str:
    """A best guess at the clip's musical role. The agent may overrule it."""
    if clip.kind == "midi" and not clip.notes:
        return "empty"
    if is_drum_track(track):
        return "drums"
    named = _name_role(f"{track.name} {track.group or ''} {clip.name}")
    if named:
        return named
    if clip.kind == "audio":
        return _name_role(clip.sample or "") or "audio"
    stats = clip_stats(clip, beats_per_bar)
    if stats.max_polyphony >= 3:
        return "chords"
    if stats.mean_pitch < 50:
        return "bass"
    if stats.notes_per_bar >= 4:
        return "lead"
    return "texture"


def describe_notes(clip: Clip, track: Track, beats_per_bar: float, limit: int = 96) -> str:
    """Notes grouped by bar, positions as bar.beat.sixteenth."""
    if not clip.notes:
        return "(no notes)"
    lines = []
    by_bar: dict[int, list[str]] = {}
    for note in clip.notes[:limit]:
        bar = int(note.start // beats_per_bar)
        label = _note_label(note.pitch, track)
        by_bar.setdefault(bar, []).append(
            f"{_position(note.start, beats_per_bar)} {label} {_duration(note.duration)} v{note.velocity:.0f}"
        )
    for bar, entries in sorted(by_bar.items()):
        lines.append(f"bar {bar + 1}: " + ", ".join(entries))
    if len(clip.notes) > limit:
        lines.append(f"... {len(clip.notes) - limit} more notes not shown")
    return "\n".join(lines)


def _note_label(pitch: int, track: Track) -> str:
    if track.drum_pads.get(pitch):
        return track.drum_pads[pitch]
    if is_drum_track(track):
        return DRUM_NAMES.get(pitch, pitch_name(pitch))
    return pitch_name(pitch)


def _name_role(text: str) -> str | None:
    text = text.lower()
    for role, pattern in ROLE_NAME_HINTS:
        if re.search(pattern, text):
            return role
    return None


def _max_polyphony(notes: list[Note]) -> int:
    events = sorted(
        [(n.start, 1) for n in notes] + [(n.start + n.duration, -1) for n in notes],
        key=lambda e: (e[0], e[1]),  # note-offs before note-ons at the same time
    )
    current = peak = 0
    for _, change in events:
        current += change
        peak = max(peak, current)
    return peak


def _chord_sequence(notes: list[Note]) -> list[str]:
    onsets = Counter(round(n.start, 3) for n in notes)
    sequence: list[str] = []
    for onset in sorted(t for t, count in onsets.items() if count >= 2):
        sounding = {n.pitch for n in notes if n.start <= onset < n.start + n.duration}
        name = chord_name(sounding)
        if name and (not sequence or sequence[-1] != name):
            sequence.append(name)
    return sequence


def _position(beats: float, beats_per_bar: float) -> str:
    bar, in_bar = divmod(beats, beats_per_bar)
    beat, in_beat = divmod(in_bar, 1)
    return f"{int(bar) + 1}.{int(beat) + 1}.{round(in_beat * 4) + 1}"


def _duration(beats: float) -> str:
    names = {4: "1", 2: "1/2", 1: "1/4", 0.5: "1/8", 0.25: "1/16"}
    return names.get(beats, f"{beats:.2f}".rstrip("0").rstrip(".") + "b")


def correlation(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    return cov / (var_x * var_y) ** 0.5 if var_x and var_y else 0.0
