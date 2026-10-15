"""Reproduce paired M8 snapshot comparisons, including known DSA failure cases."""

import argparse
from collections import Counter, defaultdict
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
from statistics import mean

from taskfleet.allocation import Observation
from taskfleet.dcop import compare_snapshot, distributed
from taskfleet.engine import Simulation
from taskfleet.instances import from_dict, generate_instance
from taskfleet.ollama import OllamaClient, OllamaConfig


def scenarios(seeds):
    result = {}
    for seed in seeds:
        for count in (2, 4, 8):
            base = generate_instance(width=6, height=5, robot_count=count,
                                     task_count=count + count // 2, seed=seed, horizon=120).to_dict()
            for compatibility in ("dense", "sparse"):
                data = json.loads(json.dumps(base))
                for index, robot in enumerate(data["robots"]):
                    robot["capacity"] = 3 if compatibility == "dense" or index == 0 else 1
                for index, task in enumerate(data["tasks"]):
                    task["load"] = 1 if compatibility == "dense" or index % 3 == 0 else 3
                result[f"n{count}_{compatibility}_seed{seed}"] = from_dict(data)
    result["symmetric_oscillation"] = from_dict({
        "width": 1, "height": 1, "horizon": 10,
        "robots": [{"id": "r1", "position": [0, 0], "capacity": 1},
                   {"id": "r2", "position": [0, 0], "capacity": 1}],
        "tasks": [{"id": "a", "pickup": [0, 0], "destination": [0, 0]},
                  {"id": "b", "pickup": [0, 0], "destination": [0, 0]}]})
    result["capacity_trap"] = from_dict({
        "width": 1, "height": 1, "horizon": 10,
        "robots": [{"id": "r1", "position": [0, 0], "capacity": 2},
                   {"id": "r2", "position": [0, 0], "capacity": 1}],
        "tasks": [{"id": "a", "pickup": [0, 0], "destination": [0, 0]},
                  {"id": "b", "pickup": [0, 0], "destination": [0, 0], "load": 2}]})
    return result


def record(name: str, method: str, result: dict, observation: Observation,
           reference: dict, **parameters) -> dict:
    quality = result.get("quality", result.get("final_quality"))
    raw = result.get("raw_quality", quality)
    proven = reference["optimal"]
    cardinality = quality["cardinality"]
    reference_cardinality = len(reference["assignments"])
    return {"scenario": name, "method": method, **parameters,
            "robots": len(observation.robots), "tasks": len(observation.tasks),
            "cardinality": cardinality, "estimated_cost": quality["estimated_cost"],
            "feasible": quality["feasible"], "raw_cardinality": raw["cardinality"],
            "raw_feasible": raw["feasible"], "raw_conflict_pairs": raw["conflict_pairs"],
            "reference_proven": proven, "reference_cardinality": reference_cardinality,
            "reference_method": "exhaustive" if "states" in reference else "cpsat",
            "method_optimality_proven": result["optimal"],
            "reference_cost": reference["estimated_cost"],
            "cardinality_gap": reference_cardinality - cardinality if proven else None,
            "cost_gap_at_optimal_cardinality": quality["estimated_cost"] - reference["estimated_cost"]
            if proven and cardinality == reference_cardinality else None,
            "logical_messages": result.get("logical_messages", 0),
            "transmissions": result.get("transmissions", 0), "rounds": result.get("rounds", 0),
            "confirmation_transmissions": result.get("confirmation_transmissions", 0),
            "confirmation_abandonments": result.get("confirmation_abandonments", 0),
            "oscillations": result.get("oscillations", 0),
            "stop_reason": result.get("stop_reason", result.get("status")),
            "total_seconds": result["total_seconds"], "assignments": result["assignments"],
            "message_sha256": hashlib.sha256(json.dumps(result.get("messages", []), sort_keys=True).encode()).hexdigest(),
            **({"llm_metrics": result["llm_metrics"], "llm_trace": result["llm_trace"]}
               if "llm_metrics" in result else {})}


def summarize(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["method"]].append(row)
    return [{"method": method, "runs": len(group),
             "feasible_runs": sum(row["feasible"] for row in group),
             "proven_reference_runs": sum(row["reference_proven"] for row in group),
             "own_proof_runs": sum(row["method_optimality_proven"] for row in group),
             "optimal_cardinality_runs": sum(row["cardinality_gap"] == 0 for row in group),
             "optimal_objective_runs": sum(row["cardinality_gap"] == 0
                                            and row["cost_gap_at_optimal_cardinality"] == 0 for row in group),
             "raw_conflicting_runs": sum(not row["raw_feasible"] for row in group),
             "mean_cardinality_gap": mean(row["cardinality_gap"] for row in group
                                          if row["cardinality_gap"] is not None),
             "mean_transmissions": mean(row["transmissions"] for row in group),
             "mean_seconds": mean(row["total_seconds"] for row in group),
             "stop_reasons": dict(Counter(row["stop_reason"] for row in group))}
            for method, group in sorted(grouped.items())]


def run_experiments(*, seeds=(11, 23, 37), dsa_seeds=(0, 1, 2), rounds=(0, 1, 5, 20),
                    probabilities=(0.3, 0.7, 1.0), message_budget=100_000,
                    communication_budgets=(0, 32, 128), enumeration_budget=10_000,
                    solver_budget=1.0, ollama_config=None):
    instances = scenarios(seeds)
    rows = []
    graphs = {}
    for name, instance in instances.items():
        observation = Simulation(instance).observation()
        comparison = compare_snapshot(observation, rounds=0, enumeration_budget=enumeration_budget,
                                      solver_budget=solver_budget, message_budget=message_budget)
        reference = comparison["methods"]["exhaustive"]
        if not reference["optimal"]:
            reference = comparison["methods"]["cpsat"]
        for method in ("fifo", "cpsat", "cnp", "exhaustive"):
            rows.append(record(name, method, comparison["methods"][method], observation, reference))
        sample = comparison["methods"]["dsa"]
        pairs = sum(len(domain) - 1 for domain in sample["domains"].values())
        graphs[name] = {"compatibility_pairs": pairs,
                        "compatibility_density": pairs / (len(observation.robots) * len(observation.tasks)),
                        "directed_edges": sum(map(len, sample["neighbors"].values()))}
        for count in rounds:
            for probability in probabilities:
                for seed in dsa_seeds:
                    result = distributed(observation, rounds=count, probability=probability,
                                         seed=seed, message_budget=message_budget)
                    rows.append(record(name, "dsa", result, observation, reference,
                                       experiment="parameter_sweep", message_budget=message_budget,
                                       round_budget=count, probability=probability, dsa_seed=seed))
        for budget in communication_budgets:
            result = distributed(observation, rounds=max(rounds), probability=0.7,
                                 seed=dsa_seeds[0], message_budget=budget)
            rows.append(record(name, "dsa", result, observation, reference,
                               experiment="communication_budget", message_budget=budget,
                               round_budget=max(rounds), probability=0.7, dsa_seed=dsa_seeds[0]))
        if ollama_config is not None:
            result = distributed(observation, rounds=min(2, max(rounds)), seed=dsa_seeds[0],
                                 message_budget=message_budget, llm=OllamaClient(ollama_config))
            rows.append(record(name, "llm", result, observation, reference))
    return {"milestone": "M8", "scope": "frozen allocation snapshots; no physical collisions",
            "parameters": {"instance_seeds": list(seeds), "dsa_seeds": list(dsa_seeds),
                           "rounds": list(rounds), "probabilities": list(probabilities),
                           "message_budget": message_budget, "enumeration_budget": enumeration_budget,
                           "communication_budgets": list(communication_budgets),
                           "solver_budget_deterministic_per_phase": solver_budget},
            "environment": {"python": platform.python_version(), "ortools": version("ortools")},
            "instances": {name: instance.to_dict() for name, instance in instances.items()},
            "graphs": graphs, "runs": rows, "summary": summarize(rows),
            "limitations": ["DSA-B is stochastic and synchronous; no optimality or convergence guarantee.",
                            "Confirmation abandons losing claims; lexicographic ID can bias winners.",
                            "Exhaustive budgets may truncate; gaps use a proven CP-SAT reference in that case.",
                            "Baselines have global state; DSA and LLM have own state plus public messages.",
                            "DSA parameter sweeps repeat baselines' snapshots; method run counts differ.",
                            "Network is simulated; timing includes local costs, messages, confirmation and checking."]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[11, 23, 37])
    parser.add_argument("--dsa-seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--rounds", type=int, nargs="+", default=[0, 1, 5, 20])
    parser.add_argument("--probabilities", type=float, nargs="+", default=[0.3, 0.7, 1.0])
    parser.add_argument("--message-budget", type=int, default=100_000)
    parser.add_argument("--communication-budgets", type=int, nargs="+", default=[0, 32, 128])
    parser.add_argument("--enumeration-budget", type=int, default=10_000)
    parser.add_argument("--solver-budget", type=float, default=1.0)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        result = run_experiments(seeds=arguments.seeds, dsa_seeds=arguments.dsa_seeds,
                                 rounds=arguments.rounds, probabilities=arguments.probabilities,
                                 message_budget=arguments.message_budget,
                                 communication_budgets=arguments.communication_budgets,
                                 enumeration_budget=arguments.enumeration_budget,
                                 solver_budget=arguments.solver_budget)
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"scenarios": len(result["instances"]), "runs": len(result["runs"]),
                          "summary": result["summary"]}, indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
