"""Build small synthetic .als files with the same structure Live 12 writes."""

import gzip
import xml.etree.ElementTree as ET
from pathlib import Path


def _v(parent: ET.Element, tag: str, value) -> ET.Element:
    return ET.SubElement(parent, tag, Value=str(value))


def midi_clip(
    notes: list[tuple[int, float, float]], length: float, looping: bool = True
) -> ET.Element:
    """notes: (pitch, start, duration) in beats."""
    clip = ET.Element("MidiClip", Id="0", Time="0")
    _v(clip, "CurrentStart", 0)
    _v(clip, "CurrentEnd", length)
    loop = ET.SubElement(clip, "Loop")
    for tag, value in (
        ("LoopStart", 0),
        ("LoopEnd", length),
        ("StartRelative", 0),
        ("LoopOn", str(looping).lower()),
        ("OutMarker", length),
    ):
        _v(loop, tag, value)
    _v(clip, "Name", "")
    _v(clip, "TakeId", 0)
    key_tracks = ET.SubElement(ET.SubElement(clip, "Notes"), "KeyTracks")
    by_pitch: dict[int, list] = {}
    for pitch, start, duration in notes:
        by_pitch.setdefault(pitch, []).append((start, duration))
    for i, (pitch, events) in enumerate(sorted(by_pitch.items())):
        key_track = ET.SubElement(key_tracks, "KeyTrack", Id=str(i))
        notes_el = ET.SubElement(key_track, "Notes")
        for start, duration in events:
            ET.SubElement(
                notes_el,
                "MidiNoteEvent",
                Time=str(start),
                Duration=str(duration),
                Velocity="100",
                NoteId="1",
            )
        _v(key_track, "MidiKey", pitch)
    return clip


def track(
    kind: str,
    name: str,
    track_id: int,
    clips: dict[int, ET.Element] | None = None,
    devices: list[str] = (),
    group_id: int = -1,
    drum_pads: dict[int, str] | None = None,
) -> ET.Element:
    el = ET.Element(kind, Id=str(track_id))
    _v(ET.SubElement(el, "Name"), "EffectiveName", name)
    _v(el, "TrackGroupId", group_id)
    ET.SubElement(el, "AutomationEnvelopes").append(ET.Element("Envelopes"))
    chain = ET.SubElement(el, "DeviceChain")
    device_list = ET.SubElement(ET.SubElement(chain, "DeviceChain"), "Devices")
    for device in devices:
        device_el = ET.SubElement(device_list, device, Id="0")
        if device == "DrumGroupDevice" and drum_pads:
            branches = ET.SubElement(device_el, "Branches")
            for note, pad in drum_pads.items():
                branch = ET.SubElement(branches, "DrumBranch")
                _v(ET.SubElement(branch, "Name"), "EffectiveName", pad)
                _v(ET.SubElement(branch, "BranchInfo"), "ReceivingNote", 128 - note)
    if kind in ("MidiTrack", "AudioTrack"):
        sequencer = ET.SubElement(chain, "MainSequencer")
        slots = ET.SubElement(sequencer, "ClipSlotList")
        for slot_index in range(4):
            slot = ET.SubElement(slots, "ClipSlot", Id=str(slot_index))
            value = ET.SubElement(ET.SubElement(slot, "ClipSlot"), "Value")
            if clips and slot_index in clips:
                value.append(clips[slot_index])
        timeable = "Sample" if kind == "AudioTrack" else "ClipTimeable"
        ET.SubElement(
            ET.SubElement(ET.SubElement(sequencer, timeable), "ArrangerAutomation"), "Events"
        )
    return el


def live_set(tracks: list[ET.Element], tempo: float = 170, time_signature: int = 201) -> ET.Element:
    root = ET.Element("Ableton", Creator="Ableton Live 12.4.3")
    ls = ET.SubElement(root, "LiveSet")
    tracks_el = ET.SubElement(ls, "Tracks")
    tracks_el.extend(tracks)
    mixer = ET.SubElement(ET.SubElement(ET.SubElement(ls, "MainTrack"), "DeviceChain"), "Mixer")
    _v(ET.SubElement(mixer, "Tempo"), "Manual", tempo)
    _v(ET.SubElement(mixer, "TimeSignature"), "Manual", time_signature)
    ET.SubElement(ET.SubElement(ls, "Locators"), "Locators")
    scenes = ET.SubElement(ls, "Scenes")
    for i in range(4):
        _v(ET.SubElement(scenes, "Scene", Id=str(i)), "Name", "")
    return root


def save(root: ET.Element, path: Path) -> Path:
    with gzip.open(path, "wb") as f:
        f.write(ET.tostring(root, encoding="utf-8", xml_declaration=True))
    return path


def demo_set(path: Path) -> Path:
    """Drums, bass, a chord loop with a layer, a one-shot and an empty clip.

    Clip ids in order: C1 drums, C2 bass, C3 chords, C4 chords layer,
    C5 empty, C6 one-shot fx.
    """
    drums = midi_clip([(36, 0, 0.25), (38, 2, 0.25), (36, 4, 0.25), (38, 6, 0.25)], 8)
    bass = midi_clip([(36, 0, 2), (43, 4, 2)], 8)
    chord_notes = [(60, 0, 8), (63, 0, 8), (67, 0, 8), (56, 8, 8), (60, 8, 8), (63, 8, 8)]
    return save(
        live_set(
            [
                track(
                    "MidiTrack",
                    "Drums",
                    1,
                    {0: drums},
                    ["DrumGroupDevice"],
                    drum_pads={36: "Kick 909", 38: "Snare 909"},
                ),
                track("MidiTrack", "Sub", 2, {0: bass}, ["Operator"]),
                track("GroupTrack", "Synths", 3),
                track("MidiTrack", "Chords A", 4, {1: midi_clip(chord_notes, 16)}, group_id=3),
                track("MidiTrack", "Chords B", 5, {1: midi_clip(chord_notes, 16)}, group_id=3),
                track("MidiTrack", "Empty", 6, {0: midi_clip([], 4)}),
                track("MidiTrack", "Riser", 7, {2: midi_clip([(72, 0, 16)], 16, looping=False)}),
            ]
        ),
        path,
    )
