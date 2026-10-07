"""Plain data model for the parts of a Live Set the agent reasons about.

Times are in beats (quarter notes), as stored in the .als file. Bars are
derived from the set's time signature only at the edges (summaries, plans).
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

TrackKind = Literal["midi", "audio", "group", "return"]
ClipKind = Literal["midi", "audio"]


@dataclass(frozen=True)
class Note:
    pitch: int
    start: float  # beats from the start of the clip's loop region
    duration: float
    velocity: float


@dataclass
class Clip:
    """A clip in a session slot: one of the producer's loop ideas."""

    id: str  # stable short id, e.g. "C3"
    track_index: int
    slot_index: int
    name: str
    kind: ClipKind
    length: float  # beats the clip plays before it repeats (or ends)
    looping: bool
    notes: list[Note] = field(default_factory=list)
    sample: str | None = None  # audio clips: sample path relative to the project


@dataclass(frozen=True)
class ArrangementClip:
    track_index: int
    start: float
    end: float
    name: str


@dataclass(frozen=True)
class Locator:
    name: str
    time: float


@dataclass
class Track:
    index: int  # position in the set's track list (return tracks included)
    live_id: str  # the track's Id attribute, referenced by grouped tracks
    name: str
    kind: TrackKind
    group: str | None  # name of the containing group track
    devices: list[str]
    drum_pads: dict[int, str] = field(default_factory=dict)  # MIDI note -> Drum Rack pad name
    session_clips: list[Clip] = field(default_factory=list)
    arrangement_clips: list[ArrangementClip] = field(default_factory=list)


@dataclass
class LiveSet:
    path: Path
    live_version: str
    tempo: float
    time_signature: tuple[int, int]
    tracks: list[Track]
    locators: list[Locator]
    scene_names: list[str]

    @property
    def beats_per_bar(self) -> float:
        numerator, denominator = self.time_signature
        return numerator * 4 / denominator

    @property
    def clips(self) -> list[Clip]:
        return [clip for track in self.tracks for clip in track.session_clips]

    def clip(self, clip_id: str) -> Clip:
        for clip in self.clips:
            if clip.id == clip_id:
                return clip
        raise KeyError(f"No session clip with id {clip_id!r}")

    def track(self, index: int) -> Track:
        return self.tracks[index]
