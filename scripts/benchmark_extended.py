"""Controlled V1 experiments using the M5 benchmark and the existing generator.

Run from the repository root: python -m scripts.benchmark_extended --output results
"""

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import platform
from statistics import median
import sys

from taskfleet import Simulation, from_dict, load_instance
from taskfleet.benchmark import STRATEGIES, aggregate, export_results, instance_digest, run_benchmark
from taskfleet.instances import generate_instance


SEEDS = (11, 23, 37, 51, 79)
CONFIGURATIONS = {
    "small": {"robots": 2, "tasks": 4, "width": 6, "height": 4, "horizon": 120},
    "base": {},
    "tasks24": {"tasks": 24},
    "tasks48": {"tasks": 48},
    "robots2": {"robots": 2},
    "robots8": {"robots": 8},
    "large_grid": {"width": 20, "height": 16},
    "obstacles": {"obstacles": True},
    "heterogeneous": {"tasks": 24, "heterogeneous": True},
    "shared_free": {"tasks": 24, "shared": True, "station_capacity": 0},
    "shared1": {"tasks": 24, "shared": True, "station_capacity": 1},
    "shared2": {"tasks": 24, "shared": True, "station_capacity": 2},
    "arrivals": {"arrivals": True},
    "downtime": {"downtime": True},
    "dynamic": {"arrivals": True, "downtime": True},
    "short": {"horizon": 20},
    "late": {"late": True},
    "empty": {"robots": 0, "tasks": 0, "horizon": 0},
    "low_budget": {"solver_budget": 1e-8},
}
TIMING_FIELDS = {"strategy_seconds", "setup_seconds", "simulation_seconds", "metrics_seconds", "total_seconds"}
FACTORS = {"robots": 4, "tasks": 12, "width": 10, "height": 8, "horizon": 220,
           "obstacles": False, "heterogeneous": False, "shared": False,
           "station_capacity": 0, "arrivals": False, "downtime": False, "late": False}
METRICS = ("tasks_completed", "completion_rate", "makespan", "total_tardiness", "distance",
           "mean_robot_utilization", "waiting_robot_ticks", "strategy_seconds", "simulation_seconds",
           "total_seconds", "messages", "direct_transmissions", "self_messages", "broadcasts", "solver_calls")


def make_instance(configuration: str, seed: int):
    parameters = FACTORS | CONFIGURATIONS[configuration]
    width, height = parameters["width"], parameters["height"]
    # Generate on a connected grid avoiding wall cells, then remove the wall in controls.
    # All factor controls retain identical task coordinates and robot prefixes.
    wall = [[width // 2, y] for y in range(height) if y != height // 2]
    generated = generate_instance(width=width, height=height, robot_count=8, task_count=48,
                                  seed=seed, horizon=parameters["horizon"], obstacles=wall)
    data = generated.to_dict()
    data["obstacles"] = wall if parameters["obstacles"] else []
    data["robots"] = data["robots"][:parameters["robots"]]
    data["tasks"] = data["tasks"][:parameters["tasks"]]
    for index, robot in enumerate(data["robots"]):
        if not parameters["heterogeneous"] or index == 0:
            robot["capacity"] = 3
    stations = [[0, 0], [width - 1, height - 1]]
    for index, task in enumerate(data["tasks"]):
        if not parameters["heterogeneous"]:
            task["load"] = 1
        task["handling_ticks"] = 3 if parameters["shared"] else 2
        task["deadline"] = 30 + 3 * index
        if parameters["shared"]:
            task["pickup"] = stations[index % 2]
            task["destination"] = stations[1 - index % 2]
        if parameters["arrivals"]:
            task["arrival_tick"] = 15 * (index // 3)
        if parameters["late"]:
            task["arrival_tick"] = parameters["horizon"] + 1
    if parameters["station_capacity"]:
        data["resources"] = [{"id": f"s{index}", "position": position,
                              "capacity": parameters["station_capacity"]}
                             for index, position in enumerate(stations)]
    if parameters["downtime"]:
        data["unavailability"] = [{"robot_id": robot["id"], "start_tick": 8 + 3 * index,
                                   "end_tick": 20 + 3 * index}
                                  for index, robot in enumerate(data["robots"])]
    return from_dict(data), parameters


def summarize(runs: list[dict]) -> list[dict]:
    """Aggregate seeds, averaging timing repetitions within each instance first."""
    per_instance = aggregate(runs)
    rows = []
    for result in per_instance:
        original = next(r for r in runs if r["scenario"] == result["scenario"] and r["strategy"] == result["strategy"])
        rows.append({"scenario": original["configuration"], "strategy": result["strategy"],
                     "repetition": 1, "error": None,
                     "status": "failed" if not result["metrics"] else "complete" if result["complete_runs"] == result["runs"] else "incomplete",
                     **{key: result["metrics"].get(key, {}).get("mean") for key in METRICS}})
    summaries = aggregate(rows)
    for summary in summaries:
        selected = [r for r in rows if r["scenario"] == summary["scenario"] and r["strategy"] == summary["strategy"] and r["status"] != "failed"]
        for key, statistics in summary["metrics"].items():
            values = [r[key] for r in selected if r[key] is not None]
            statistics.update(median=median(values) if values else None,
                              min=min(values) if values else None, max=max(values) if values else None)
        raw = [r for r in runs if r["configuration"] == summary["scenario"] and r["strategy"] == summary["strategy"]]
        summary.update(simulations=len(raw), failed_simulations=sum(r["status"] == "failed" for r in raw),
                       incomplete_simulations=sum(r["status"] == "incomplete" for r in raw))
    return summaries


def paired_differences(runs: list[dict]) -> list[dict]:
    indexed = {(r["scenario"], r["strategy"], r["repetition"]): r for r in runs}
    pairs = []
    controls = {"obstacles": "base", "heterogeneous": "tasks24", "shared1": "shared_free",
                "shared2": "shared_free", "arrivals": "base", "downtime": "base", "dynamic": "arrivals",
                "robots2": "base", "robots8": "base", "large_grid": "base", "short": "base"}
    for row in runs:
        comparisons = []
        if row["strategy"] != "fifo":
            comparisons.append((row["scenario"], "fifo", "strategy"))
        control = controls.get(row["configuration"])
        if control:
            comparisons.append((f"{control}/seed-{row['seed']}", row["strategy"], "factor"))
        for scenario, strategy, kind in comparisons:
            baseline = indexed.get((scenario, strategy, row["repetition"]))
            if baseline is None:
                continue
            pairs.append({"scenario": row["scenario"], "strategy": row["strategy"], "repetition": row["repetition"],
                          "control": scenario, "control_strategy": strategy, "kind": kind,
                          "delta": {key: row[key] - baseline[key]
                                    if row.get(key) is not None and baseline.get(key) is not None else None
                                    for key in METRICS}})
    return pairs


def deterministic_runs(report: dict) -> list[dict]:
    return [{key: value for key, value in row.items() if key not in TIMING_FIELDS} for row in report["runs"]]


def run_experiments(seeds=SEEDS, *, repeats=2, solver_budget=1.0, configurations=None,
                    include_reference=True) -> dict:
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("Provide distinct seeds")
    # Warm every allocator and import OR-Tools before any recorded simulation.
    warmup = load_instance(Path(__file__).resolve().parents[1] / "examples/simple.json")
    for strategy in STRATEGIES:
        Simulation(warmup, strategy).run()
    names = list(CONFIGURATIONS) if configurations is None else configurations
    instances, parameters = {}, {}
    for name in names:
        for seed in seeds[:1] if name == "empty" else seeds:
            instance, factors = make_instance(name, seed)
            scenario = f"{name}/seed-{seed}"
            instances[scenario] = instance
            parameters[scenario] = {"configuration": name, "seed": seed, "factors": factors}
    if include_reference:
        scenario = "complex_comparison"
        instances[scenario] = load_instance(Path(__file__).resolve().parents[1] / "examples/complex_comparison.json")
        parameters[scenario] = {"configuration": scenario, "seed": None, "factors": {"reference": True}}
    runs = []
    for name, instance in instances.items():
        budget = parameters[name]["factors"].get("solver_budget", solver_budget)
        report = run_benchmark({name: instance}, repeats=repeats, solver_budget=budget,
                               controls=False, diagnostics=True)
        for row in report["runs"]:
            row.update(configuration=parameters[name]["configuration"], seed=parameters[name]["seed"],
                       solver_budget_per_phase=budget)
        runs.extend(report["runs"])
        print(f"{name}: {dict(Counter(r['status'] for r in report['runs']))}", flush=True)
    mismatches = []
    grouped = defaultdict(list)
    for row in deterministic_runs({"runs": runs}):
        grouped[row["scenario"], row["strategy"]].append({k: v for k, v in row.items() if k != "repetition"})
    for key, rows in grouped.items():
        if any(row != rows[0] for row in rows[1:]):
            mismatches.append(list(key))
    return {"protocol": {"seeds": list(seeds), "repeats": repeats, "strategies": list(STRATEGIES),
                          "solver_budget_per_phase": solver_budget, "stress_budget": 1e-8,
                          "solver_workers": 1, "solver_seed": 0, "warmup": "simple, all strategies, excluded",
                          "budget_unit": "OR-Tools deterministic time per phase", "generator_draws": [8, 48],
                          "seed_aggregation": "mean repetitions per instance, then aggregate independent seeds",
                          "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                          "python": sys.version.split()[0], "platform": platform.platform(),
                          "processor": platform.processor(), "ortools": version("ortools")},
            "parameters": parameters,
            "scenarios": {name: instance.to_dict() for name, instance in instances.items()},
            "distinct_instances": len({instance_digest(instance) for instance in instances.values()}),
            "runs": runs, "aggregates": aggregate(runs), "summaries": summarize(runs),
            "paired_differences": paired_differences(runs), "reproducibility_mismatches": mismatches}


def plots(report: dict, output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.size": 9, "svg.hashsalt": "taskfleet-v1"})
    indexed = {(s["scenario"], s["strategy"]): s for s in report["summaries"]}
    colors = {"fifo": "#64748b", "cpsat": "#2563eb", "cnp": "#d97706"}
    labels_by_strategy = {"fifo": "FIFO", "cpsat": "CP-SAT", "cnp": "CNP"}

    def panel(axis, names, labels, key, title, unit, strategies=STRATEGIES):
        for strategy in strategies:
            statistics = [indexed.get((name, strategy), {}).get("metrics", {}).get(key, {}) for name in names]
            values = [s.get("mean") for s in statistics]
            if not any(v is not None for v in values):
                continue
            axis.errorbar(range(len(names)), [float("nan") if v is None else v for v in values],
                          yerr=[s.get("stddev", 0) for s in statistics], marker="o", capsize=3,
                          color=colors[strategy], label=labels_by_strategy[strategy])
            if key == "makespan":
                for index, (name, statistics) in enumerate(zip(names, statistics)):
                    total = indexed.get((name, strategy), {}).get("runs", 0)
                    if statistics.get("count", 0) < total and statistics.get("mean") is not None:
                        axis.annotate(f"n={statistics['count']}/{total}", (index, statistics["mean"]),
                                      xytext=(5, 8), textcoords="offset points", color=colors[strategy])
        axis.set(xticks=range(len(names)), xticklabels=labels, title=title, ylabel=unit)
        axis.grid(alpha=.2)
        axis.legend(fontsize=8)

    groups = [
        ("scaling", [
            (["base", "tasks24", "tasks48"], ["12", "24", "48"], "makespan", "Nombre de tâches (4 robots)", "ticks"),
            (["robots2", "base", "robots8"], ["2", "4", "8"], "makespan", "Nombre de robots (12 tâches)", "ticks"),
            (["base", "tasks24", "tasks48"], ["3", "6", "12"], "distance", "Charge tâches / robot (4 robots)", "cases"),
            (["base", "large_grid"], ["10 × 8", "20 × 16"], "makespan", "Taille de l'entrepôt", "ticks")]),
        ("factors", [
            (["base", "obstacles"], ["sans mur", "avec mur"], "makespan", "Obstacles, mêmes coordonnées", "ticks"),
            (["shared_free", "shared1", "shared2"], ["sans station", "capacité 1", "capacité 2"], "makespan", "Ressources, mêmes trajets", "ticks"),
            (["base", "arrivals", "downtime", "dynamic"], ["statique", "arrivées", "suspensions", "les deux"], "makespan", "Perturbations appariées", "ticks"),
            (["base", "short", "late", "low_budget"], ["base", "horizon 20", "hors horizon", "budget 1e-8"], "completion_rate", "Cas incomplets et budget limité", "ratio")]),
        ("costs", [
            (["base", "tasks24", "tasks48"], ["12", "24", "48"], "strategy_seconds", "Temps de décision / simulation", "secondes"),
            (["base", "tasks24", "tasks48"], ["12", "24", "48"], "total_seconds", "Temps total / simulation", "secondes"),
            (["robots2", "base", "robots8"], ["2", "4", "8"], "messages", "Messages CNP / nombre de robots", "messages logiques", ("cnp",)),
            (["base", "tasks24", "tasks48"], ["12", "24", "48"], "messages", "Messages CNP / nombre de tâches", "messages logiques", ("cnp",))]),
    ]
    for name, panels in groups:
        figure, axes = plt.subplots(2, 2, figsize=(12, 8), layout="constrained")
        for axis, arguments in zip(axes.flat, panels):
            panel(axis, *arguments)
        figure.suptitle(f"TaskFleet V1 — {len(report['protocol']['seeds'])} graines ; moyenne ± écart-type\n"
                        "Makespan : runs complets seulement ; répétitions moyennées par instance", fontsize=11)
        figure.savefig(output / f"{name}.png", dpi=160)
        figure.savefig(output / f"{name}.svg", metadata={"Date": None})
        plt.close(figure)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(SEEDS))
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--solver-budget", type=float, default=1.0)
    parser.add_argument("--configurations", nargs="+", choices=CONFIGURATIONS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--plots", action="store_true")
    parser.add_argument("--compare", type=Path, help="Compare deterministic runs against an earlier report")
    args = parser.parse_args(argv)
    report = run_experiments(args.seeds, repeats=args.repeats, solver_budget=args.solver_budget,
                             configurations=args.configurations)
    args.output.mkdir(parents=True, exist_ok=True)
    export_results(report, json_path=args.output / "report.json", csv_path=args.output / "runs.csv")
    summaries = [{"configuration": s["scenario"], "strategy": s["strategy"], "instances": s["runs"],
                  "complete_instances": s["complete_runs"], "failed_simulations": s["failed_simulations"],
                  "incomplete_simulations": s["incomplete_simulations"],
                  **{f"{key}_{statistic}": value for key, values in s["metrics"].items()
                     for statistic, value in values.items()}} for s in report["summaries"]]
    export_results({"runs": summaries}, csv_path=args.output / "summary.csv")
    failed = any(r["status"] == "failed" for r in report["runs"]) or bool(report["reproducibility_mismatches"])
    if args.compare:
        previous = json.loads(args.compare.read_text(encoding="utf-8"))
        matches = deterministic_runs(previous) == deterministic_runs(report) and previous["scenarios"] == report["scenarios"]
        print(f"Deterministic comparison: {matches}")
        failed |= not matches
    if args.plots:
        plots(report, args.output)
    print(f"Distinct instances: {report['distinct_instances']}; simulations: {len(report['runs'])}; "
          f"statuses: {dict(Counter(r['status'] for r in report['runs']))}; "
          f"reproducibility mismatches: {len(report['reproducibility_mismatches'])}")
    for row in report["runs"]:
        if row["status"] == "failed":
            print(f"FAILED {row['scenario']} / {row['strategy']}: {row['error']}", file=sys.stderr)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
