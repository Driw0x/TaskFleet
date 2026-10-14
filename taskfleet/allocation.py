"""Allocation from current observations, without access to the simulation."""

from dataclasses import dataclass
import math

from .models import Grid, Robot, Task
from .paths import shortest_path


@dataclass
class Observation:
    tick: int
    grid: Grid
    robots: tuple[Robot, ...]
    tasks: tuple[Task, ...]


def estimated_cost(grid: Grid, robot: Robot, task: Task) -> int | None:
    """Estimated ticks: two BFS distances and two handling durations; no queue forecast."""
    if not robot.available or robot.capacity < task.load:
        return None
    pickup = shortest_path(grid, robot.position, task.pickup)
    delivery = shortest_path(grid, task.pickup, task.destination)
    if pickup is None or delivery is None:
        return None
    return len(pickup) + len(delivery) + 2 * task.handling_ticks


def fifo(observation: Observation) -> dict:
    remaining = {r.id: r for r in observation.robots if r.available}
    assignments = []
    cost = 0
    for task in observation.tasks:
        for robot_id in sorted(remaining):
            estimate = estimated_cost(observation.grid, remaining[robot_id], task)
            if estimate is not None:
                assignments.append([robot_id, task.id])
                cost += estimate
                del remaining[robot_id]
                break
    return {"assignments": assignments, "estimated_cost": cost,
            "status": "HEURISTIC", "optimal": False}


def cpsat(observation: Observation, budget: float = 1.0) -> dict:
    """Two solves, each bounded by deterministic time units (not wall seconds)."""
    from ortools.sat.python import cp_model

    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget) or budget <= 0:
        raise ValueError("solver_budget must be a finite positive number")
    costs = {}
    for task in observation.tasks:
        for robot in sorted(observation.robots, key=lambda r: r.id):
            cost = estimated_cost(observation.grid, robot, task)
            if cost is not None:
                costs[robot.id, task.id] = cost
    if not costs:
        return {"assignments": [], "estimated_cost": 0, "status": "NO_COMPATIBLE_PAIR",
                "optimal": True, "phases": []}
    model = cp_model.CpModel()
    choices = {pair: model.new_bool_var(f"pair_{index}")
               for index, pair in enumerate(costs)}
    for robot in observation.robots:
        model.add_at_most_one(v for (r, t), v in choices.items() if r == robot.id)
    for task in observation.tasks:
        model.add_at_most_one(v for (r, t), v in choices.items() if t == task.id)
    cardinality = sum(choices.values())
    model.maximize(cardinality)
    solver = cp_model.CpSolver()
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    solver.parameters.max_deterministic_time = budget
    phases = []

    def solve():
        status = solver.solve(model)
        phases.append(solver.status_name(status))
        if status == cp_model.MODEL_INVALID:
            raise RuntimeError(f"Invalid CP-SAT model: {model.validate()}")
        return status

    def result(pairs, status, optimal=False):
        return {"assignments": [list(p) for p in pairs], "estimated_cost": sum(costs[p] for p in pairs),
                "status": solver.status_name(status), "optimal": optimal,
                "phases": phases.copy()}

    first = solve()
    if first not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return result([], first)
    pairs = [p for p, v in choices.items() if solver.value(v)]
    if first == cp_model.FEASIBLE:
        return result(pairs, first)
    model.add(cardinality == len(pairs))
    model.minimize(sum(costs[p] * v for p, v in choices.items()))
    second = solve()
    if second in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        pairs = [p for p, v in choices.items() if solver.value(v)]
    # UNKNOWN keeps the feasible cardinality solution; never claim cost optimality.
    return result(pairs, second, second == cp_model.OPTIMAL)


def allocate(strategy: str, observation: Observation, budget: float) -> dict:
    if strategy == "fifo":
        return fifo(observation)
    if strategy == "cpsat":
        return cpsat(observation, budget)
    raise ValueError(f"Unknown allocation strategy {strategy!r}")
