"""Read an Ableton Live Set (.als: gzipped XML) into the plain data model."""

import gzip
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from loopitis.als.model import (
    ArrangementClip,
    Clip,
    LiveSet,
    Locator,
    Note,
    Track,
    TrackKind,
)

TRACK_KINDS: dict[str, TrackKind] = {
    "MidiTrack": "midi",
    "AudioTrack": "audio",
    "GroupTrack": "group",
    "ReturnTrack": "return",
}

# Internal device tags mapped to the names Live shows in its browser.
DEVICE_NAMES = {
    "OriginalSimpler": "Simpler",
    "MultiSampler": "Sampler",
    "Compressor2": "Compressor",
    "Eq8": "EQ Eight",
    "AutoFilter2": "Auto Filter",
    "DrumGroupDevice": "Drum Rack",
    "InstrumentGroupDevice": "Instrument Rack",
    "AudioEffectGroupDevice": "Audio Effect Rack",
    "MidiEffectGroupDevice": "MIDI Effect Rack",
    "MxDeviceInstrument": "Max Instrument",
    "MxDeviceAudioEffect": "Max Audio Effect",
    "MxDeviceMidiEffect": "Max MIDI Effect",
    "StereoGain": "Utility",
    "InstrumentVector": "Wavetable",
}
PLUGIN_TAGS = {"PluginDevice", "AuPluginDevice"}


def load_xml(path: Path) -> ET.Element:
    """Return the root <Ableton> element of a .als file."""
    with gzip.open(path) as f:
        return ET.parse(f).getroot()


def parse(path: str | Path) -> LiveSet:
    """Parse a .als file, cached until the file changes on disk."""
    path = Path(path).expanduser().resolve()
    return _parse_cached(path, path.stat().st_mtime_ns)


@lru_cache(maxsize=8)
def _parse_cached(path: Path, _mtime_ns: int) -> LiveSet:
    return parse_root(load_xml(path), path)


def parse_root(root: ET.Element, path: Path) -> LiveSet:
    live_set = root.find("LiveSet")
    main = live_set.find("MainTrack")
    if main is None:  # Live 11 and earlier
        main = live_set.find("MasterTrack")

    track_elements = list(live_set.find("Tracks"))
    names_by_live_id = {el.get("Id"): _track_name(el) for el in track_elements}

    tracks = []
    clip_number = 0
    for index, el in enumerate(track_elements):
        group_id = _value(el, "TrackGroupId", "-1")
        track = Track(
            index=index,
            live_id=el.get("Id"),
            name=_track_name(el),
            kind=TRACK_KINDS[el.tag],
            group=names_by_live_id.get(group_id),
            devices=_devices(el),
            drum_pads=_drum_pads(el),
        )
        for slot_index, clip_el in _session_clip_elements(el):
            clip_number += 1
            track.session_clips.append(_session_clip(clip_el, f"C{clip_number}", index, slot_index))
        track.arrangement_clips = [
            ArrangementClip(
                track_index=index,
                start=float(_value(c, "CurrentStart")),
                end=float(_value(c, "CurrentEnd")),
                name=_value(c, "Name", ""),
            )
            for c in arrangement_clip_elements(el)
        ]
        tracks.append(track)

    return LiveSet(
        path=path,
        live_version=root.get("Creator", "unknown"),
        tempo=float(_value(main, "DeviceChain/Mixer/Tempo/Manual", "120")),
        time_signature=decode_time_signature(
            int(_value(main, "DeviceChain/Mixer/TimeSignature/Manual", "201"))
        ),
        tracks=tracks,
        locators=[
            Locator(name=_value(loc, "Name", ""), time=float(_value(loc, "Time")))
            for loc in live_set.findall("Locators/Locators/Locator")
        ],
        scene_names=[_value(s, "Name", "") for s in live_set.findall("Scenes/Scene")],
    )


def decode_time_signature(encoded: int) -> tuple[int, int]:
    """Live stores a time signature as numerator-1 + 99 * log2(denominator)."""
    return encoded % 99 + 1, 2 ** (encoded // 99)


def main_sequencer(track_el: ET.Element) -> ET.Element | None:
    return track_el.find("DeviceChain/MainSequencer")


def arrangement_events(track_el: ET.Element) -> ET.Element | None:
    """The <Events> list holding a track's arrangement clips, if it has one."""
    sequencer = main_sequencer(track_el)
    if sequencer is None:
        return None
    timeable = "Sample" if track_el.tag == "AudioTrack" else "ClipTimeable"
    return sequencer.find(f"{timeable}/ArrangerAutomation/Events")


def arrangement_clip_elements(track_el: ET.Element) -> list[ET.Element]:
    events = arrangement_events(track_el)
    return list(events) if events is not None else []


def session_clip_element(track_el: ET.Element, slot_index: int) -> ET.Element:
    for index, clip_el in _session_clip_elements(track_el):
        if index == slot_index:
            return clip_el
    raise KeyError(f"No clip in slot {slot_index}")


def _session_clip_elements(track_el: ET.Element):
    sequencer = main_sequencer(track_el)
    if sequencer is None:
        return
    for slot_index, slot in enumerate(sequencer.findall("ClipSlotList/ClipSlot")):
        value = slot.find("ClipSlot/Value")
        if value is not None and len(value):
            yield slot_index, value[0]


def _session_clip(el: ET.Element, clip_id: str, track_index: int, slot_index: int) -> Clip:
    loop = el.find("Loop")
    loop_start = float(_value(loop, "LoopStart"))
    looping = _value(loop, "LoopOn") == "true"
    if looping:
        start, length = loop_start, float(_value(loop, "LoopEnd")) - loop_start
    else:
        start = loop_start + float(_value(loop, "StartRelative", "0"))
        length = float(_value(loop, "OutMarker")) - start

    clip = Clip(
        id=clip_id,
        track_index=track_index,
        slot_index=slot_index,
        name=_value(el, "Name", ""),
        kind="midi" if el.tag == "MidiClip" else "audio",
        length=length,
        looping=looping,
    )
    if clip.kind == "midi":
        clip.notes = _notes(el, start, start + length)
    else:
        clip.sample = _value(el, "SampleRef/FileRef/RelativePath", None)
    return clip


def _notes(clip_el: ET.Element, start: float, end: float) -> list[Note]:
    notes = []
    for key_track in clip_el.findall("Notes/KeyTracks/KeyTrack"):
        pitch = int(_value(key_track, "MidiKey"))
        for event in key_track.findall("Notes/MidiNoteEvent"):
            time = float(event.get("Time"))
            if event.get("IsEnabled", "true") == "false" or not start <= time < end:
                continue
            notes.append(
                Note(
                    pitch=pitch,
                    start=time - start,
                    duration=float(event.get("Duration")),
                    velocity=float(event.get("Velocity")),
                )
            )
    return sorted(notes, key=lambda n: (n.start, n.pitch))


def _devices(track_el: ET.Element) -> list[str]:
    devices = track_el.find("DeviceChain/DeviceChain/Devices")
    return [_device_name(d) for d in devices] if devices is not None else []


def _drum_pads(track_el: ET.Element) -> dict[int, str]:
    """Pad names of the track's first Drum Rack, keyed by the MIDI note that plays them."""
    rack = track_el.find("DeviceChain/DeviceChain/Devices/DrumGroupDevice")
    if rack is None:
        return {}
    pads = {}
    for branch in rack.findall("Branches/DrumBranch"):
        receiving = branch.find("BranchInfo/ReceivingNote")
        if receiving is not None:  # stored as 128 - note
            pads[128 - int(receiving.get("Value"))] = _value(branch, "Name/EffectiveName", "")
    return pads


def _device_name(device_el: ET.Element) -> str:
    if device_el.tag in PLUGIN_TAGS:
        desc = device_el.find("PluginDesc")
        for el in desc.iter() if desc is not None else ():
            if el.tag in ("PlugName", "Name") and el.get("Value"):
                return el.get("Value")
        return "Plugin"
    return DEVICE_NAMES.get(device_el.tag, device_el.tag)


def _track_name(track_el: ET.Element) -> str:
    return _value(track_el, "Name/EffectiveName", "")


def _value(el: ET.Element, path: str, default: str | None = ...) -> str | None:
    found = el.find(path)
    if found is None or found.get("Value") is None:
        if default is ...:
            raise ValueError(f"Missing {path!r} under <{el.tag}>")
        return default
    return found.get("Value")
