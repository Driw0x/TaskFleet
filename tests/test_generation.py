import copy
import json
import random

import pytest

from taskfleet import Simulation, load_instance
from taskfleet.instances import generate_instance, loads_instance
from taskfleet.paths import shortest_path


@pytest.mark.parametrize("seed", [0, 42, 999])
def test_generation_fixed_dimensions_feasibility_and_roundtrip(seed):
    obstacles = [[3, y] for y in range(5)]  # Two disconnected components.
    resources = [{"id": "station", "position": [1, 1], "capacity": 1}]
    original = copy.deepcopy((obstacles, resources))
    random_state = random.getstate()
    parameters = dict(width=7, height=5, robot_count=4, task_count=20, seed=seed,
                      horizon=200, obstacles=obstacles, resources=resources)
    instance = generate_instance(**parameters)
    assert instance.grid.width == 7 and instance.grid.height == 5
    assert len(instance.robots) == 4 and len(instance.tasks) == 20
    assert len({r.id for r in instance.robots.values()}) == 4
    assert len({t.id for t in instance.tasks.values()}) == 20
    assert all(instance.grid.accessible(r.position) and 1 <= r.capacity <= 3
               for r in instance.robots.values())
    for task in instance.tasks.values():
        assert instance.grid.accessible(task.pickup) and instance.grid.accessible(task.destination)
        assert shortest_path(instance.grid, task.pickup, task.destination) is not None
        assert any(r.capacity >= task.load and shortest_path(instance.grid, r.position, task.pickup) is not None
                   for r in instance.robots.values())
    assert generate_instance(**parameters).to_dict() == instance.to_dict()
    assert loads_instance(json.dumps(instance.to_dict())).to_dict() == instance.to_dict()
    assert (obstacles, resources) == original and random.getstate() == random_state
    assert instance.to_dict()["resources"] == resources


@pytest.mark.parametrize("strategy", ["fifo", "cpsat", "cnp"])
def test_generation_shared_resource_and_model_overlap(strategy):
    instance = generate_instance(width=1, height=1, robot_count=3, task_count=6, seed=4,
                                 horizon=20, resources=[{"id": "station", "position": [0, 0], "capacity": 1}])
    simulation = Simulation(instance, strategy)
    simulation.run()
    simulation.check_invariants()
    assert simulation.all_tasks_completed
    assert any(e["kind"] == "waiting" for e in simulation.events)
    assert all(len(s["resources"]["station"]["holders"]) <= 1 for s in simulation.snapshots)


@pytest.mark.parametrize("robots,tasks,horizon", [(0, 0, 0), (2, 0, 4), (1, 2, 0)])
def test_generation_model_edge_cases(robots, tasks, horizon):
    instance = generate_instance(width=1, height=1, robot_count=robots, task_count=tasks,
                                 seed=0, horizon=horizon)
    assert len(instance.robots) == robots and len(instance.tasks) == tasks
    simulation = Simulation(instance, "fifo")
    simulation.run()
    assert simulation.all_tasks_completed == (tasks == 0)


@pytest.mark.parametrize("changes,message", [
    ({"width": 0}, "width"), ({"height": True}, "height"),
    ({"width": 1.5}, "width"), ({"robot_count": -1}, "Nombre de robots"),
    ({"task_count": True}, "Nombre de tâches"), ({"task_count": 1.5}, "Nombre de tâches"),
    ({"seed": -1}, "Graine"), ({"seed": None}, "Graine"), ({"seed": True}, "Graine"),
    ({"horizon": -1}, "horizon"), ({"robot_count": 0}, "Au moins un robot"),
    ({"obstacles": [[0, 0]]}, "aucune case libre"),
    ({"obstacles": [[1, 0]]}, "outside grid"),
    ({"resources": [{"id": "s", "position": [0, 0], "capacity": 0}]}, "capacity"),
    ({"resources": [{"id": "s", "position": [1, 0]}]}, "outside grid"),
])
def test_generation_rejects_impossible_or_invalid_parameters(changes, message):
    parameters = dict(width=1, height=1, robot_count=1, task_count=1, seed=0)
    parameters.update(changes)
    with pytest.raises(ValueError, match=message):
        generate_instance(**parameters)


def test_complex_comparison_operational_differences_and_repeat():
    from taskfleet.metrics import metrics

    instance = load_instance("examples/complex_comparison.json")
    original = copy.deepcopy(instance)
    expected = {"fifo": (154, 340, 437, 0), "cpsat": (114, 279, 179, 0), "cnp": (142, 312, 365, 1919)}
    assignments = []
    for strategy, values in expected.items():
        first, repeat = Simulation(instance, strategy), Simulation(instance, strategy)
        first.run()
        repeat.run()
        first.check_invariants()
        assert first.trace() == repeat.trace()
        measured = metrics(first)
        assert measured["tasks_completed"] == 18 and measured["invalid_decisions"] == 0
        assert tuple(measured[k] for k in ("makespan", "distance", "total_tardiness", "messages")) == values
        awarded = [(e["tick"], e["robot_id"], e["task_id"]) for e in first.events if e["kind"] == "assigned"]
        assert len(awarded) == len({t for _, _, t in awarded}) == 18
        assert all(len(r["holders"]) <= r["capacity"] for s in first.snapshots for r in s["resources"].values())
        assignments.append(tuple(awarded))
    assert len(set(assignments)) == 3
    assert instance == original


