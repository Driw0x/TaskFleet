"""Reproducible M10 train/validation/test and held-out generalization experiment."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
from statistics import mean, stdev
from time import perf_counter

from taskfleet import Simulation
from taskfleet.benchmark import instance_digest
from taskfleet.instances import generate_instance
from taskfleet.marl import QPolicy, run_episode, train
from taskfleet.metrics import metrics


def make_instance(seed, *, unseen=False):
    instance = generate_instance(width=5 if unseen else 4, height=3, robot_count=2,
                                 task_count=4 if unseen else 3, seed=seed, horizon=40)
    # Deadlines are public and fixed by index, not selected using test outcomes.
    for index, task in enumerate(instance.tasks.values()):
        task.deadline = 10 + 5 * index
    return instance


def benchmark(*, episodes=60, seeds=(0, 1, 2), output=None, llm_client=None):
    splits = {"train": [make_instance(s) for s in (11, 23, 37, 51)],
              "validation": [make_instance(s) for s in (101, 103)],
              "test": [make_instance(s) for s in (201, 203, 207)],
              "unseen": [make_instance(s, unseen=True) for s in (301, 303)]}
    digests = {k: [instance_digest(i) for i in instances] for k, instances in splits.items()}
    all_digests = [d for group in digests.values() for d in group]
    assert len(all_digests) == len(set(all_digests))
    # Also reject duplicate physical configurations hidden behind distinct seed fields.
    physical = []
    for group in splits.values():
        for instance in group:
            data = instance.to_dict()
            data.pop("seed")
            physical.append(json.dumps(data, sort_keys=True))
    assert len(physical) == len(set(physical))
    if output:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        for split, instances in splits.items():
            for index, instance in enumerate(instances):
                (output / f"m10-{split}-{index}.json").write_text(
                    json.dumps(instance.to_dict(), indent=2) + "\n", encoding="utf-8")
    runs, training, checkpoints = [], [], []
    for seed in seeds:
        policy, report = train(splits["train"], splits["validation"], episodes=episodes,
                               seed=seed, checkpoint_every=max(1, min(10, episodes)))
        training.append(report)
        checkpoint = policy.to_dict()
        checkpoints.append({"seed": seed, "checkpoint": checkpoint})
        if output:
            policy.save(output / f"m10-q-seed-{seed}.json")
            policy = QPolicy.load(output / f"m10-q-seed-{seed}.json")
        frozen = deepcopy(policy.to_dict())
        for split in ("train", "validation", "test", "unseen"):
            for index, instance in enumerate(splits[split]):
                for kind in ("random", "heuristic", "q"):
                    row = run_episode(instance, kind, policy=policy, seed=1000 + seed * 10 + index)
                    runs.append({"split": split, "index": index, "training_seed": seed,
                                 "instance_sha256": instance_digest(instance), **row})
        assert frozen == policy.to_dict(), "test must never update the selected policy"
    for split in ("train", "validation", "test", "unseen"):
        for index, instance in enumerate(splits[split]):
            for kind in ("fifo", "cpsat", "cnp"):
                started = perf_counter()
                simulation = Simulation(instance, kind)
                while not simulation.all_tasks_completed and not simulation.finished:
                    simulation.step()
                row = metrics(simulation)
                # Same post-transition reward, stop contribution after completion.
                reward = -sum(sum(t["arrival_tick"] <= snapshot["tick"] and t["state"] != "completed"
                                  for t in snapshot["tasks"].values())
                              for snapshot in simulation.snapshots[1:])
                runs.append({"split": split, "index": index, "training_seed": None,
                             "instance_sha256": instance_digest(instance), "policy": kind,
                             "return": reward, "total_seconds": perf_counter() - started, **row})
            if llm_client is not None:
                # An explicit per-instance budget; never reset between agents within an episode.
                from taskfleet.ollama import OllamaClient
                client = OllamaClient(llm_client.config)
                row = run_episode(instance, "llm", client=client, seed=1000 + index)
                runs.append({"split": split, "index": index, "training_seed": None,
                             "instance_sha256": instance_digest(instance), **row})
    summaries = []
    for split in ("train", "validation", "test", "unseen"):
        for kind in sorted({r["policy"] for r in runs}):
            selected = [r for r in runs if r["split"] == split and r["policy"] == kind]
            if not selected:
                continue
            summary = {"split": split, "policy": kind, "runs": len(selected),
                       "complete": sum(r["completion_rate"] == 1 for r in selected)}
            for key in ("return", "completion_rate", "makespan", "distance", "total_tardiness",
                        "conflicts", "inference_seconds", "total_seconds"):
                values = [r[key] for r in selected if r.get(key) is not None]
                summary[key] = {"mean": mean(values) if values else None,
                                "stddev": stdev(values) if len(values) > 1 else 0,
                                "count": len(values)}
            summaries.append(summary)
    seed_summary = []
    for seed in seeds:
        for split in ("train", "validation", "test", "unseen"):
            selected = [r for r in runs if r["training_seed"] == seed
                        and r["split"] == split and r["policy"] == "q"]
            seed_summary.append({"training_seed": seed, "split": split,
                                 "mean_return": mean(r["return"] for r in selected),
                                 "mean_completion": mean(r["completion_rate"] for r in selected),
                                 "q_observations": sum(r["q_observations"] for r in selected),
                                 "q_known_observations": sum(r["q_known_observations"] for r in selected)})
    result = {"protocol": {"episodes": episodes, "training_seeds": list(seeds),
                            "instance_seeds": {"train": [11, 23, 37, 51], "validation": [101, 103],
                                               "test": [201, 203, 207], "unseen": [301, 303]},
                            "reward": "minus arrived unfinished tasks after transition; gamma=1",
                            "selection": "highest mean validation return; earliest tie",
                            "evaluation": "frozen tables; seeded random Q ties; no test selection",
                            "conflicts": "lexicographic robot ID; shared by local policies",
                            "information": "local policies exclude other robot state; V1 baselines see all robots",
                            "physics": "unchanged V1 BFS without robot collisions",
                            "stopping": "all policies stop at full completion or horizon",
                            "statistics": "descriptive across paired test instances and training seeds; no independence claim"},
              "splits": {k: [i.to_dict() for i in v] for k, v in splits.items()},
              "digests": digests, "training": training, "checkpoints": checkpoints,
              "runs": runs, "summary": summaries, "seed_summary": seed_summary}
    if output:
        (output / "m10.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episodes", type=int, default=60)
    parser.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--output", type=Path, default=Path("docs/results/v2"))
    parser.add_argument("--llm", action="store_true")
    parser.add_argument("--model", default="")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--call-budget", type=int, default=12)
    args = parser.parse_args(argv)
    try:
        client = None
        if args.llm:
            from taskfleet.ollama import OllamaClient, OllamaConfig
            client = OllamaClient(OllamaConfig(model=args.model, url=args.ollama_url,
                                               timeout=args.timeout, call_budget=args.call_budget))
        result = benchmark(episodes=args.episodes, seeds=args.seeds, output=args.output, llm_client=client)
        print(json.dumps({"training": [{k: v for k, v in r.items() if k not in {"curve", "validation"}}
                                        for r in result["training"]], "summary": result["summary"]}, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
