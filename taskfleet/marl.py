"""Bounded cooperative allocation on the unchanged V1 manual engine.

Each learner sees its own robot, public pending tasks and its own BFS costs.
Actions are task IDs or None (wait). Simultaneous claims use robot-ID priority.
The shared reward is minus arrived, unfinished tasks AFTER each engine step.
Future tasks and downtime calendars never enter observations or policy inputs.
Independent tabular Q-learning is partially observable and has no convergence claim.
"""

import argparse
from copy import deepcopy
from dataclasses import asdict
import json
import math
from pathlib import Path
import random
from time import perf_counter

from .allocation import estimated_cost
from .engine import Simulation
from .instances import load_instance
from .metrics import metrics
from .models import TaskState


LLM_CONTEXT = ("Allocate tasks to finish deliveries promptly and minimize unfinished arrived tasks. "
               "Use only this observation. Waiting is legal; concurrent claims use robot-ID priority.")


class AllocationEnv:
    """At most four robots, eight tasks and an 8 x 8 grid; one engine tick per step."""

    def __init__(self, instance):
        if (len(instance.robots) > 4 or len(instance.tasks) > 8
                or instance.grid.width > 8 or instance.grid.height > 8):
            raise ValueError("tabular domain requires <=4 robots, <=8 tasks and grid <=8x8")
        self.instance = instance
        self.reset()

    def reset(self, seed=0):
        if type(seed) is not int or seed < 0:
            raise ValueError("seed must be a nonnegative integer")
        self.seed = seed
        self.simulation = Simulation(self.instance)
        self.conflicts = 0
        self.rejected_actions = 0
        self.wins = {r: 0 for r in self.simulation.robots}
        self.decisions = []
        self.rewards = []
        return self.observations()

    @property
    def terminated(self):
        return self.simulation.all_tasks_completed

    @property
    def truncated(self):
        return self.simulation.finished and not self.terminated

    def observations(self):
        public = self.simulation.observation()
        result = {}
        for robot in public.robots:
            costs = {task.id: cost for task in public.tasks
                     if (cost := estimated_cost(public.grid, robot, task)) is not None}
            result[robot.id] = {
                "tick": public.tick,
                "grid": {"width": public.grid.width, "height": public.grid.height,
                         "obstacles": [list(p) for p in sorted(public.grid.obstacles)]},
                "robot": {"id": robot.id, "position": list(robot.position),
                          "capacity": robot.capacity, "state": robot.state.value,
                          "task_id": robot.task_id, "unavailable": robot.unavailable},
                "tasks": [{"id": t.id, "pickup": list(t.pickup),
                           "destination": list(t.destination), "load": t.load,
                           "handling_ticks": t.handling_ticks, "deadline": t.deadline,
                           "arrival_tick": t.arrival_tick} for t in public.tasks],
                "costs": costs,
                "legal_actions": [None, *sorted(costs)],
            }
        return result

    def step(self, actions):
        if self.terminated or self.truncated:
            raise ValueError("episode has ended; reset before step")
        if not isinstance(actions, dict) or actions.keys() - self.simulation.robots.keys():
            raise ValueError("actions must map known robot IDs to task IDs or None")
        before = self.observations()
        claimed = set()
        accepted, rejected, conflicts = [], [], []
        for robot_id in sorted(before):
            action = actions.get(robot_id)
            if action is not None and not isinstance(action, str):
                rejected.append(robot_id)
            elif action not in before[robot_id]["legal_actions"]:
                rejected.append(robot_id)
            elif action is not None:
                if action in claimed:
                    conflicts.append(robot_id)
                else:
                    self.simulation.assign(robot_id, action)
                    claimed.add(action)
                    accepted.append([robot_id, action])
                    self.wins[robot_id] += 1
        self.conflicts += len(conflicts)
        self.rejected_actions += len(rejected)
        self.decisions.append({"tick": self.simulation.tick, "observations": before,
                               "proposals": deepcopy(actions), "accepted": accepted,
                               "rejected": rejected, "conflicts": conflicts})
        self.simulation.step()
        remaining = sum(t.arrival_tick <= self.simulation.tick and t.state != TaskState.COMPLETED
                        for t in self.simulation.tasks.values())
        reward = -remaining
        self.rewards.append(reward)
        return self.observations(), reward, self.terminated, self.truncated, {
            "accepted": accepted, "rejected": rejected, "conflicts": conflicts,
            "remaining_arrived_tasks": remaining}


def state_key(observation):
    """Exact finite local observation, without elapsed tick or robot identifier.

    Deadlines become relative to the current tick. This is a tabular encoding,
    not a Markov-state or generalization guarantee.
    """
    state = deepcopy(observation)
    tick = state.pop("tick")
    state["robot"].pop("id")
    for task in state["tasks"]:
        task["arrival_tick"] = tick - task["arrival_tick"]
        if task["deadline"] is not None:
            task["deadline"] -= tick
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def heuristic_action(observation):
    return min(observation["costs"], key=lambda t: (observation["costs"][t], t), default=None)


class QPolicy:
    """One table per robot, common reward, gamma=1 and legal action masks."""

    def __init__(self, tables=None):
        self.tables = deepcopy(tables) if tables is not None else {}

    def values(self, robot_id, observation):
        # Evaluation never inserts unseen states into the frozen policy.
        return self.tables.get(robot_id, {}).get(state_key(observation), {})

    def action(self, robot_id, observation, rng, epsilon=0.0):
        legal = observation["legal_actions"]
        if epsilon > 0 and rng.random() < epsilon:
            return rng.choice(legal)
        values = self.values(robot_id, observation)
        best = max(values.get(json.dumps(a), 0.0) for a in legal)
        return rng.choice([a for a in legal if values.get(json.dumps(a), 0.0) == best])

    def update(self, robot_id, before, action, reward, after, done, alpha=0.2):
        values = self.tables.setdefault(robot_id, {}).setdefault(state_key(before), {})
        key = json.dumps(action)
        future = self.values(robot_id, after)
        target = reward if done else reward + max(
            future.get(json.dumps(a), 0.0) for a in after["legal_actions"])
        values[key] = values.get(key, 0.0) + alpha * (target - values.get(key, 0.0))

    def to_dict(self):
        return {"schema": 1, "algorithm": "independent_q_learning", "gamma": 1,
                "observation": "local_v1", "tables": deepcopy(self.tables)}

    def save(self, path):
        Path(path).write_text(json.dumps(self.to_dict(), sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("schema") != 1
                or data.get("algorithm") != "independent_q_learning"
                or data.get("gamma") != 1 or data.get("observation") != "local_v1"
                or not isinstance(data.get("tables"), dict)):
            raise ValueError("unsupported Q-policy checkpoint")
        for robot, states in data["tables"].items():
            if not isinstance(robot, str) or not isinstance(states, dict):
                raise ValueError("invalid Q-policy table")
            for state, values in states.items():
                if not isinstance(state, str) or not isinstance(values, dict):
                    raise ValueError("invalid Q-policy state")
                for action, value in values.items():
                    try:
                        decoded = json.loads(action)
                    except (ValueError, TypeError) as error:
                        raise ValueError("invalid Q-policy action") from error
                    if (decoded is not None and not isinstance(decoded, str)
                            or type(value) not in (int, float) or not math.isfinite(value)):
                        raise ValueError("invalid Q-policy value")
        return cls(data["tables"])


def run_episode(instance, kind="heuristic", *, policy=None, seed=0, client=None, trace=False):
    if kind not in {"random", "heuristic", "q", "llm"}:
        raise ValueError("unknown local policy")
    if kind == "q" and policy is None or kind == "llm" and client is None:
        raise ValueError("requested policy requires checkpoint or Ollama client")
    started = perf_counter()
    env = AllocationEnv(instance)
    observations = env.reset(seed)
    rng = random.Random(seed)
    inference = 0.0
    q_observations = q_known_observations = 0
    llm_actions_accepted = 0
    while not env.terminated and not env.truncated:
        decision_start = perf_counter()
        actions = {}
        sources = {}
        for robot, observation in observations.items():
            legal = observation["legal_actions"]
            if len(legal) == 1:
                actions[robot] = None
            elif kind == "q":
                q_observations += 1
                q_known_observations += bool(policy.values(robot, observation))
                actions[robot] = policy.action(robot, observation, rng)
            elif kind == "random":
                actions[robot] = rng.choice(legal)
            elif kind == "llm":
                sources[robot] = client.decide(observation, legal, context=LLM_CONTEXT)
                actions[robot] = sources[robot]["action"]
            else:
                actions[robot] = heuristic_action(observation)
        inference += perf_counter() - decision_start
        observations, _, _, _, info = env.step(actions)
        llm_actions_accepted += sum(robot in sources and not sources[robot]["fallback"]
                                    and sources[robot]["action"] == task
                                    for robot, task in info["accepted"])
    row = {**metrics(env.simulation), "policy": kind, "seed": seed,
           "return": sum(env.rewards), "terminated": env.terminated, "truncated": env.truncated,
           "conflicts": env.conflicts, "rejected_actions": env.rejected_actions,
           "priority_wins": env.wins, "inference_seconds": inference,
           "q_observations": q_observations, "q_known_observations": q_known_observations,
           "total_seconds": perf_counter() - started}
    if client is not None:
        row["llm_actions_accepted"] = llm_actions_accepted
        row["llm"] = deepcopy(client.metrics)
        row["llm_trace"] = deepcopy(client.trace)
    if trace:
        row["trace"] = env.simulation.trace()
        row["decisions"] = env.decisions
        row["rewards"] = env.rewards
    return row


def train(training, validation, *, episodes=100, seed=0, checkpoint_every=10, alpha=0.2):
    """Select a checkpoint ONLY on validation return. Test instances are not accepted."""
    if (not training or not validation or type(episodes) is not int or episodes < 1
            or type(checkpoint_every) is not int or checkpoint_every < 1
            or not 0 < alpha <= 1):
        raise ValueError("nonempty splits, positive episode/checkpoint budgets and 0<alpha<=1 required")
    def physical_configuration(instance):
        data = instance.to_dict()
        data.pop("seed")
        return json.dumps(data, sort_keys=True)

    if {physical_configuration(i) for i in training} & {physical_configuration(i) for i in validation}:
        raise ValueError("training and validation instances must be disjoint")
    started = perf_counter()
    rng = random.Random(seed)
    policy, best = QPolicy(), QPolicy()
    best_score = -math.inf
    curve, validations = [], []
    selected = 0
    for episode in range(1, episodes + 1):
        env = AllocationEnv(training[rng.randrange(len(training))])
        observations = env.reset(seed)
        epsilon = 0.3 + (0.05 - 0.3) * (episode - 1) / max(1, episodes - 1)
        while not env.terminated and not env.truncated:
            actions = {r: policy.action(r, o, rng, epsilon) for r, o in observations.items()}
            after, reward, terminated, truncated, _ = env.step(actions)
            for robot in observations:
                policy.update(robot, observations[robot], actions[robot], reward, after[robot],
                              terminated or truncated, alpha)
            observations = after
        curve.append({"episode": episode, "epsilon": epsilon, "return": sum(env.rewards),
                      "completion_rate": metrics(env.simulation)["completion_rate"],
                      "conflicts": env.conflicts})
        if episode % checkpoint_every == 0 or episode == episodes:
            scores = [run_episode(i, "q", policy=policy, seed=seed + index)["return"]
                      for index, i in enumerate(validation)]
            score = sum(scores) / len(scores)
            validations.append({"episode": episode, "mean_return": score, "returns": scores})
            if score > best_score:
                best, best_score, selected = QPolicy(policy.tables), score, episode
    return best, {"training_seed": seed, "episodes": episodes, "alpha": alpha, "gamma": 1,
                  "epsilon_start": 0.3, "epsilon_end": 0.05,
                  "checkpoint_every": checkpoint_every, "selected_episode": selected,
                  "validation_return": best_score, "curve": curve, "validation": validations,
                  "training_seconds": perf_counter() - started}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fit = commands.add_parser("train")
    fit.add_argument("--train", nargs="+", required=True, type=Path)
    fit.add_argument("--validation", nargs="+", required=True, type=Path)
    fit.add_argument("--episodes", type=int, default=100)
    fit.add_argument("--seed", type=int, default=0)
    fit.add_argument("--checkpoint-every", type=int, default=10)
    fit.add_argument("--checkpoint", type=Path, required=True)
    fit.add_argument("--json", type=Path)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("instance", type=Path)
    evaluate.add_argument("--policy", choices=["random", "heuristic", "q", "llm"], default="heuristic")
    evaluate.add_argument("--checkpoint", type=Path)
    evaluate.add_argument("--seed", type=int, default=0)
    evaluate.add_argument("--json", type=Path)
    evaluate.add_argument("--trace", action="store_true")
    evaluate.add_argument("--selection", type=Path, help="frozen benchmark report; explicit options override it")
    evaluate.add_argument("--ollama-url")
    evaluate.add_argument("--model")
    evaluate.add_argument("--timeout", type=float)
    evaluate.add_argument("--temperature", type=float)
    evaluate.add_argument("--max-tokens", type=int)
    evaluate.add_argument("--call-budget", type=int)
    evaluate.add_argument("--load-timeout", type=float)
    evaluate.add_argument("--scenario-budget", type=float)
    evaluate.add_argument("--num-ctx", type=int)
    evaluate.add_argument("--llm-seed", type=int)
    evaluate.add_argument("--keep-alive")
    evaluate.add_argument("--think", choices=["true", "false"], help="explicit Ollama thinking mode")
    evaluate.add_argument("--preload", action="store_true", help="load before starting decision budget")
    args = parser.parse_args(argv)
    try:
        if args.command == "train":
            policy, result = train([load_instance(p) for p in args.train],
                                   [load_instance(p) for p in args.validation],
                                   episodes=args.episodes, seed=args.seed,
                                   checkpoint_every=args.checkpoint_every)
            policy.save(args.checkpoint)
        else:
            client = None
            policy = QPolicy.load(args.checkpoint) if args.checkpoint else None
            if args.policy == "llm":
                from .ollama import OllamaClient, preload_model
                from .ollama_selection import resolve_config
                config = resolve_config("m10", selection=args.selection, url=args.ollama_url,
                    model=args.model, timeout=args.timeout, temperature=args.temperature,
                    max_tokens=args.max_tokens, call_budget=args.call_budget, load_timeout=args.load_timeout,
                    scenario_budget=args.scenario_budget, num_ctx=args.num_ctx, seed=args.llm_seed,
                    keep_alive=args.keep_alive, think=None if args.think is None else args.think == "true")
                loading = preload_model(config) if args.preload else None
                if loading is not None and loading["status"] != "ok":
                    raise RuntimeError(f"Ollama preload failed: {loading.get('error')}: {loading.get('error_detail')}")
                client = OllamaClient(config)
            result = run_episode(load_instance(args.instance), args.policy, policy=policy,
                                 seed=args.seed, client=client, trace=args.trace)
            if client is not None:
                result["ollama_config"] = asdict(config)
                result["ollama_preload"] = loading
                result["selection_source"] = str(args.selection) if args.selection else None
        if args.json:
            args.json.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps({k: v for k, v in result.items()
                          if k not in {"trace", "decisions", "rewards", "llm_trace", "curve"}},
                         indent=2, ensure_ascii=False))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
