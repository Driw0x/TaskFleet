import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from taskfleet import Simulation, from_dict, load_instance
from taskfleet.__main__ import main
from taskfleet.models import RobotState, TaskState

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "contention.json"


def simple_instance(*, handling=1, pickup=None, destination=None, horizon=20, deadline=None):
    return from_dict({
        "width": 3, "height": 1, "horizon": horizon,
        "robots": [{"id": "r1", "position": [0, 0], "capacity": 2},
                   {"id": "r2", "position": [0, 0], "capacity": 1}],
        "tasks": [{"id": "t1", "pickup": pickup or [1, 0],
                   "destination": destination or [2, 0], "load": 2,
                   "handling_ticks": handling, "deadline": deadline},
                  {"id": "t2", "pickup": [0, 0], "destination": [0, 0]}],
    })


def test_full_task_tick_transitions_and_reuse():
    simulation = Simulation(simple_instance())
    robot, task = simulation.robots["r1"], simulation.tasks["t1"]
    simulation.assign("r1", "t1")
    assert simulation.tick == 0
    assert robot.state == RobotState.TO_PICKUP
    assert not robot.available and task.state == TaskState.ASSIGNED
    simulation.step()
    assert robot.position == (1, 0) and robot.state == RobotState.PICKING
    assert task.state == TaskState.ASSIGNED
    simulation.step()
    assert robot.position == (1, 0) and robot.state == RobotState.TO_DELIVERY
    assert task.state == TaskState.PICKED_UP
    simulation.step()
    assert robot.position == (2, 0) and robot.state == RobotState.DELIVERING
    simulation.step()
    assert task.state == TaskState.COMPLETED and task.completed_tick == 4
    assert robot.available and robot.task_id is None
    assert robot.handling_remaining == 0 and not robot.path
    assert [e["kind"] for e in simulation.events] == [
        "assigned", "moved", "collected", "moved", "delivered"]
    simulation.assign("r1", "t2")
    simulation.run()
    assert simulation.all_tasks_completed
    assert simulation.robots["r1"].position == (0, 0)
    simulation.check_invariants()


def test_simple_example():
    simulation = Simulation(load_instance(ROOT / "examples" / "simple.json"))
    simulation.assign("r1", "t1")
    simulation.run()
    assert simulation.all_tasks_completed and simulation.finished
    assert simulation.tasks["t1"].completed_tick == 4
    assert simulation.robots["r1"].position == (2, 0)
    assert simulation.robots["r1"].available


@pytest.mark.parametrize("handling", [1, 2, 4])
def test_coincident_pickup_delivery_requires_two_handlings(handling):
    simulation = Simulation(simple_instance(handling=handling, pickup=[0, 0], destination=[0, 0]))
    simulation.assign("r1", "t1")
    for tick in range(1, 2 * handling + 1):
        simulation.step()
        simulation.check_invariants()
        expected = (TaskState.ASSIGNED if tick < handling else TaskState.PICKED_UP
                    if tick < 2 * handling else TaskState.COMPLETED)
        assert simulation.tasks["t1"].state == expected
    assert simulation.tasks["t1"].completed_tick == 2 * handling
    assert not any(e["kind"] == "moved" for e in simulation.events)


@pytest.mark.parametrize("robot_id,task_id,prior,message", [
    ("unknown", "t1", None, "Unknown robot"),
    ("r1", "unknown", None, "Unknown task"),
    ("r2", "t1", None, "exceeds robot capacity"),
    ("r1", "t2", ("r1", "t1"), "already executing"),
    ("r2", "t2", ("r1", "t2"), "already assigned"),
])
def test_rejected_assignment_is_atomic(robot_id, task_id, prior, message):
    simulation = Simulation(simple_instance())
    if prior:
        simulation.assign(*prior)
    before = simulation.trace()
    with pytest.raises(ValueError, match=message):
        simulation.assign(robot_id, task_id)
    assert simulation.trace() == before


def test_completed_task_cannot_be_reassigned():
    simulation = Simulation(simple_instance())
    simulation.assign("r2", "t2")
    simulation.run(2)
    before = simulation.trace()
    with pytest.raises(ValueError, match="already assigned or completed"):
        simulation.assign("r1", "t2")
    assert simulation.trace() == before


def test_selected_robot_cannot_reach_pickup():
    instance = from_dict({
        "width": 3, "height": 1, "obstacles": [[1, 0]],
        "robots": [{"id": "left", "position": [0, 0], "capacity": 1},
                   {"id": "right", "position": [2, 0], "capacity": 1}],
        "tasks": [{"id": "t", "pickup": [2, 0], "destination": [2, 0]}],
    })
    simulation = Simulation(instance)
    before = simulation.trace()
    with pytest.raises(ValueError, match="pickup is unreachable"):
        simulation.assign("left", "t")
    assert simulation.trace() == before
    simulation.assign("right", "t")
    simulation.run(2)
    assert simulation.all_tasks_completed


@pytest.mark.parametrize("capacity,handling", [(1, 1), (1, 2), (2, 2)])
def test_resource_capacity_waiting_and_release(capacity, handling):
    data = simple_instance(handling=handling, pickup=[0, 0], destination=[0, 0]).to_dict()
    data["tasks"][1]["handling_ticks"] = handling
    data["resources"] = [{"id": "station", "position": [0, 0], "capacity": capacity}]
    simulation = Simulation(from_dict(data))
    # Assignment order does not override lexicographic robot processing.
    simulation.assign("r2", "t2")
    simulation.assign("r1", "t1")
    first = simulation.step()
    resource = simulation.resources["station"]
    if capacity == 1:
        assert simulation.robots["r2"].state == RobotState.WAITING_PICKUP
        assert simulation.robots["r2"].handling_remaining == handling
        assert any(e["kind"] == "waiting" for e in simulation.events)
        # Even handling=1 cannot free a slot for another robot in this tick.
        assert first["tasks"]["t2"]["state"] == "assigned"
    else:
        assert not any(e["kind"] == "waiting" for e in simulation.events)
    if handling > 1:
        assert resource.holders == ({"r1"} if capacity == 1 else {"r1", "r2"})
    else:
        assert resource.holders == set()
    while not simulation.all_tasks_completed:
        simulation.step()
        assert len(resource.holders) <= capacity
        simulation.check_invariants()
    assert not resource.holders
    assert all(r.available and r.resource_id is None for r in simulation.robots.values())
    acquisitions = [e for e in simulation.events if e["kind"] == "resource_acquired"]
    releases = [e for e in simulation.events if e["kind"] == "resource_released"]
    assert len(acquisitions) == len(releases) == 4


def test_delivery_contention():
    instance = from_dict({
        "width": 3, "height": 1,
        "robots": [{"id": "a", "position": [0, 0], "capacity": 1},
                   {"id": "b", "position": [2, 0], "capacity": 1}],
        "tasks": [{"id": "a", "pickup": [0, 0], "destination": [1, 0], "handling_ticks": 2},
                  {"id": "b", "pickup": [2, 0], "destination": [1, 0], "handling_ticks": 2}],
        "resources": [{"id": "delivery", "position": [1, 0]}],
    })
    simulation = Simulation(instance)
    simulation.assign("a", "a")
    simulation.assign("b", "b")
    simulation.run(4)
    assert simulation.robots["a"].state == RobotState.DELIVERING
    assert simulation.robots["b"].state == RobotState.WAITING_DELIVERY
    assert simulation.tasks["b"].state == TaskState.PICKED_UP
    assert simulation.robots["b"].handling_remaining == 2
    simulation.run(5)
    assert not simulation.resources["delivery"].holders
    assert simulation.robots["b"].state == RobotState.WAITING_DELIVERY
    simulation.run(7)
    assert simulation.all_tasks_completed
    assert [simulation.tasks[key].completed_tick for key in ("a", "b")] == [5, 7]


@pytest.mark.parametrize("horizon", [0, 1, 2, 3, 4])
def test_horizon_does_not_complete_unfinished_tasks(horizon):
    simulation = Simulation(simple_instance(horizon=horizon))
    if horizon:
        simulation.assign("r1", "t1")
    simulation.run()
    assert simulation.tick == horizon and simulation.finished
    assert (simulation.tasks["t1"].state == TaskState.COMPLETED) == (horizon == 4)
    before = simulation.trace()
    with pytest.raises(ValueError, match="horizon"):
        simulation.step()
    with pytest.raises(ValueError, match="horizon"):
        simulation.assign("r2", "t2")
    assert simulation.trace() == before
    assert simulation.run() == before["current"]


def test_horizon_preserves_active_reservation():
    data = simple_instance(handling=3, pickup=[0, 0], horizon=1).to_dict()
    data["resources"] = [{"id": "station", "position": [0, 0]}]
    simulation = Simulation(from_dict(data))
    simulation.assign("r1", "t1")
    simulation.run()
    assert simulation.resources["station"].holders == {"r1"}
    assert simulation.robots["r1"].handling_remaining == 2
    assert simulation.tasks["t1"].state == TaskState.ASSIGNED
    assert not any(e["kind"] == "resource_released" for e in simulation.events)
    simulation.check_invariants()


@pytest.mark.parametrize("until", [-1, 21, 1.5, True, "2"])
def test_invalid_run_limit(until):
    simulation = Simulation(simple_instance())
    before = simulation.trace()
    with pytest.raises(ValueError, match="until"):
        simulation.run(until)
    assert simulation.trace() == before


def test_run_cannot_go_backwards():
    simulation = Simulation(simple_instance())
    simulation.run(2)
    before = simulation.trace()
    with pytest.raises(ValueError, match="until"):
        simulation.run(1)
    assert simulation.trace() == before


@pytest.mark.parametrize("deadline,late", [(None, False), (3, True), (4, False), (5, False)])
def test_deadline_is_informative(deadline, late):
    simulation = Simulation(simple_instance(deadline=deadline))
    simulation.assign("r1", "t1")
    simulation.run(4)
    assert simulation.snapshot()["tasks"]["t1"]["late"] == late
    assert simulation.events[-1]["kind"] == "delivered"
    assert simulation.events[-1]["late"] == late


def test_snapshot_and_trace_are_detached():
    instance = simple_instance()
    initial = copy.deepcopy(instance)
    simulation = Simulation(instance)
    snapshot = simulation.snapshot()
    snapshot["robots"]["r1"]["position"][0] = 99
    simulation.assign("r1", "t1")
    state = simulation.step()
    state["robots"]["r1"]["position"][0] = 99
    trace = simulation.trace()
    trace["snapshots"].clear()
    trace["events"].clear()
    trace["instance"]["robots"].clear()
    assert simulation.robots["r1"].position == (1, 0)
    assert simulation.snapshots[-1]["robots"]["r1"]["position"] == [1, 0]
    assert simulation.events and simulation.trace()["instance"]["robots"]
    assert instance == initial


def test_complete_scenario_obstacles_and_reproducibility():
    instance = load_instance(EXAMPLE)
    first = Simulation(instance)
    first.assign("r1", "t1")
    first.assign("r2", "t2")
    first.run()
    assert first.all_tasks_completed and first.finished
    assert [first.tasks[key].completed_tick for key in ("t1", "t2")] == [12, 14]
    assert all(robot.position == (4, 0) and robot.available for robot in first.robots.values())
    assert all(not resource.holders for resource in first.resources.values())
    waits = [e for e in first.events if e["kind"] == "waiting"]
    assert [(e["tick"], e["robot_id"]) for e in waits] == [(2, "r2"), (3, "r2")]
    for event in first.events:
        if event["kind"] == "moved":
            position, previous = tuple(event["position"]), event["from_position"]
            assert first.grid.accessible(position)
            assert abs(position[0] - previous[0]) + abs(position[1] - previous[1]) == 1
    assert len(first.snapshots) == first.horizon + 1
    for state in first.snapshots:
        for resource in state["resources"].values():
            assert len(resource["holders"]) <= resource["capacity"]
    second = Simulation(instance)
    second.assign("r1", "t1")
    second.assign("r2", "t2")
    second.run()
    assert first.trace() == second.trace()
    data = instance.to_dict()
    for field in ("robots", "tasks", "resources", "obstacles"):
        data[field].reverse()
    reordered = Simulation(from_dict(data))
    reordered.assign("r1", "t1")
    reordered.assign("r2", "t2")
    reordered.run()
    assert first.trace() == reordered.trace()


def test_replay_assignments_between_ticks_from_trace():
    original = Simulation(simple_instance())
    original.assign("r1", "t1")
    original.run(4)
    original.assign("r1", "t2")
    original.run(8)
    recorded = json.loads(json.dumps(original.trace()))
    replay = Simulation(from_dict(recorded["instance"]))
    for event in recorded["events"]:
        if event["kind"] == "assigned":
            replay.run(event["tick"])
            replay.assign(event["robot_id"], event["task_id"])
    replay.run(recorded["current"]["tick"])
    assert replay.trace() == recorded


def test_empty_simulation():
    simulation = Simulation(from_dict({"width": 1, "height": 1, "robots": [], "tasks": []}))
    assert simulation.all_tasks_completed and not simulation.finished
    simulation.run()
    assert simulation.finished and not simulation.events


def test_invariant_detects_invalid_position():
    simulation = Simulation(simple_instance())
    simulation.robots["r1"].position = (3, 0)
    with pytest.raises(AssertionError):
        simulation.check_invariants()


def test_cli_complete_trace_and_repeat(tmp_path, capsys):
    path = tmp_path / "trace.json"
    assert main([str(EXAMPLE), "--assign", "r1", "t1", "--assign", "r2", "t2",
                 "--trace", str(path), "--verify-repeat"]) == 0
    output = capsys.readouterr().out
    assert "Initial state:" in output and "Final state:" in output and "Events:" in output
    assert "Reproducibility: identical traces" in output
    trace = json.loads(path.read_text(encoding="utf-8"))
    assert trace["current"]["all_tasks_completed"]
    assert all(not r["holders"] for r in trace["current"]["resources"].values())


def test_cli_invalid_assignment(capsys):
    with pytest.raises(SystemExit) as error:
        main([str(EXAMPLE), "--assign", "r2", "t1"])
    assert error.value.code == 1
    assert "exceeds robot capacity" in capsys.readouterr().err


def test_reproducibility_across_hash_seeds():
    script = (
        "import json; from taskfleet import Simulation, load_instance; "
        "s=Simulation(load_instance('examples/contention.json')); "
        "s.assign('r1','t1'); s.assign('r2','t2'); s.run(); print(json.dumps(s.trace()))"
    )
    outputs = []
    for seed in ("1", "99"):
        environment = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                env=environment, capture_output=True, text=True, check=True)
        outputs.append(json.loads(result.stdout))
    assert outputs[0] == outputs[1]
