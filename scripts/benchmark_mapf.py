"""Reproduce isolated M9 comparisons, finite-horizon certificates and failures.

python -m scripts.benchmark_mapf --output docs/results/v2/m9.json
"""

import argparse
from collections import Counter
from itertools import permutations
import json
from pathlib import Path
import platform
import random
from statistics import mean, median
from time import perf_counter

from taskfleet.mapf import from_dict, individual_bound, solve, validate_plan
from taskfleet.paths import shortest_path


def scenarios(seeds=(11, 23, 37)) -> list[dict]:
    def case(name, width, height, agents, horizon=6, obstacles=()):
        return {"name": name, "kind": "micro", "seed": None,
                "problem": {"width": width, "height": height, "horizon": horizon,
                            "obstacles": [list(p) for p in obstacles],
                            "agents": [{"id": f"r{i}", "start": list(s), "goal": list(g)}
                                       for i, (s, g) in enumerate(agents)]}}
    cases = [
        case("crossing_wait", 3, 3, [((0, 1), (2, 1)), ((1, 0), (1, 2))]),
        case("edge_swap_detour", 2, 2, [((0, 0), (1, 0)), ((1, 0), (0, 0))]),
        case("blocking_goal", 3, 2, [((1, 1), (1, 0)), ((0, 0), (2, 0))],
             obstacles=[(0, 1), (2, 1)]),
        case("narrow_pockets", 5, 3, [((0, 1), (4, 1)), ((4, 1), (0, 1))], horizon=12,
             obstacles=[(x, y) for x in range(5) for y in (0, 2) if x != 2]),
        case("start_at_goal", 3, 2, [((1, 0), (1, 0)), ((0, 0), (2, 0))]),
        case("impossible_corridor", 3, 1, [((0, 0), (2, 0)), ((2, 0), (0, 0))], horizon=4),
        case("horizon_short", 3, 3, [((0, 1), (2, 1)), ((1, 0), (1, 2))], horizon=2),
        case("disconnected", 3, 1, [((0, 0), (2, 0))], obstacles=[(1, 0)]),
        case("zero_horizon", 1, 1, [((0, 0), (0, 0))], horizon=0),
    ]
    for seed in seeds:
        for name, width, height, n_agents in (("open4", 6, 5, 4), ("bottleneck6", 7, 5, 6),
                                              ("crowded8", 5, 4, 8)):
            rng = random.Random(seed)
            obstacles = [(width // 2, y) for y in range(height) if y != height // 2] if name == "bottleneck6" else []
            free = [(x, y) for x in range(width) for y in range(height) if (x, y) not in obstacles]
            starts, goals = rng.sample(free, n_agents), rng.sample(free, n_agents)
            entry = case(f"{name}_seed{seed}", width, height, list(zip(starts, goals)), horizon=24,
                         obstacles=obstacles)
            entry.update(kind=name, seed=seed)
            cases.append(entry)
    return cases


def exhaustive_certificates(max_nodes=100_000, seconds=5.0) -> dict:
    """All 144 ordered distinct start/goal pairs for two agents on the 2x2 grid."""
    cells = [(0, 0), (1, 0), (0, 1), (1, 1)]
    records, mismatches, unresolved = [], [], []
    for starts in permutations(cells, 2):
        for goals in permutations(cells, 2):
            p = from_dict({"width": 2, "height": 2, "horizon": 5, "agents": [
                {"id": a, "start": list(s), "goal": list(g)}
                for a, s, g in zip(("a", "b"), starts, goals)]})
            cbs = solve(p, "cbs", max_nodes=max_nodes, seconds=seconds)
            oracle = solve(p, "oracle", max_nodes=max_nodes, seconds=seconds)
            record = {"problem": p.to_dict(), "cbs": cbs, "oracle": oracle}
            records.append(record)
            if "BUDGET_EXHAUSTED" in (cbs["status"], oracle["status"]):
                unresolved.append(len(records) - 1)
            elif (cbs["status"] != oracle["status"] or (cbs["status"] == "SOLVED" and
                  cbs["validation"]["metrics"]["sum_of_costs"] != oracle["validation"]["metrics"]["sum_of_costs"])):
                mismatches.append(len(records) - 1)
    return {"cases": len(records), "certified": len(records) - len(unresolved) - len(mismatches),
            "mismatches": mismatches, "unresolved": unresolved, "runs": records}


def benchmark(*, seeds=(11, 23, 37), budgets=(1, 100, 20_000), seconds=2.0) -> dict:
    started, runs, inputs = perf_counter(), [], []
    for scenario in scenarios(seeds):
        setup = perf_counter()
        problem = from_dict(scenario["problem"])
        bound = individual_bound(problem)
        bfs_paths = {}
        for agent in sorted(problem.starts):
            path = shortest_path(problem.grid, problem.starts[agent], problem.goals[agent])
            if path is not None:
                bfs_paths[agent] = [problem.starts[agent], *path]
        setup_seconds = perf_counter() - setup
        inputs.append(scenario | {"individual_bound": bound, "bfs_plan_validation": validate_plan(problem, bfs_paths),
                                  "setup_and_bound_seconds": setup_seconds})
        agents = sorted(problem.starts)
        by_distance = sorted(agents, key=lambda a: (bound["distances"][a] if bound["distances"][a] is not None else float("inf"), a))
        methods = [("prioritized_id", "prioritized", agents),
                   ("prioritized_reverse", "prioritized", agents[::-1]),
                   ("prioritized_shortest", "prioritized", by_distance),
                   ("prioritized_longest", "prioritized", by_distance[::-1]),
                   ("cbs", "cbs", None)]
        if scenario["kind"] == "micro":
            methods.append(("oracle", "oracle", None))
        for node_budget in budgets:
            for name, algorithm, priorities in methods:
                began = perf_counter()
                result = solve(problem, algorithm, priorities=priorities, max_nodes=node_budget, seconds=seconds)
                runs.append({"scenario": scenario["name"], "kind": scenario["kind"], "seed": scenario["seed"],
                             "method": name, "node_budget": node_budget,
                             "full_method_seconds": perf_counter() - began, **result})
    aggregates = []
    for budget in budgets:
        for method in sorted({r["method"] for r in runs}):
            rows = [r for r in runs if r["method"] == method and r["node_budget"] == budget]
            successful = [r for r in rows if r["status"] == "SOLVED"]
            conditional = [r["validation"]["metrics"] for r in successful]
            aggregates.append({"method": method, "node_budget": budget, "runs": len(rows),
                               "solved": len(successful), "success_rate": len(successful) / len(rows),
                               "statuses": dict(Counter(r["status"] for r in rows)),
                               "conditional_cost_count": len(conditional),
                               **{f"mean_{m}": mean(r[m] for r in conditional) if conditional else None
                                  for m in ("sum_of_costs", "makespan", "distance", "waits")},
                               "median_full_method_seconds": median(r["full_method_seconds"] for r in rows),
                               "total_nodes": sum(r["total_nodes"] for r in rows)})
    highest = max(budgets)
    priority_sensitivity = []
    for scenario in inputs:
        rows = [r for r in runs if r["scenario"] == scenario["name"] and r["node_budget"] == highest
                and r["method"].startswith("prioritized_")]
        costs = [r["validation"]["metrics"]["sum_of_costs"] for r in rows if r["status"] == "SOLVED"]
        priority_sensitivity.append({"scenario": scenario["name"], "node_budget": highest,
                                     "outcomes": {r["method"]: {"status": r["status"], "priorities": r["priorities"],
                                                               "sum_of_costs": r["validation"]["metrics"]["sum_of_costs"]
                                                               if r["validation"] else None} for r in rows},
                                     "solved_orders": len(costs), "best_cost": min(costs) if costs else None,
                                     "worst_cost": max(costs) if costs else None})
    certificates = exhaustive_certificates(seconds=seconds)
    paired = []
    for scenario in inputs:
        rows = {r["method"]: r for r in runs if r["scenario"] == scenario["name"] and r["node_budget"] == highest}
        oracle = rows.get("oracle")
        if oracle and oracle["optimal"]:
            optimum = oracle["validation"]["metrics"]["sum_of_costs"]
            paired.append({"scenario": scenario["name"], "oracle_optimum": optimum,
                           "gaps": {name: r["validation"]["metrics"]["sum_of_costs"] - optimum
                                    if r["validation"] else None for name, r in rows.items() if name != "oracle"}})
    return {"milestone": "M9", "python": platform.python_version(), "seeds": list(seeds),
            "node_budgets": list(budgets), "seconds_per_search": seconds,
            "conventions": {"physics": "isolated MAPF, no warehouse collisions added",
                            "moves": "cardinal or wait; vertex and opposite-edge conflicts forbidden",
                            "goals": "absorbing on first visit, then persistent; distinct starts and goals within each set",
                            "objective": "sum first-arrival ticks; all waits before arrival included",
                            "guarantees": "CBS/oracle SOLVED optimal only within stated horizon and absorbing-goal model; budget stop unknown",
                            "priority_failure": "not an infeasibility proof",
                            "oracle_limits": "4 agents, 16 free cells, horizon <=16",
                            "timing": "full_method_seconds includes input validation, complete search and independent plan checking; setup/BFS separate",
                            "nodes": "all expanded low-level, CBS high-level and joint states combined",
                            "conditioning": "cost means include solved runs only; compare paired cases, not marginal means with differing completion"},
            "source": "https://ojs.aaai.org/index.php/AAAI/article/view/8140",
            "scenario_count": len(inputs), "run_count": len(runs), "scenarios": inputs,
            "aggregates": aggregates, "priority_sensitivity": priority_sensitivity,
            "paired_oracle_gaps": paired, "exhaustive_2x2": certificates,
            "invalid_solutions": sum(r["validation"] is not None and not r["validation"]["valid"] for r in runs),
            "runs": runs, "total_seconds": perf_counter() - started}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("docs/results/v2/m9.json"))
    parser.add_argument("--seeds", type=int, nargs="+", default=[11, 23, 37])
    parser.add_argument("--budgets", type=int, nargs="+", default=[1, 100, 20_000])
    parser.add_argument("--seconds", type=float, default=2.0)
    args = parser.parse_args(argv)
    if any(b < 0 for b in args.budgets) or any(s < 0 for s in args.seeds):
        parser.error("seeds and budgets must be nonnegative")
    try:
        result = benchmark(seeds=tuple(args.seeds), budgets=tuple(args.budgets), seconds=args.seconds)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except (ValueError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")
    print(json.dumps({"scenarios": result["scenario_count"], "runs": result["run_count"],
                      "invalid_solutions": result["invalid_solutions"],
                      "exhaustive_certified": result["exhaustive_2x2"]["certified"],
                      "seconds": result["total_seconds"], "output": str(args.output)}, indent=2))
    return int(bool(result["exhaustive_2x2"]["mismatches"] or result["exhaustive_2x2"]["unresolved"]))


if __name__ == "__main__":
    raise SystemExit(main())
