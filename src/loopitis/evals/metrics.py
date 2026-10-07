"""Compare a proposed arrangement with the one the producer actually made.

Both arrangements are reduced to the same shape: for each track that has
session loops, the set of bars in which it plays. Comparing tracks rather than
clip ids is deliberate: producers often place an edited copy of a loop rather
than the loop itself.

Matching the producer is a proxy for quality, not the definition of it. Many
arrangements are good; these metrics reward plans that share the producer's
broad decisions (what enters when, where the energy goes) without demanding
the same bar numbers.
"""

from dataclasses import asdict, dataclass

from loopitis.als.model import LiveSet
from loopitis.analysis import correlation
from loopitis.plan import ArrangementPlan, check_plan, layout

Activity = dict[str, set[int]]  # track name -> 0-based bars in which it plays


@dataclass
class Reference:
    name: str
    tempo: float
    bars: int
    activity: Activity

    def to_json(self) -> dict:
        return {**asdict(self), "activity": {k: sorted(v) for k, v in self.activity.items()}}

    @classmethod
    def from_json(cls, data: dict) -> "Reference":
        return cls(**{**data, "activity": {k: set(v) for k, v in data["activity"].items()}})


def eligible_tracks(live_set: LiveSet) -> set[int]:
    """Tracks with at least one usable session loop."""
    return {c.track_index for c in live_set.clips if c.kind == "audio" or c.notes}


def reference_from_set(live_set: LiveSet, min_share: float = 0.25) -> Reference:
    """The producer's arrangement, limited to tracks that have session loops.

    The song is taken to end at the last bar where at least `min_share` of
    those tracks play, which trims long tails such as a sustained pad left
    running for minutes after the track has ended.
    """
    bpb = live_set.beats_per_bar
    activity: Activity = {}
    for index in eligible_tracks(live_set):
        track = live_set.track(index)
        bars = set()
        for clip in track.arrangement_clips:
            bars.update(range(int(clip.start // bpb), int(-(-clip.end // bpb))))
        if bars:
            activity[track.name] = bars

    counts: dict[int, int] = {}
    for bars in activity.values():
        for bar in bars:
            counts[bar] = counts.get(bar, 0) + 1
    threshold = max(1, round(min_share * len(activity)))
    end = max((bar for bar, n in counts.items() if n >= threshold), default=-1) + 1
    activity = {name: {b for b in bars if b < end} for name, bars in activity.items()}
    return Reference(
        name=live_set.path.stem,
        tempo=live_set.tempo,
        bars=end,
        activity={k: v for k, v in activity.items() if v},
    )


def plan_activity(plan: ArrangementPlan, live_set: LiveSet) -> Activity:
    clips = {c.id: c for c in live_set.clips}
    activity: Activity = {}
    for section in layout(plan):
        bars = range(section.start_bar - 1, section.end_bar)
        for clip_id in section.clips:
            if clip_id in clips:
                name = live_set.track(clips[clip_id].track_index).name
                activity.setdefault(name, set()).update(bars)
    return activity


def score(
    plan: ArrangementPlan | None, live_set: LiveSet, reference: Reference
) -> dict[str, float]:
    """All metrics in 0..1 (higher is better), plus `overall`, their mean."""
    if plan is None or not check_plan(plan, live_set).ok:
        return {"valid": 0.0, "overall": 0.0}
    proposed = plan_activity(plan, live_set)
    bars = sum(s.bars for s in plan.sections)
    scores = {
        "valid": 1.0,
        "entry_order": entry_order(proposed, reference.activity),
        "energy_shape": energy_shape(proposed, bars, reference.activity, reference.bars),
        "boundaries": boundary_f1(proposed, bars, reference.activity, reference.bars),
        "track_choice": jaccard(set(proposed), set(reference.activity)),
        "length": min(bars, reference.bars) / max(bars, reference.bars, 1),
    }
    scores["overall"] = sum(scores.values()) / len(scores)
    return {k: round(v, 3) for k, v in scores.items()}


def entry_order(a: Activity, b: Activity) -> float:
    """Share of track pairs that enter in the same order in both arrangements.

    `a` is the proposal, `b` the reference. Entering together where the
    reference staggers earns nothing, so a plan can't score by bringing
    everything in at once; staggering a pair the reference brings in together
    earns half.
    """
    shared = sorted(set(a) & set(b))
    pairs = [(x, y) for i, x in enumerate(shared) for y in shared[i + 1 :]]
    if not pairs:
        return 0.0
    agree = 0.0
    for x, y in pairs:
        da = _sign(min(a[x]) - min(a[y]))
        db = _sign(min(b[x]) - min(b[y]))
        if da == db:
            agree += 1.0
        elif db == 0:
            agree += 0.5
    return agree / len(pairs)


def energy_shape(a: Activity, a_bars: int, b: Activity, b_bars: int, points: int = 32) -> float:
    """Correlation of the number of playing tracks over normalised time, clipped at 0."""
    curve_a = _density_curve(a, a_bars, points)
    curve_b = _density_curve(b, b_bars, points)
    return max(0.0, correlation(curve_a, curve_b))


def boundary_f1(
    a: Activity, a_bars: int, b: Activity, b_bars: int, tolerance: float = 0.04
) -> float:
    """F1 of section boundaries (where the set of playing tracks changes),
    positions normalised to 0..1, matched within `tolerance`."""
    found = [x / a_bars for x in _boundaries(a, a_bars)] if a_bars else []
    truth = [x / b_bars for x in _boundaries(b, b_bars)] if b_bars else []
    if not found or not truth:
        return 0.0
    unmatched = list(truth)
    hits = 0
    for position in found:
        nearest = min(unmatched, key=lambda t: abs(t - position), default=None)
        if nearest is not None and abs(nearest - position) <= tolerance:
            unmatched.remove(nearest)
            hits += 1
    precision, recall = hits / len(found), hits / len(truth)
    return 2 * precision * recall / (precision + recall) if hits else 0.0


def jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _boundaries(activity: Activity, bars: int) -> list[int]:
    playing = [frozenset(t for t, b in activity.items() if bar in b) for bar in range(bars)]
    return [bar for bar in range(1, bars) if playing[bar] != playing[bar - 1]]


def _density_curve(activity: Activity, bars: int, points: int) -> list[float]:
    if not bars:
        return [0.0] * points
    return [sum(1 for b in activity.values() if int(i * bars / points) in b) for i in range(points)]


def _sign(x: float) -> int:
    return (x > 0) - (x < 0)
