import copy

import pytest

from taskfleet import Simulation, from_dict, load_instance


@pytest.mark.parametrize("strategy", ["fifo", "cpsat", "cnp"])
def test_arrivals_suspend_resume_resource_and_repeat(strategy):
    instance = load_instance("examples/dynamic.json")
    simulation = Simulation(instance, strategy)
    assert [t.id for t in simulation.observation().tasks] == ["t1"]
    simulation.run(2)
    assert simulation.resources["pickup"].holders == {"r1"}
    remaining = simulation.robots["r1"].handling_remaining
    simulation.run(5)
    assert simulation.robots["r1"].unavailable
    assert simulation.robots["r1"].handling_remaining == remaining
    assert simulation.resources["pickup"].holders == {"r1"}
    assert not any(e["kind"] in {"moved", "collected", "delivered", "resource_acquired", "resource_released"}
                   and e.get("robot_id") == "r1" and 3 <= e["tick"] < 6 for e in simulation.events)
    simulation.run(6)
    assert not simulation.robots["r1"].unavailable
    assert simulation.robots["r1"].handling_remaining == remaining - 1
    simulation.run()
    assert simulation.all_tasks_completed
    for event in simulation.events:
        if event["kind"] == "assigned":
            assert event["tick"] >= instance.tasks[event["task_id"]].arrival_tick
    assert [(e["tick"], e["task_id"]) for e in simulation.events if e["kind"] == "task_arrived"] == [(3, "t2"), (4, "t3")]
    repeat = Simulation(instance, strategy)
    repeat.run()
    assert repeat.trace() == simulation.trace()
    simulation.check_invariants()


def test_illegal_manual_actions_are_atomic():
    simulation = Simulation(load_instance("examples/dynamic.json"))
    before = simulation.trace()
    with pytest.raises(ValueError, match="has not arrived"):
        simulation.assign("r2", "t2")
    assert simulation.trace() == before
    simulation.run(3)
    before = simulation.trace()
    with pytest.raises(ValueError, match="temporarily unavailable"):
        simulation.assign("r1", "t1")
    assert simulation.trace() == before


@pytest.mark.parametrize("strategy", ["fifo", "cpsat", "cnp"])
def test_future_information_not_disclosed(strategy):
    data = load_instance("examples/dynamic.json").to_dict()
    alternate = copy.deepcopy(data)
    alternate["unavailability"][0]["start_tick"] = 10
    alternate["unavailability"][0]["end_tick"] = 15
    alternate["tasks"][1]["arrival_tick"] = 20
    first = Simulation(from_dict(data), strategy)
    second = Simulation(from_dict(alternate), strategy)
    assert first.observation() == second.observation()
    assert not hasattr(first.observation(), "unavailability")
    first.run(2)
    second.run(2)
    assert first.events == second.events
    assert all(m.get("task_id") not in {"t2", "t3"} for m in first.events)


@pytest.mark.parametrize("state", ["idle", "moving", "handling"])
def test_suspension_in_each_activity(state):
    data = {"width": 4, "height": 1, "horizon": 12,
            "robots": [{"id": "r", "position": [0, 0], "capacity": 1}],
            "tasks": [{"id": "t", "pickup": [0 if state == "handling" else 2, 0],
                       "destination": [3, 0], "handling_ticks": 2}],
            "unavailability": [{"robot_id": "r", "start_tick": 2, "end_tick": 4}]}
    simulation = Simulation(from_dict(data))
    if state != "idle":
        simulation.assign("r", "t")
    simulation.step()
    robot = copy.deepcopy(simulation.robots["r"])
    simulation.run(3)
    assert simulation.robots["r"].position == robot.position
    assert simulation.robots["r"].path == robot.path
    assert simulation.robots["r"].handling_remaining == robot.handling_remaining
    assert simulation.robots["r"].unavailable
    simulation.run(4)
    assert not simulation.robots["r"].unavailable
    simulation.check_invariants()


def test_initial_and_adjacent_unavailability_periods():
    data = load_instance("examples/simple.json").to_dict()
    data["unavailability"] = [
        {"robot_id": "r1", "start_tick": 0, "end_tick": 2},
        {"robot_id": "r1", "start_tick": 2, "end_tick": 4}]
    simulation = Simulation(from_dict(data), "fifo")
    assert not simulation.robots["r1"].available
    simulation.run(3)
    assert not any(e["kind"] == "assigned" for e in simulation.events)
    simulation.run(5)
    assert [e["tick"] for e in simulation.events if e["kind"] == "robot_resumed"] == [4]
    assert [e["tick"] for e in simulation.events if e["kind"] == "assigned"] == [4]


@pytest.mark.parametrize("period,message", [
    ({"robot_id": "bad", "start_tick": 1, "end_tick": 2}, "unknown robot"),
    ({"robot_id": "r1", "start_tick": True, "end_tick": 2}, "integer"),
    ({"robot_id": "r1", "start_tick": 2, "end_tick": 2}, "greater"),
    ({"robot_id": "r1", "start_tick": 4, "end_tick": 7}, "overlapping"),
])
def test_unavailability_validation(period, message):
    data = load_instance("examples/dynamic.json").to_dict()
    data["unavailability"].append(period)
    with pytest.raises(ValueError, match=message):
        from_dict(data)


@pytest.mark.parametrize("arrival", [-1, True, 1.5])
def test_arrival_validation(arrival):
    data = load_instance("examples/simple.json").to_dict()
    data["tasks"][0]["arrival_tick"] = arrival
    with pytest.raises(ValueError, match="arrival_tick"):
        from_dict(data)


def test_event_order_and_horizon():
    data = load_instance("examples/dynamic.json").to_dict()
    data["horizon"] = 3
    simulation = Simulation(from_dict(data), "cnp")
    simulation.run()
    events = [e["kind"] for e in simulation.events if e["tick"] == 3]
    assert events[:2] == ["task_arrived", "robot_unavailable"]
    assert simulation.tasks["t2"].robot_id is None
    assert simulation.tasks["t3"].state == "scheduled"
    assert simulation.resources["pickup"].holders == {"r1"}
    assert from_dict(data).to_dict() == from_dict(from_dict(data).to_dict()).to_dict()
