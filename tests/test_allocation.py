import itertools
import json

import pytest
from ortools.sat.python import cp_model

from taskfleet import Simulation, from_dict, load_instance
from taskfleet.allocation import Observation, cpsat, estimated_cost, fifo
from taskfleet.__main__ import main
from taskfleet.models import Grid, Robot, RobotState, Task


def test_fifo_and_cpsat_choose_different_pairs():
    observation = Simulation(load_instance("examples/allocation.json")).observation()
    assert fifo(observation)["assignments"] == [["r1", "t1"], ["r2", "t2"]]
    optimized = cpsat(observation)
    assert optimized["assignments"] == [["r2", "t1"], ["r1", "t2"]]
    assert optimized["estimated_cost"] == 4
    assert optimized["optimal"] and optimized["phases"] == ["OPTIMAL", "OPTIMAL"]


@pytest.mark.parametrize("strategy", ["fifo", "cpsat"])
def test_capacity_and_pending_retry(strategy):
    instance = from_dict({"width": 1, "height": 1, "horizon": 6,
                          "robots": [{"id": "small", "position": [0, 0], "capacity": 1},
                                     {"id": "large", "position": [0, 0], "capacity": 2}],
                          "tasks": [{"id": "a", "pickup": [0, 0], "destination": [0, 0], "load": 2},
                                    {"id": "b", "pickup": [0, 0], "destination": [0, 0], "load": 2}]})
    simulation = Simulation(instance, strategy)
    simulation.step()
    assert sum(t.robot_id is not None for t in simulation.tasks.values()) == 1
    simulation.run()
    assert simulation.all_tasks_completed
    assert all(t.robot_id == "large" for t in simulation.tasks.values())
    assert simulation.robots["small"].available
    simulation.check_invariants()


@pytest.mark.parametrize("robot,task,grid", [
    (Robot("r", (0, 0), 1), Task("t", (0, 0), (0, 0), load=2), Grid(1, 1)),
    (Robot("r", (0, 0), 1, state=RobotState.PICKING), Task("t", (0, 0), (0, 0)), Grid(1, 1)),
    (Robot("r", (0, 0), 1), Task("t", (2, 0), (2, 0)), Grid(3, 1, frozenset({(1, 0)}))),
    (Robot("r", (0, 0), 1), Task("t", (0, 0), (2, 0)), Grid(3, 1, frozenset({(1, 0)}))),
])
def test_no_compatible_robot(robot, task, grid):
    observation = Observation(0, grid, (robot,), (task,))
    assert estimated_cost(grid, robot, task) is None
    assert fifo(observation)["assignments"] == []
    assert cpsat(observation)["status"] == "NO_COMPATIBLE_PAIR"


@pytest.mark.parametrize("strategy", ["fifo", "cpsat"])
def test_busy_robot_and_manual_commitment_preserved(strategy):
    simulation = Simulation(load_instance("examples/allocation.json"), strategy)
    simulation.assign("r1", "t1")
    simulation.run()
    events = [e for e in simulation.events if e["kind"] == "assigned"]
    assert len(events) == 2 and events[0]["robot_id"] == "r1"
    assert simulation.all_tasks_completed


@pytest.mark.parametrize("positions,loads", [
    ([0, 4], [1, 1]), ([1, 1], [1, 2]), ([0, 3], [2, 2, 1]),
    ([2, 4], [1, 1, 1]), ([0, 0], [2, 1]), ([4, 0], [3, 3]),
])
def test_cpsat_matches_independent_exhaustive_optimum(positions, loads):
    robots = tuple(Robot(f"r{i}", (p, 0), i + 1) for i, p in enumerate(positions))
    tasks = tuple(Task(f"t{i}", (i, 0), (4 - i, 0), load=load) for i, load in enumerate(loads))
    observation = Observation(0, Grid(5, 1), robots, tasks)
    objectives = []
    # Independent enumeration: one value per task; -1 means unassigned.
    for assignment in itertools.product(range(-1, len(robots)), repeat=len(tasks)):
        chosen = [r for r in assignment if r >= 0]
        if len(chosen) != len(set(chosen)):
            continue
        if any(r >= 0 and robots[r].capacity < t.load for r, t in zip(assignment, tasks)):
            continue
        cost = sum(abs(robots[r].position[0] - t.pickup[0])
                   + abs(t.pickup[0] - t.destination[0]) + 2 * t.handling_ticks
                   for r, t in zip(assignment, tasks) if r >= 0)
        objectives.append((-len(chosen), cost))
    result = cpsat(observation)
    assert (-len(result["assignments"]), result["estimated_cost"]) == min(objectives)
    assert result["optimal"]


@pytest.mark.parametrize("statuses,expected_count,optimal", [
    ([cp_model.UNKNOWN], 0, False), ([cp_model.INFEASIBLE], 0, False),
    ([cp_model.FEASIBLE], 2, False),
    ([cp_model.OPTIMAL, cp_model.FEASIBLE], 2, False),
    ([cp_model.OPTIMAL, cp_model.UNKNOWN], 2, False),
    ([cp_model.OPTIMAL, cp_model.INFEASIBLE], 2, False),
    ([cp_model.OPTIMAL, cp_model.OPTIMAL], 2, True),
])
def test_solver_status_handling(monkeypatch, statuses, expected_count, optimal):
    original = cp_model.CpSolver.solve
    responses = iter(statuses)

    def controlled_solve(solver, model):
        original(solver, model)
        return next(responses)

    monkeypatch.setattr(cp_model.CpSolver, "solve", controlled_solve)
    result = cpsat(Simulation(load_instance("examples/allocation.json")).observation())
    assert len(result["assignments"]) == expected_count
    assert result["optimal"] == optimal
    assert result["phases"] == [cp_model.CpSolver().status_name(s) for s in statuses]


def test_invalid_model_status_raises(monkeypatch):
    monkeypatch.setattr(cp_model.CpSolver, "solve", lambda *args: cp_model.MODEL_INVALID)
    with pytest.raises(RuntimeError, match="Invalid CP-SAT model"):
        cpsat(Simulation(load_instance("examples/allocation.json")).observation())


@pytest.mark.parametrize("budget", [0, -1, float("inf"), float("nan"), True])
def test_invalid_budget(budget):
    with pytest.raises(ValueError, match="solver_budget"):
        Simulation(load_instance("examples/simple.json"), "cpsat", solver_budget=budget)


@pytest.mark.parametrize("strategy", ["fifo", "cpsat"])
def test_complete_reproducible_traces_and_detached_observation(strategy):
    instance = load_instance("examples/contention.json")
    first, second = Simulation(instance, strategy), Simulation(instance, strategy)
    observation = first.observation()
    observation.robots[0].position = (99, 0)
    observation.tasks[0].load = 99
    first.run()
    second.run()
    assert first.trace() == second.trace()
    assert json.loads(json.dumps(first.trace())) == first.trace()
    assert first.all_tasks_completed
    assert len([e for e in first.events if e["kind"] == "assigned"]) == 2


def test_cli_strategy_and_repeat(tmp_path, capsys):
    path = tmp_path / "trace.json"
    assert main(["examples/allocation.json", "--strategy", "cpsat", "--verify-repeat",
                 "--trace", str(path)]) == 0
    assert "identical traces" in capsys.readouterr().out
    assert json.loads(path.read_text())["current"]["all_tasks_completed"]
