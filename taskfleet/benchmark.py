"""Identical scenarios, deterministic strategies, paired perturbation controls and exports."""

import argparse
from collections import Counter, defaultdict
import csv
from copy import deepcopy
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from statistics import mean, stdev
from time import perf_counter

from .engine import Simulation
from .instances import from_dict, load_instance
from .metrics import metrics
from .models import Instance

STRATEGIES = ("fifo", "cpsat", "cnp")


def instance_digest(instance: Instance) -> str:
    canonical = json.dumps(instance.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def aggregate(runs: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for run in runs:
        groups[run["scenario"], run["strategy"]].append(run)
    result = []
    for (scenario, strategy), rows in sorted(groups.items()):
        successful = [r for r in rows if r["status"] != "failed"]
        values = {}
        if successful:
            for key, value in successful[0].items():
                if type(value) not in (int, float) and value is not None:
                    continue
                if key in {"repetition", "error"}:
                    continue
                samples = [r[key] for r in successful if r[key] is not None]
                values[key] = {"mean": mean(samples) if samples else None,
                               "stddev": stdev(samples) if len(samples) > 1 else 0.0,
                               "count": len(samples)}
        result.append({"scenario": scenario, "strategy": strategy, "runs": len(rows),
                       "failed_runs": len(rows) - len(successful),
                       "complete_runs": sum(r["status"] == "complete" for r in rows),
                       "metrics": values})
    return result


def run_benchmark(instances: dict[str, Instance], *, repeats: int = 1,
                  solver_budget: float = 1.0, controls: bool = True,
                  diagnostics: bool = False) -> dict:
    if type(repeats) is not int or repeats < 1:
        raise ValueError("repeats must be a positive integer")
    if not instances:
        raise ValueError("At least one benchmark instance is required")
    scenarios = dict(instances)
    pairs = []
    for name, instance in instances.items():
        data = instance.to_dict()
        arrivals = any(t["arrival_tick"] > 0 for t in data["tasks"])
        downtime = bool(data["unavailability"])
        if not controls or not (arrivals or downtime):
            continue
        static_name = name + ":static"
        if static_name in scenarios:
            raise ValueError(f"Duplicate scenario name {static_name!r}")
        static = deepcopy(data)
        static["unavailability"] = []
        for task in static["tasks"]:
            task["arrival_tick"] = 0
        scenarios[static_name] = from_dict(static)
        if arrivals and downtime:
            arrivals_name = name + ":arrivals"
            if arrivals_name in scenarios:
                raise ValueError(f"Duplicate scenario name {arrivals_name!r}")
            arrival_data = deepcopy(data)
            arrival_data["unavailability"] = []
            scenarios[arrivals_name] = from_dict(arrival_data)
            pairs.extend([(arrivals_name, static_name, "arrivals"), (name, arrivals_name, "unavailability")])
        else:
            pairs.append((name, static_name, "arrivals" if arrivals else "unavailability"))
    runs = []
    for name, instance in sorted(scenarios.items()):
        for strategy in STRATEGIES:
            for repetition in range(1, repeats + 1):
                row = {"scenario": name, "strategy": strategy, "repetition": repetition,
                       "instance_sha256": instance_digest(instance), "status": "failed", "error": None}
                try:
                    started = perf_counter()
                    simulation = Simulation(instance, strategy, solver_budget=solver_budget)
                    initialized = perf_counter()
                    simulation.run()
                    simulated = perf_counter()
                    simulation.check_invariants()
                    row.update(metrics(simulation))
                    row["status"] = "complete" if simulation.all_tasks_completed else "incomplete"
                    if diagnostics:
                        measured = perf_counter()
                        decisions = [e for e in simulation.events if e["kind"] == "allocation"]
                        messages = [e for e in simulation.events if e["kind"] == "message"]
                        row.update(
                            setup_seconds=initialized - started,
                            simulation_seconds=simulated - initialized,
                            metrics_seconds=measured - simulated,
                            total_seconds=measured - started,
                            allocation_rounds=len(decisions),
                            solver_calls=sum(len(e.get("phases", [])) for e in decisions),
                            solver_phase_statuses=dict(Counter(p for e in decisions for p in e.get("phases", []))),
                            allocation_statuses=dict(Counter(e["status"] for e in decisions)),
                            self_messages=sum(e["sender"] == e["recipient"] for e in messages),
                            direct_transmissions=sum(e["recipient"] != "all" and e["sender"] != e["recipient"]
                                                     for e in messages),
                            broadcasts=sum(e["recipient"] == "all" for e in messages),
                            trace_sha256=hashlib.sha256(json.dumps(simulation.trace(), sort_keys=True,
                                                                 separators=(",", ":")).encode()).hexdigest(),
                        )
                except (ValueError, RuntimeError, AssertionError, ImportError, OSError) as error:
                    row["error"] = f"{type(error).__name__}: {error}"
                runs.append(row)
    indexed = {(r["scenario"], r["strategy"], r["repetition"]): r for r in runs}
    impacts = []
    for scenario, control, perturbation in pairs:
        for strategy in STRATEGIES:
            for repetition in range(1, repeats + 1):
                actual = indexed[scenario, strategy, repetition]
                baseline = indexed[control, strategy, repetition]
                delta = {}
                for key in ("tasks_completed", "makespan", "distance", "total_tardiness", "messages"):
                    delta[key] = (actual[key] - baseline[key]
                                  if actual.get(key) is not None and baseline.get(key) is not None else None)
                impacts.append({"scenario": scenario, "control": control, "perturbation": perturbation,
                                "strategy": strategy, "repetition": repetition, "delta": delta})
    return {"protocol": {"strategies": list(STRATEGIES), "repeats": repeats,
                         "solver_budget_per_phase": solver_budget,
                         "ortools_version": version("ortools"),
                         "solver_budget_unit": "OR-Tools deterministic time", "randomness": False},
            "scenarios": {name: instance.to_dict() for name, instance in sorted(scenarios.items())},
            "runs": runs, "aggregates": aggregate(runs), "perturbation_impacts": impacts}


def export_results(report: dict, *, json_path: Path | None = None, csv_path: Path | None = None) -> None:
    if json_path is not None:
        Path(json_path).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if csv_path is not None:
        fields = list(dict.fromkeys(key for row in report["runs"] for key in row))
        with Path(csv_path).open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            for row in report["runs"]:
                writer.writerow({key: json.dumps(value, sort_keys=True) if isinstance(value, dict) else value
                                 for key, value in row.items()})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instances", nargs="*", type=Path)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--solver-budget", type=float, default=1.0)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--csv", type=Path)
    parser.add_argument("--no-controls", action="store_true")
    args = parser.parse_args(argv)
    paths = args.instances or [Path(__file__).resolve().parents[1] / "examples" / f"{name}.json"
                               for name in ("simple", "allocation", "contention", "dynamic")]
    try:
        if len({p.stem for p in paths}) != len(paths):
            raise ValueError("Instance filenames must have distinct stems")
        report = run_benchmark({p.stem: load_instance(p) for p in paths}, repeats=args.repeats,
                               solver_budget=args.solver_budget, controls=not args.no_controls)
        export_results(report, json_path=args.json, csv_path=args.csv)
        for row in report["aggregates"]:
            values = row["metrics"]
            print(f"{row['scenario']} / {row['strategy']}: "
                  f"completed={values.get('tasks_completed', {}).get('mean')} "
                  f"makespan={values.get('makespan', {}).get('mean')} "
                  f"distance={values.get('distance', {}).get('mean')} "
                  f"messages={values.get('messages', {}).get('mean')} failed={row['failed_runs']}")
        for run in report["runs"]:
            if run["status"] != "complete":
                print(f"{run['scenario']} / {run['strategy']} / {run['repetition']}: {run['status']} {run['error'] or ''}")
        return int(any(r["status"] == "failed" for r in report["runs"]))
    except (ValueError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
