from dataclasses import fields
import json

import pytest

from taskfleet import Simulation, load_instance
from taskfleet.allocation import Observation
from taskfleet.cnp import RobotAgent, contract_net
from taskfleet.models import Grid, Robot, RobotState, Task


def test_local_offer_and_commitment():
    agent = RobotAgent(Grid(5, 1), Robot("r", (0, 0), 2))
    task = Task("t", (2, 0), (4, 0), load=2, handling_ticks=3)
    assert agent.bid(task) == {"type": "offer", "cost": 10}
    assert agent.accept(task)
    assert not agent.accept(task)
    assert agent.bid(Task("other", (0, 0), (0, 0)))["type"] == "refuse"
    assert agent.execution_state()["commitment"] == "t"


@pytest.mark.parametrize("robot,task,grid,reason", [
    (Robot("r", (0, 0), 1), Task("t", (0, 0), (0, 0), load=2), Grid(1, 1), "capacity"),
    (Robot("r", (0, 0), 1, state=RobotState.PICKING), Task("t", (0, 0), (0, 0)), Grid(1, 1), "busy_or_unavailable"),
    (Robot("r", (0, 0), 1), Task("t", (2, 0), (2, 0)), Grid(3, 1, frozenset({(1, 0)})), "unreachable"),
])
def test_agent_refusals(robot, task, grid, reason):
    agent = RobotAgent(grid, robot)
    assert agent.bid(task) == {"type": "refuse", "reason": reason}
    assert not agent.accept(task)


def test_information_boundary_and_bid_delivery(monkeypatch):
    assert {f.name for f in fields(RobotAgent)} == {"grid", "robot", "commitment"}
    observation = Simulation(load_instance("examples/allocation.json")).observation()
    calls = []
    original = RobotAgent.bid

    def private_bid(agent, task):
        calls.append((agent.robot.id, task.id))
        assert not hasattr(agent, "simulation") and not hasattr(agent, "robots")
        # Agents own their cost: overridden offer must govern the award.
        response = original(agent, task)
        if response["type"] == "offer":
            response["cost"] = 0 if agent.robot.id == "r1" else 100
        return response

    monkeypatch.setattr(RobotAgent, "bid", private_bid)
    result = contract_net(observation)
    assert result["assignments"][0] == ["r1", "t1"]
    assert ("r1", "t1") in calls and ("r2", "t1") in calls
    messages = result["messages"]
    assert messages[0]["type"] == "announce" and messages[0]["sender"] == "r1"
    assert {m["type"] for m in messages} >= {"request", "offer", "award", "accept", "reject", "refuse"}


def test_tie_break_and_no_double_engagement():
    robots = (Robot("b", (0, 0), 1), Robot("a", (0, 0), 1))
    tasks = tuple(Task(str(i), (0, 0), (0, 0)) for i in range(3))
    result = contract_net(Observation(0, Grid(1, 1), robots, tasks))
    assert result["assignments"] == [["a", "0"], ["b", "1"]]
    assert result["messages"][-1]["type"] == "no_award"
    assert len({r for r, t in result["assignments"]}) == 2


def test_no_valid_offer():
    observation = Observation(0, Grid(1, 1), (Robot("r", (0, 0), 1),),
                              (Task("t", (0, 0), (0, 0), load=2),))
    result = contract_net(observation)
    assert not result["assignments"]
    assert [m["type"] for m in result["messages"]] == ["announce", "request", "refuse", "no_award"]


def test_commitment_conflict(monkeypatch):
    monkeypatch.setattr(RobotAgent, "accept", lambda *args: False)
    result = contract_net(Simulation(load_instance("examples/allocation.json")).observation())
    assert not result["assignments"]
    assert any(m.get("reason") == "commitment_conflict" for m in result["messages"])


def test_no_initiator():
    observation = Observation(0, Grid(1, 1), (), (Task("t", (0, 0), (0, 0)),))
    assert contract_net(observation)["status"] == "NO_INITIATOR"


@pytest.mark.parametrize("example", ["simple", "contention", "allocation"])
def test_complete_contract_execution_and_reproducibility(example):
    instance = load_instance(f"examples/{example}.json")
    first, repeat = Simulation(instance, "cnp"), Simulation(instance, "cnp")
    first.run()
    repeat.run()
    first.check_invariants()
    assert first.all_tasks_completed
    assert first.trace() == repeat.trace() == json.loads(json.dumps(first.trace()))
    messages = [e for e in first.events if e["kind"] == "message"]
    assert sum(e["type"] == "accept" for e in messages) == len(instance.tasks)
    assert sum(e["type"] == "execution" for e in messages) == 2 * len(instance.tasks)
    assert len([e for e in first.events if e["kind"] == "assigned"]) == len(instance.tasks)
    if example == "contention":
        assert any(e.get("reason") == "capacity" for e in messages)
