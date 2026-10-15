"""Isolated, fixed-goal MAPF; warehouse simulation and V1 paths are unchanged.

Tick 0 contains the starts. Moves are cardinal or wait; vertex sharing and
opposite edge swaps are forbidden. Starts and goals are distinct within each
set. A goal is absorbing from its first visit, including a start already at its
goal. Plans include the start, end at first arrival, and occupy that goal forever.
The objective is sum of arrival ticks, including every wait before arrival.

CBS and the joint-state oracle are optimal only for this finite-horizon model
when SOLVED. Exhaustion proves only NO_SOLUTION_WITHIN_HORIZON. A priority failure
or a budget stop proves neither infeasibility nor optimality. Node budgets count
all expanded low/high/joint search states; seconds are a cooperative wall limit.
"""

import argparse
from dataclasses import dataclass, field
from heapq import heappop, heappush
from itertools import count, product
import json
import math
from pathlib import Path
from time import perf_counter

from .instances import _array, _id, _integer, _object, _position, from_dict as warehouse_from_dict
from .models import Grid, Position
from .paths import shortest_path


@dataclass(frozen=True)
class Problem:
    grid: Grid
    starts: dict[str, Position]
    goals: dict[str, Position]
    horizon: int

    def to_dict(self) -> dict:
        return {"width": self.grid.width, "height": self.grid.height,
                "obstacles": [list(p) for p in sorted(self.grid.obstacles)],
                "horizon": self.horizon,
                "agents": [{"id": a, "start": list(self.starts[a]),
                            "goal": list(self.goals[a])} for a in sorted(self.starts)]}


def from_dict(data: dict) -> Problem:
    """Parse a separate MAPF format using the strict V1 grid validators."""
    data = _object(data, {"width", "height", "agents", "horizon"}, {"obstacles"}, "mapf")
    # Reuse V1 validation, including strict dimensions and obstacle uniqueness.
    grid = warehouse_from_dict({"width": data["width"], "height": data["height"],
                                "obstacles": data.get("obstacles", []),
                                "robots": [], "tasks": []}).grid
    starts, goals = {}, {}
    for index, entry in enumerate(_array(data["agents"], "agents")):
        label = f"agents[{index}]"
        entry = _object(entry, {"id", "start", "goal"}, set(), label)
        agent = _id(entry["id"], starts, label)
        starts[agent] = _position(entry["start"], grid, f"{label}.start")
        goals[agent] = _position(entry["goal"], grid, f"{label}.goal")
    if len(set(starts.values())) != len(starts):
        raise ValueError("starts must be distinct")
    if len(set(goals.values())) != len(goals):
        raise ValueError("goals must be distinct")
    return Problem(grid, starts, goals, _integer(data["horizon"], "horizon"))


def load_problem(path: str | Path) -> Problem:
    def unique(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError(f"duplicate field {key!r}")
            data[key] = value
        return data
    return from_dict(json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique))


def validate_plan(problem: Problem, paths: dict) -> dict:
    """Independent full checker: no search, reservation or CBS conflict helpers.

    Report every illegal cell/move and pairwise conflict through the horizon.
    Metrics count moves/waits only through first arrival, never padded goal waits.
    """
    violations, clean = [], {}
    if not isinstance(paths, dict):
        return {"valid": False, "violations": [{"kind": "paths_not_object"}], "metrics": None}
    if set(paths) != set(problem.starts):
        violations.append({"kind": "agent_set", "expected": sorted(problem.starts)})
    for agent in sorted(problem.starts):
        path = paths.get(agent)
        if not isinstance(path, (list, tuple)) or not path:
            violations.append({"kind": "empty_path", "agent": agent})
            continue
        if any(not isinstance(p, (list, tuple)) or len(p) != 2
               or any(type(n) is not int for n in p) for p in path):
            violations.append({"kind": "invalid_position", "agent": agent})
            continue
        path = [tuple(p) for p in path]
        clean[agent] = path
        if path[0] != problem.starts[agent]:
            violations.append({"kind": "wrong_start", "agent": agent})
        if path[-1] != problem.goals[agent]:
            violations.append({"kind": "wrong_goal", "agent": agent})
        if len(path) - 1 > problem.horizon:
            violations.append({"kind": "horizon", "agent": agent})
        arrived = False
        for tick, cell in enumerate(path):
            if not problem.grid.accessible(cell):
                violations.append({"kind": "illegal_cell", "agent": agent, "tick": tick})
            if arrived and cell != problem.goals[agent]:
                violations.append({"kind": "left_goal", "agent": agent, "tick": tick})
            arrived |= cell == problem.goals[agent]
            if tick and sum(abs(a - b) for a, b in zip(cell, path[tick - 1])) > 1:
                violations.append({"kind": "illegal_move", "agent": agent, "tick": tick})
    agents = sorted(clean)
    for tick in range(problem.horizon + 1):
        for i, a in enumerate(agents):
            pa = clean[a][min(tick, len(clean[a]) - 1)]
            for b in agents[i + 1:]:
                pb = clean[b][min(tick, len(clean[b]) - 1)]
                if pa == pb:
                    violations.append({"kind": "vertex", "agents": [a, b], "tick": tick,
                                       "position": list(pa)})
                if tick:
                    old_a = clean[a][min(tick - 1, len(clean[a]) - 1)]
                    old_b = clean[b][min(tick - 1, len(clean[b]) - 1)]
                    if old_a != pa and old_a == pb and old_b == pa:
                        violations.append({"kind": "edge_swap", "agents": [a, b], "tick": tick})
    metrics = None
    if not violations:
        arrivals = {a: path.index(problem.goals[a]) for a, path in clean.items()}
        distance = sum(sum(p != q for p, q in zip(path[:arrivals[a]], path[1:arrivals[a] + 1]))
                       for a, path in clean.items())
        cost = sum(arrivals.values())
        metrics = {"arrivals": arrivals, "sum_of_costs": cost, "distance": distance,
                   "waits": cost - distance, "makespan": max(arrivals.values(), default=0)}
    return {"valid": not violations, "violations": violations, "metrics": metrics,
            "vertex_conflicts": sum(v["kind"] == "vertex" for v in violations),
            "edge_conflicts": sum(v["kind"] == "edge_swap" for v in violations)}


class BudgetExhausted(Exception):
    pass


@dataclass
class SearchBudget:
    max_nodes: int = 100_000
    seconds: float = 5.0
    started: float = field(default_factory=perf_counter)
    counts: dict = field(default_factory=lambda: {"low_level": 0, "high_level": 0, "joint": 0})

    def __post_init__(self):
        _integer(self.max_nodes, "max_nodes")
        if (isinstance(self.seconds, bool) or not isinstance(self.seconds, (int, float))
                or not math.isfinite(self.seconds) or self.seconds <= 0):
            raise ValueError("seconds must be finite and positive")

    def check(self):
        if perf_counter() - self.started >= self.seconds:
            raise BudgetExhausted("seconds")

    def expand(self, kind: str):
        self.check()
        if sum(self.counts.values()) >= self.max_nodes:
            raise BudgetExhausted("max_nodes")
        self.counts[kind] += 1


def _moves(grid: Grid, cell: Position):
    x, y = cell
    return [p for p in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1), cell)
            if grid.accessible(p)]


def _at(path, tick):
    return path[min(tick, len(path) - 1)]


@dataclass
class Reservations:
    """Vertex/forward-edge reservations, including persistent goal occupancy."""
    vertices: set = field(default_factory=set)
    edges: set = field(default_factory=set)

    def reserve(self, path, horizon):
        for tick in range(horizon + 1):
            self.vertices.add((_at(path, tick), tick))
            if tick:
                self.edges.add((_at(path, tick - 1), _at(path, tick), tick))


def _space_time(problem, agent, vertices, edges, budget):
    """A* with admissible Manhattan bound; minimal arrival under constraints.

    Edge constraints name (source, destination, arrival tick). Goal acceptance
    checks the entire remaining horizon, including future self-wait constraints.
    Reaching an unsafe absorbing goal cannot be followed by leaving it.
    """
    start, goal, horizon = problem.starts[agent], problem.goals[agent], problem.horizon
    if (start, 0) in vertices:
        return None
    serial = count()
    heuristic = lambda p: abs(p[0] - goal[0]) + abs(p[1] - goal[1])
    frontier = [(heuristic(start), 0, next(serial), start)]
    previous = {(start, 0): None}
    while frontier:
        _, tick, _, cell = heappop(frontier)
        budget.expand("low_level")
        if cell == goal:
            if (all((goal, t) not in vertices for t in range(tick, horizon + 1))
                    and all((goal, goal, t) not in edges for t in range(tick + 1, horizon + 1))):
                state, path = (cell, tick), []
                while state is not None:
                    path.append(state[0])
                    state = previous[state]
                return path[::-1]
            continue
        if tick >= horizon:
            continue
        for nxt in _moves(problem.grid, cell):
            arrival = tick + 1
            if ((nxt, arrival) in vertices or (cell, nxt, arrival) in edges
                    or (nxt, arrival) in previous or arrival + heuristic(nxt) > horizon):
                continue
            previous[nxt, arrival] = (cell, tick)
            heappush(frontier, (arrival + heuristic(nxt), arrival, next(serial), nxt))
    return None


def _conflict(paths):
    """CBS branching detector; intentionally separate from the public checker."""
    agents = sorted(paths)
    for tick in range(max((len(p) for p in paths.values()), default=0)):
        for index, a in enumerate(agents):
            for b in agents[index + 1:]:
                a_to, b_to = _at(paths[a], tick), _at(paths[b], tick)
                if a_to == b_to:
                    return (a, b, "vertex", tick, a_to)
                if tick:
                    a_from, b_from = _at(paths[a], tick - 1), _at(paths[b], tick - 1)
                    if a_from == b_to and b_from == a_to:
                        return (a, b, "edge", tick, a_from, a_to)
    return None


def _prioritized(problem, order, budget):
    reservations, paths = Reservations(), {}
    for agent in order:
        # Another agent's forward reservation forbids traversing the reverse edge.
        reverse = {(destination, source, tick) for source, destination, tick in reservations.edges}
        path = _space_time(problem, agent, reservations.vertices, reverse, budget)
        if path is None:
            return None
        paths[agent] = path
        reservations.reserve(path, problem.horizon)
    return paths


def _cbs(problem, budget):
    paths = {}
    for agent in sorted(problem.starts):
        path = _space_time(problem, agent, set(), set(), budget)
        if path is None:
            return None
        paths[agent] = path
    serial, seen = count(), {frozenset()}
    frontier = [(sum(len(p) - 1 for p in paths.values()), next(serial), frozenset(), paths)]
    while frontier:
        _, _, constraints, paths = heappop(frontier)
        budget.expand("high_level")
        conflict = _conflict(paths)
        if conflict is None:
            return paths
        a, b, kind, tick, *positions = conflict
        for agent in (a, b):
            if kind == "vertex":
                constraint = (agent, "vertex", tick, positions[0])
            else:
                source, destination = positions if agent == a else positions[::-1]
                constraint = (agent, "edge", tick, source, destination)
            child = constraints | {constraint}
            if child in seen:
                continue
            seen.add(child)
            vertices = {(c[3], c[2]) for c in child if c[0] == agent and c[1] == "vertex"}
            edges = {(c[3], c[4], c[2]) for c in child if c[0] == agent and c[1] == "edge"}
            replacement = _space_time(problem, agent, vertices, edges, budget)
            if replacement is not None:
                updated = paths | {agent: replacement}
                heappush(frontier, (sum(len(p) - 1 for p in updated.values()), next(serial), child, updated))
    return None


def _joint_oracle(problem, budget):
    """Independent exhaustive Dijkstra over synchronous joint states.

    Each transition costs number of agents not yet at goal before transition.
    Therefore accumulated cost equals sum of arrival ticks, including waits.
    No individual A*, CBS detector or reservations are used.
    """
    agents = sorted(problem.starts)
    starts, goals = tuple(problem.starts[a] for a in agents), tuple(problem.goals[a] for a in agents)
    free_cells = problem.grid.width * problem.grid.height - len(problem.grid.obstacles)
    if len(agents) > 4 or free_cells > 16 or problem.horizon > 16:
        raise ValueError("joint oracle limited to 4 agents, 16 free cells and horizon 16")
    serial = count()
    frontier = [(0, next(serial), 0, starts)]
    best, previous = {(0, starts): 0}, {(0, starts): None}
    while frontier:
        cost, _, tick, cells = heappop(frontier)
        if cost != best[tick, cells]:
            continue
        budget.expand("joint")
        if cells == goals:
            states, state = [], (tick, cells)
            while state is not None:
                states.append(state[1])
                state = previous[state]
            states.reverse()
            paths = {}
            for index, agent in enumerate(agents):
                path = [state[index] for state in states]
                paths[agent] = path[:path.index(goals[index]) + 1]
            return paths
        if tick == problem.horizon:
            continue
        choices = []
        for cell, goal in zip(cells, goals):
            x, y = cell
            choices.append([cell] if cell == goal else [p for p in
                ((x, y), (x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
                if problem.grid.accessible(p)])
        for nxt in product(*choices):
            budget.check()
            if len(set(nxt)) != len(nxt):
                continue
            if any(cells[i] == nxt[j] and cells[j] == nxt[i]
                   for i in range(len(agents)) for j in range(i + 1, len(agents))):
                continue
            new_cost = cost + sum(cell != goal for cell, goal in zip(cells, goals))
            state = (tick + 1, nxt)
            if new_cost < best.get(state, math.inf):
                best[state] = new_cost
                previous[state] = (tick, cells)
                heappush(frontier, (new_cost, next(serial), tick + 1, nxt))
    return None


def solve(problem: Problem, algorithm="cbs", *, priorities=None,
          max_nodes=100_000, seconds=5.0) -> dict:
    """Return a checked plan or explicit failure; never return unchecked partial paths."""
    # Validate even directly constructed Problems at this public boundary.
    problem = from_dict(problem.to_dict())
    if algorithm not in {"prioritized", "cbs", "oracle"}:
        raise ValueError("algorithm must be prioritized, cbs or oracle")
    order = sorted(problem.starts) if priorities is None else list(priorities)
    if len(order) != len(problem.starts) or set(order) != set(problem.starts):
        raise ValueError("priorities must contain every agent exactly once")
    budget = SearchBudget(max_nodes, seconds)
    reason, paths = None, None
    try:
        if algorithm == "prioritized":
            paths = _prioritized(problem, order, budget)
        elif algorithm == "cbs":
            paths = _cbs(problem, budget)
        else:
            paths = _joint_oracle(problem, budget)
        status = ("SOLVED" if paths is not None else "PRIORITY_FAILED"
                  if algorithm == "prioritized" else "NO_SOLUTION_WITHIN_HORIZON")
    except BudgetExhausted as error:
        status, reason = "BUDGET_EXHAUSTED", str(error)
    validation = validate_plan(problem, paths) if paths is not None else None
    if validation is not None and not validation["valid"]:
        raise RuntimeError(f"MAPF solver produced an invalid plan: {validation['violations']}")
    return {"algorithm": algorithm, "status": status, "reason": reason,
            "optimal": status == "SOLVED" and algorithm in {"cbs", "oracle"},
            "infeasible_within_horizon": status == "NO_SOLUTION_WITHIN_HORIZON",
            "horizon": problem.horizon, "priorities": order if algorithm == "prioritized" else None,
            "budgets": {"max_nodes": max_nodes, "seconds": seconds},
            "nodes": budget.counts, "total_nodes": sum(budget.counts.values()),
            "seconds": perf_counter() - budget.started, "validation": validation,
            "paths": None if paths is None else {a: [list(p) for p in path] for a, path in paths.items()}}


def individual_bound(problem: Problem) -> dict:
    """Independent BFS lower bound; these individual paths may collide."""
    distances = {}
    for agent in sorted(problem.starts):
        path = shortest_path(problem.grid, problem.starts[agent], problem.goals[agent])
        distances[agent] = None if path is None else len(path)
    return {"distances": distances,
            "sum_of_costs_lower_bound": None if None in distances.values() else sum(distances.values())}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path)
    parser.add_argument("--algorithm", choices=["prioritized", "cbs", "oracle"], default="cbs")
    parser.add_argument("--priorities", nargs="+", help="agent IDs, highest priority first")
    parser.add_argument("--max-nodes", type=int, default=100_000)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--horizon", type=int, help="explicit finite-horizon override")
    parser.add_argument("--validate", type=Path, help="validate an exported result or raw paths JSON")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        problem = load_problem(args.instance)
        if args.horizon is not None:
            problem = from_dict(problem.to_dict() | {"horizon": args.horizon})
        if args.validate:
            data = json.loads(args.validate.read_text(encoding="utf-8"))
            result = validate_plan(problem, data.get("paths", data) if isinstance(data, dict) else data)
        else:
            result = solve(problem, args.algorithm, priorities=args.priorities,
                           max_nodes=args.max_nodes, seconds=args.seconds)
            result["individual_bound"] = individual_bound(problem)
        serialized = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized, encoding="utf-8")
        print(serialized, end="")
        return 1 if args.validate and not result["valid"] else 0
    except (ValueError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
