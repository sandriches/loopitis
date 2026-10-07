"""Build eval cases from finished Live Sets and score the agent against them.

    loopitis-eval build ~/beats            # finished sets -> loops-only copies + references
    loopitis-eval run --limit 3            # run locally, print scores
    loopitis-eval run --langsmith          # run as a LangSmith experiment

Each case is a copy of a finished set with its arrangement removed (the input)
and the producer's real arrangement (the reference). The agent runs until it
asks to write its plan; that plan is scored against the reference.
"""

import argparse
import json
import re
import statistics
import time
import uuid
from pathlib import Path

from loopitis.als.parse import load_xml, parse, parse_root
from loopitis.als.write import save, strip_arrangement
from loopitis.evals.metrics import Reference, reference_from_set, score
from loopitis.plan import ArrangementPlan

DEFAULT_CASES = Path("evals/cases")
DEFAULT_RESULTS = Path("evals/results")
EVAL_REQUEST = (
    "Arrange the loops in {path} into a full track. I'm not available to answer "
    "questions, so make your best call on anything unclear."
)


def build_cases(
    sources: list[Path], out: Path, min_tracks: int, max_bars: int, include_live: bool
) -> list[dict]:
    files = _find_sets(sources, include_live)
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for file in files:
        try:
            root = load_xml(file)
            live_set = parse_root(root, file)
        except Exception as e:  # noqa: BLE001 - one unreadable set shouldn't stop the build
            print(f"skip {file.name}: {e}")
            continue
        reference = reference_from_set(live_set)
        if len(reference.activity) < min_tracks or not 32 <= reference.bars <= max_bars:
            continue
        case_dir = out / _slug(file.stem)
        case_dir.mkdir(exist_ok=True)
        strip_arrangement(root)
        save(root, case_dir / file.name)
        (case_dir / "reference.json").write_text(json.dumps(reference.to_json()))
        manifest.append({"name": file.stem, "als_path": str((case_dir / file.name).resolve())})
        print(f"case {file.stem}: {len(reference.activity)} tracks, {reference.bars} bars")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def plan_for(als_path: str) -> dict:
    """Run the agent until it asks to write its plan; return that plan."""
    from langgraph.checkpoint.memory import InMemorySaver

    from loopitis.agent.graph import build_agent

    agent = build_agent(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": 120}
    state = agent.invoke(
        {"messages": [{"role": "user", "content": EVAL_REQUEST.format(path=als_path)}]}, config
    )
    last_message = state["messages"][-1].text if state["messages"] else ""
    for interrupt in state.get("__interrupt__", []):
        for request in interrupt.value.get("action_requests", []):
            if request["name"] == "write_arrangement":
                return {"plan": request["args"]["plan"], "message": last_message}
    return {"plan": None, "message": last_message}


def score_case(als_path: str, plan: dict | None, reference: dict) -> dict[str, float]:
    parsed = ArrangementPlan.model_validate(plan) if plan else None
    return score(parsed, parse(als_path), Reference.from_json(reference))


def run_local(cases: list[dict], repetitions: int, results_dir: Path) -> None:
    rows = []
    for case in cases:
        for repetition in range(repetitions):
            started = time.time()
            output = plan_for(case["als_path"])
            scores = score_case(case["als_path"], output["plan"], case["reference"])
            rows.append(
                {"case": case["name"], "repetition": repetition, **output, "scores": scores}
            )
            print(f"{case['name']:<40} {_format_scores(scores)}  ({time.time() - started:.0f}s)")

    keys = sorted({k for row in rows for k in row["scores"]})
    print(
        "\nmean "
        + "  ".join(
            f"{k}={statistics.mean(r['scores'].get(k, 0.0) for r in rows):.3f}" for k in keys
        )
    )
    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(rows, indent=2))
    print(f"results: {path}")


def run_baselines(cases: list[dict]) -> None:
    from loopitis.evals.baselines import BASELINES

    for name, make_plan in BASELINES.items():
        rows = []
        for case in cases:
            live_set = parse(case["als_path"])
            rows.append(
                score(make_plan(live_set), live_set, Reference.from_json(case["reference"]))
            )
        keys = rows[0].keys()
        print(
            f"{name:<8} "
            + "  ".join(f"{k}={statistics.mean(r[k] for r in rows):.3f}" for k in keys)
        )


def run_langsmith(cases: list[dict], repetitions: int, dataset_name: str) -> None:
    from langsmith import Client, evaluate

    client = Client()
    if client.has_dataset(dataset_name=dataset_name):
        dataset = client.read_dataset(dataset_name=dataset_name)
    else:
        dataset = client.create_dataset(
            dataset_name, description="Loops-only Live Sets with the producer's real arrangement."
        )
    existing = {e.inputs["als_path"] for e in client.list_examples(dataset_id=dataset.id)}
    new = [c for c in cases if c["als_path"] not in existing]
    if new:
        client.create_examples(
            dataset_id=dataset.id,
            examples=[
                {"inputs": {"als_path": c["als_path"]}, "outputs": {"reference": c["reference"]}}
                for c in new
            ],
        )

    def target(inputs: dict) -> dict:
        return plan_for(inputs["als_path"])

    def arrangement_scores(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
        scores = score_case(inputs["als_path"], outputs.get("plan"), reference_outputs["reference"])
        return {"results": [{"key": k, "score": v} for k, v in scores.items()]}

    from loopitis.agent.graph import build_model

    evaluate(
        target,
        data=dataset_name,
        evaluators=[arrangement_scores],
        experiment_prefix=build_model().model,
        num_repetitions=repetitions,
        max_concurrency=2,
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="loopitis-eval", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="make eval cases from finished Live Sets")
    build.add_argument("sources", nargs="+", type=Path, help=".als files or folders to search")
    build.add_argument("--out", type=Path, default=DEFAULT_CASES)
    build.add_argument(
        "--min-tracks",
        type=int,
        default=3,
        help="minimum arranged tracks that also have session loops",
    )
    build.add_argument("--max-bars", type=int, default=400)
    build.add_argument(
        "--include-live",
        action="store_true",
        help="keep sets with 'live' in the name (usually performance sets)",
    )

    baseline = commands.add_parser("baseline", help="score the non-LLM baselines")
    baseline.add_argument("--cases", type=Path, default=DEFAULT_CASES)

    run = commands.add_parser("run", help="run the agent on the cases and score it")
    run.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    run.add_argument("--limit", type=int)
    run.add_argument("--only", help="regex on case names")
    run.add_argument("--repetitions", type=int, default=1)
    run.add_argument("--langsmith", action="store_true", help="run as a LangSmith experiment")
    run.add_argument("--dataset", default="loopitis")
    run.add_argument("--results", type=Path, default=DEFAULT_RESULTS)

    args = parser.parse_args()
    if args.command == "build":
        cases = build_cases(
            args.sources, args.out, args.min_tracks, args.max_bars, args.include_live
        )
        print(f"{len(cases)} cases written to {args.out}")
        return

    cases = load_cases(args.cases)
    if args.command == "baseline":
        run_baselines(cases)
        return
    if args.only:
        cases = [c for c in cases if re.search(args.only, c["name"], re.IGNORECASE)]
    cases = cases[: args.limit] if args.limit else cases
    if args.langsmith:
        run_langsmith(cases, args.repetitions, args.dataset)
    else:
        run_local(cases, args.repetitions, args.results)


def load_cases(cases_dir: Path) -> list[dict]:
    manifest = json.loads((cases_dir / "manifest.json").read_text())
    for case in manifest:
        case["reference"] = json.loads(
            (Path(case["als_path"]).parent / "reference.json").read_text()
        )
    return manifest


def _find_sets(sources: list[Path], include_live: bool) -> list[Path]:
    """Every .als under the sources, skipping Live's backups and duplicate copies."""
    found: dict[str, Path] = {}
    for source in sources:
        source = source.expanduser()
        candidates = [source] if source.suffix == ".als" else source.rglob("*.als")
        for path in candidates:
            if "Backup" in path.parts or (
                not include_live and re.search(r"\blive\b", path.stem, re.IGNORECASE)
            ):
                continue
            # Nested "X Project/X Project/X.als" copies: keep the shallowest.
            if path.stem not in found or len(path.parts) < len(found[path.stem].parts):
                found[path.stem] = path
    return sorted(found.values())


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _format_scores(scores: dict[str, float]) -> str:
    return "  ".join(f"{k}={v:.2f}" for k, v in scores.items())


if __name__ == "__main__":
    main()
