"""Snapshot allocation by DSA-B and explicit distributed confirmation.

Each robot owns one variable (compatible pending task or None). Public domain
advertisements define edges between robots with an overlapping task domain.
Only the owner computes its BFS/handling costs. DSA exchanges selected values,
never other agents' states or costs. Simultaneous strict improvements occur with
probability p; ties retain the current value. This is not an optimality guarantee.

The potential is sum(cost(task), or R for idle) + P per conflicting robot pair.
For public C >= every compatible cost, R=n*C+1 makes cardinality lexicographically
dominant on feasible assignments; P=n*R+n*C+1 makes any conflict more expensive
than every feasible assignment. Confirmation claims values and acknowledges
lexicographic-ID winners; losers abandon their task without a central repair.
All transport is synchronous in-process. Evaluation may inspect global costs;
local decision functions cannot access that evaluator or the simulation.
"""

import argparse
from collections import Counter
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from itertools import product
import json
import math
from pathlib import Path
import random
from time import perf_counter

from .allocation import Observation, cpsat, estimated_cost, fifo
from .cnp import contract_net
from .engine import Simulation
from .instances import load_instance
from .models import Grid, Robot, Task, TaskState
from .ollama import OllamaClient, OllamaConfig


@dataclass
class LocalAgent:
    grid: Grid
    robot: Robot
    tasks: tuple[Task, ...]
    tick: int
    costs: dict[str, int] = field(init=False)
    neighbors: tuple[str, ...] = ()
    received: dict[str, str | None] = field(default_factory=dict)
    value: str | None = None

    def __post_init__(self):
        self.costs = {}
        for task in self.tasks:
            cost = estimated_cost(self.grid, self.robot, task)
            if cost is not None:
                self.costs[task.id] = cost

    @property
    def domain(self) -> tuple:
        return (None, *sorted(self.costs))

    def initial_value(self):
        return min(self.costs, key=lambda task: (self.costs[task], task), default=None)

    def score(self, value: str | None, idle_penalty: int, conflict_penalty: int) -> int:
        if value is None:
            return idle_penalty
        return self.costs[value] + conflict_penalty * sum(
            other == value for other in self.received.values())

    def best_value(self, idle_penalty: int, conflict_penalty: int):
        scores = {value: self.score(value, idle_penalty, conflict_penalty)
                  for value in self.domain}
        minimum = min(scores.values())
        if scores[self.value] == minimum:
            return self.value, False
        # Domain order is fixed: idle, then task ID; equal minima are deterministic.
        return next(value for value in self.domain if scores[value] == minimum), True

    def confirm(self):
        if self.value is None:
            return None
        return (None if any(sender < self.robot.id and value == self.value
                            for sender, value in self.received.items()) else self.value)

    def llm_observation(self) -> dict:
        return {"tick": self.tick,
                "grid": {"width": self.grid.width, "height": self.grid.height,
                         "obstacles": [list(p) for p in sorted(self.grid.obstacles)]},
                "robot": {"id": self.robot.id, "position": list(self.robot.position),
                          "capacity": self.robot.capacity, "available": self.robot.available},
                "tasks": [{"id": task.id, "pickup": list(task.pickup),
                           "destination": list(task.destination), "load": task.load,
                           "handling_ticks": task.handling_ticks, "deadline": task.deadline}
                          for task in self.tasks],
                "own_costs": self.costs.copy(), "current_action": self.value,
                "messages": [{"sender": sender, "action": value}
                             for sender, value in sorted(self.received.items())]}


def local_agents(observation: Observation) -> list[LocalAgent]:
    tasks = tuple(deepcopy(task) for task in observation.tasks
                  if task.state == TaskState.PENDING and task.arrival_tick <= observation.tick)
    return [LocalAgent(observation.grid, deepcopy(robot), tasks, observation.tick)
            for robot in sorted(observation.robots, key=lambda robot: robot.id)]


def assignment_quality(observation: Observation, assignments: list) -> dict:
    """Independent final checker; not used to choose or repair local decisions."""
    robots = {robot.id: robot for robot in observation.robots}
    tasks = {task.id: task for task in observation.tasks}
    robot_counts = Counter(robot for robot, _ in assignments)
    task_counts = Counter(task for _, task in assignments)
    errors = []
    cost = 0
    for robot_id, task_id in assignments:
        robot, task = robots.get(robot_id), tasks.get(task_id)
        if robot is None or task is None:
            errors.append("unknown_id")
            continue
        estimate = estimated_cost(observation.grid, robot, task)
        if (estimate is None or task.state != TaskState.PENDING
                or task.arrival_tick > observation.tick):
            errors.append("outside_domain")
        else:
            cost += estimate
    duplicates = sum(n * (n - 1) // 2 for n in task_counts.values())
    if duplicates:
        errors.append("duplicate_task")
    if any(n > 1 for n in robot_counts.values()):
        errors.append("duplicate_robot")
    return {"cardinality": len(assignments), "unique_tasks": len(task_counts),
            "estimated_cost": cost, "conflict_pairs": duplicates,
            "feasible": not errors, "errors": errors}


def exhaustive(observation: Observation, max_states: int = 100_000) -> dict:
    """Central evaluation reference, with proof only after complete enumeration."""
    if type(max_states) is not int or max_states < 1:
        raise ValueError("max_states must be a positive integer")
    started = perf_counter()
    agents = local_agents(observation)
    states = math.prod(len(agent.domain) for agent in agents)
    best_key, best = (0, 0), []
    checked = 0
    for values in product(*(agent.domain for agent in agents)):
        if checked >= max_states:
            break
        checked += 1
        selected = [value for value in values if value is not None]
        if len(selected) != len(set(selected)):
            continue
        cost = sum(agent.costs[value] for agent, value in zip(agents, values)
                   if value is not None)
        key = (-len(selected), cost)
        if key < best_key:
            best_key = key
            best = [[agent.robot.id, value] for agent, value in zip(agents, values)
                    if value is not None]
    return {"assignments": best, "estimated_cost": best_key[1],
            "optimal": checked == states, "status": "OPTIMAL" if checked == states else "STATE_BUDGET",
            "states": states, "states_checked": checked, "total_seconds": perf_counter() - started}


def distributed(observation: Observation, *, rounds: int = 20, probability: float = 0.7,
                seed: int = 0, message_budget: int = 100_000,
                llm: OllamaClient | None = None) -> dict:
    if type(rounds) is not int or rounds < 0:
        raise ValueError("rounds must be a nonnegative integer")
    if type(message_budget) is not int or message_budget < 0:
        raise ValueError("message_budget must be a nonnegative integer")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if type(probability) not in (int, float) or not math.isfinite(probability) or not 0 <= probability <= 1:
        raise ValueError("probability must lie in [0, 1]")
    started = perf_counter()
    agents = local_agents(observation)
    public_domains = {agent.robot.id: set(agent.domain) - {None} for agent in agents}
    for agent in agents:
        agent.neighbors = tuple(other for other, domain in public_domains.items()
                                if other != agent.robot.id and public_domains[agent.robot.id] & domain)
    edges = sum(len(agent.neighbors) for agent in agents)
    initialization = len(agents) * max(0, len(agents) - 1)
    confirmation_reserve = 2 * edges
    cells = observation.grid.width * observation.grid.height - len(observation.grid.obstacles)
    max_handling = max((task.handling_ticks for agent in agents for task in agent.tasks), default=0)
    cost_bound = 2 * max(0, cells - 1) + 2 * max_handling
    idle_penalty = len(agents) * cost_bound + 1
    conflict_penalty = len(agents) * idle_penalty + len(agents) * cost_bound + 1
    messages, history = [], []
    transmissions = 0
    optimization_messages = 0
    completed_rounds = 0
    oscillations = 0
    rng = random.Random(seed)
    stop_reason = "ROUND_BUDGET"

    def exchange(kind: str, number: int):
        nonlocal transmissions
        for agent in agents:
            agent.received = {}
        by_id = {agent.robot.id: agent for agent in agents}
        # Snapshot every sender value before any local update or confirmation.
        for agent in agents:
            for recipient in agent.neighbors:
                message = {"type": kind, "round": number, "sender": agent.robot.id,
                           "recipient": recipient, "action": agent.value}
                messages.append(message)
                by_id[recipient].received[agent.robot.id] = agent.value
                transmissions += 1

    sufficient = message_budget >= initialization + confirmation_reserve
    if not sufficient:
        stop_reason = "COMMUNICATION_BUDGET"
    else:
        for agent in agents:
            messages.append({"type": "domain", "round": 0, "sender": agent.robot.id,
                             "recipient": "all", "tasks": sorted(public_domains[agent.robot.id])})
            agent.value = agent.initial_value()
        transmissions += initialization
        seen = {tuple(agent.value for agent in agents)}
        for number in range(1, rounds + 1):
            if transmissions + edges + confirmation_reserve > message_budget:
                stop_reason = "COMMUNICATION_BUDGET"
                break
            exchange("value", number)
            optimization_messages += edges
            updates = []
            improvements = 0
            for agent in agents:
                if llm is None:
                    value, improves = agent.best_value(idle_penalty, conflict_penalty)
                    improvements += improves
                    value = value if improves and rng.random() < probability else agent.value
                else:
                    value = llm.decide(
                        agent.llm_observation(), agent.domain,
                        context="Maximize distinct assigned tasks, then minimize own cost. "
                        "Neighbors selecting your task create a conflict. Idle is null.")["action"]
                    improvements += value != agent.value
                updates.append(value)
            previous = tuple(agent.value for agent in agents)
            for agent, value in zip(agents, updates):
                agent.value = value
            state = tuple(updates)
            oscillations += state in seen and state != previous
            seen.add(state)
            completed_rounds = number
            history.append({"round": number, "values": dict(zip(public_domains, updates)),
                            "changed": sum(a != b for a, b in zip(previous, updates)),
                            "strict_improvements": improvements if llm is None else None})
            if not improvements:
                stop_reason = "LOCAL_OPTIMUM" if llm is None else "UNCHANGED_LLM"
                break
    raw = [[agent.robot.id, agent.value] for agent in agents if agent.value is not None]
    before_confirmation = transmissions
    if sufficient:
        exchange("claim", completed_rounds + 1)
        confirmed = [agent.confirm() for agent in agents]
        for agent, value in zip(agents, confirmed):
            agent.value = value
        exchange("confirmed", completed_rounds + 1)
    assignments = [[agent.robot.id, agent.value] for agent in agents if agent.value is not None]
    raw_quality = assignment_quality(observation, raw)
    quality = assignment_quality(observation, assignments)
    if not quality["feasible"]:
        raise RuntimeError("Distributed confirmation produced an infeasible assignment")
    result = {"method": "llm" if llm is not None else "dsa", "assignments": assignments,
              "estimated_cost": quality["estimated_cost"], "optimal": False,
              "status": "CONFIRMED", "stop_reason": stop_reason, "seed": seed,
              "probability": probability, "round_budget": rounds, "rounds": completed_rounds,
              "message_budget": message_budget, "messages": messages,
              "logical_messages": len(messages), "transmissions": transmissions,
              "initialization_transmissions": initialization if sufficient else 0,
              "optimization_transmissions": optimization_messages,
              "confirmation_transmissions": transmissions - before_confirmation,
              "minimum_confirmation_budget": initialization + confirmation_reserve,
              "confirmation_abandonments": len(raw) - len(assignments),
              "raw_assignments": raw, "raw_quality": raw_quality, "final_quality": quality,
              "history": history, "oscillations": oscillations,
              "domains": {agent.robot.id: list(agent.domain) for agent in agents},
              "neighbors": {agent.robot.id: list(agent.neighbors) for agent in agents},
              "penalties": {"public_cost_bound": cost_bound, "idle": idle_penalty,
                            "conflict_pair": conflict_penalty},
              "total_seconds": perf_counter() - started}
    if llm is not None:
        result["llm_config"] = asdict(llm.config)
        result["llm_metrics"] = deepcopy(llm.metrics)
        result["llm_trace"] = deepcopy(llm.trace)
    return result


def compare_snapshot(observation: Observation, *, rounds=20, probability=0.7, seed=0,
                     message_budget=100_000, solver_budget=1.0, enumeration_budget=100_000,
                     llm=None) -> dict:
    result = {}
    methods = {"fifo": lambda: fifo(observation), "cpsat": lambda: cpsat(observation, solver_budget),
               "cnp": lambda: contract_net(observation),
               "dsa": lambda: distributed(observation, rounds=rounds, probability=probability,
                                           seed=seed, message_budget=message_budget),
               "exhaustive": lambda: exhaustive(observation, enumeration_budget)}
    if llm is not None:
        methods["llm"] = lambda: distributed(observation, rounds=rounds, probability=probability,
                                              seed=seed, message_budget=message_budget, llm=llm)
    for name, method in methods.items():
        started = perf_counter()
        row = method()
        row["total_seconds"] = perf_counter() - started
        row["quality"] = assignment_quality(observation, row["assignments"])
        row.setdefault("logical_messages", len(row.get("messages", [])))
        row.setdefault("transmissions", sum(
            max(0, len(observation.robots) - 1) if message["recipient"] == "all"
            else int(message["sender"] != message["recipient"])
            for message in row.get("messages", [])))
        result[name] = row
    reference = result["exhaustive"] if result["exhaustive"]["optimal"] else result["cpsat"]
    for row in result.values():
        proven = reference["optimal"]
        row["cardinality_gap"] = (len(reference["assignments"]) - len(row["assignments"])) if proven else None
        row["cost_gap_at_optimal_cardinality"] = (
            row["estimated_cost"] - reference["estimated_cost"]
            if proven and len(reference["assignments"]) == len(row["assignments"]) else None)
    return {"tick": observation.tick, "reference_proven": reference["optimal"], "methods": result}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path)
    parser.add_argument("--method", choices=["dsa", "llm"], default="dsa")
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--probability", type=float, default=0.7)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--message-budget", type=int, default=100_000,
                        help="total transmissions, including domain discovery and reserved confirmation")
    parser.add_argument("--enumeration-budget", type=int, default=100_000)
    parser.add_argument("--solver-budget", type=float, default=1.0)
    parser.add_argument("--tick", type=int, default=0)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--execute", action="store_true", help="execute only confirmed snapshot assignments in V1")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--selection", type=Path, help="frozen benchmark report; explicit options override it")
    parser.add_argument("--url")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--max-tokens", type=int)
    parser.add_argument("--call-budget", type=int)
    parser.add_argument("--load-timeout", type=float)
    parser.add_argument("--scenario-budget", type=float)
    parser.add_argument("--num-ctx", type=int)
    parser.add_argument("--llm-seed", type=int)
    parser.add_argument("--keep-alive")
    parser.add_argument("--think", choices=["true", "false"], help="explicit Ollama thinking mode")
    parser.add_argument("--preload", action="store_true", help="load separately before starting decision budget")
    arguments = parser.parse_args(argv)
    try:
        simulation = Simulation(load_instance(arguments.instance))
        simulation.run(arguments.tick)
        client, loading = None, None
        if arguments.method == "llm":
            from .ollama import preload_model
            from .ollama_selection import resolve_config
            config = resolve_config("m8", selection=arguments.selection, url=arguments.url,
                model=arguments.model, timeout=arguments.timeout, temperature=arguments.temperature,
                max_tokens=arguments.max_tokens, call_budget=arguments.call_budget,
                load_timeout=arguments.load_timeout, scenario_budget=arguments.scenario_budget,
                num_ctx=arguments.num_ctx, seed=arguments.llm_seed, keep_alive=arguments.keep_alive,
                think=None if arguments.think is None else arguments.think == "true")
            if arguments.preload:
                loading = preload_model(config)
                if loading["status"] != "ok":
                    raise RuntimeError(f"Ollama preload failed: {loading.get('error')}: {loading.get('error_detail')}")
            client = OllamaClient(config)
        options = dict(rounds=arguments.rounds, probability=arguments.probability,
                       seed=arguments.seed, message_budget=arguments.message_budget, llm=client)
        observation = simulation.observation()
        if arguments.compare:
            result = compare_snapshot(observation, solver_budget=arguments.solver_budget,
                                      enumeration_budget=arguments.enumeration_budget, **options)
            decision = result["methods"][arguments.method]
        else:
            result = decision = distributed(observation, **options)
        if client is not None:
            result["ollama_preload"] = loading
            result["selection_source"] = str(arguments.selection) if arguments.selection else None
        if arguments.execute:
            for robot, task in decision["assignments"]:
                simulation.assign(robot, task)
            simulation.run()
            simulation.check_invariants()
            result["execution"] = {"current": simulation.snapshot(), "events": simulation.events,
                                   "invalid_decisions": simulation.invalid_decisions}
        content = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if arguments.output:
            arguments.output.write_text(content, encoding="utf-8")
        print(content, end="")
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
