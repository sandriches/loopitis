from loopitis.als.parse import load_xml, parse, parse_root
from loopitis.als.write import save, strip_arrangement, write_arrangement
from loopitis.evals.metrics import boundary_f1, entry_order, reference_from_set, score
from loopitis.plan import ArrangementPlan
from tests.live_set_builder import demo_set

PRODUCER_PLAN = ArrangementPlan(
    sections=[
        {"name": "Intro", "bars": 8, "clips": ["C1"]},
        {"name": "Build", "bars": 8, "clips": ["C1", "C2"]},
        {"name": "Drop", "bars": 16, "clips": ["C1", "C2", "C3", "C4"]},
        {"name": "Outro", "bars": 8, "clips": ["C3"]},
    ]
)


def make_case(tmp_path):
    """A finished set (arranged with PRODUCER_PLAN) and its loops-only copy."""
    finished = write_arrangement(demo_set(tmp_path / "demo.als"), PRODUCER_PLAN)
    reference = reference_from_set(parse(finished))
    root = load_xml(finished)
    strip_arrangement(root)
    loops_only = tmp_path / "loops.als"
    save(root, loops_only)
    return parse_root(load_xml(loops_only), loops_only), reference


def test_reference_reads_the_producers_arrangement(tmp_path):
    _, reference = make_case(tmp_path)

    assert reference.bars == 40
    assert min(reference.activity["Drums"]) == 0
    assert min(reference.activity["Sub"]) == 8
    assert "Riser" not in reference.activity  # never arranged


def test_matching_the_producer_scores_full_marks(tmp_path):
    live_set, reference = make_case(tmp_path)

    assert score(PRODUCER_PLAN, live_set, reference)["overall"] == 1.0


def test_a_flat_loop_scores_lower(tmp_path):
    live_set, reference = make_case(tmp_path)
    flat = ArrangementPlan(
        sections=[{"name": "All", "bars": 40, "clips": ["C1", "C2", "C3", "C4"]}]
    )

    scores = score(flat, live_set, reference)

    assert scores["valid"] == 1.0
    assert scores["energy_shape"] == 0.0  # constant density has no shape
    assert scores["overall"] < 0.6


def test_invalid_or_missing_plans_score_zero(tmp_path):
    live_set, reference = make_case(tmp_path)
    broken = ArrangementPlan(sections=[{"name": "A", "bars": 8, "clips": ["C99"]}])

    assert score(None, live_set, reference)["overall"] == 0.0
    assert score(broken, live_set, reference)["overall"] == 0.0


def test_entry_order_and_boundaries():
    a = {"drums": {0, 1, 2, 3}, "bass": {2, 3}, "pad": {3}}
    b = {"drums": {1, 2, 3}, "bass": {0, 1, 2, 3}, "pad": {3}}

    assert entry_order(a, a) == 1.0
    assert entry_order(a, b) == 2 / 3
    assert boundary_f1(a, 4, a, 4) == 1.0
