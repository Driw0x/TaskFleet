from copy import deepcopy
from dataclasses import fields
import json

import pytest

from taskfleet import Simulation, from_dict, load_instance
from taskfleet.allocation import Observation, cpsat
from taskfleet.dcop import (LocalAgent, assignment_quality, compare_snapshot, distributed,
                            exhaustive, local_agents, main)
from taskfleet.models import Grid, Robot, RobotState, Task
from taskfleet.ollama import OllamaClient, OllamaConfig
from taskfleet import ollama


def symmetric(agents=2, tasks=2):
    return Observation(0, Grid(1, 1),
                       tuple(Robot(f"r{i}", (0, 0), 1) for i in range(agents)),
                       tuple(Task(f"t{i}", (0, 0), (0, 0)) for i in range(tasks)))


def without_time(result):
    result = deepcopy(result)
    result.pop("total_seconds")
    return result


def test_local_domains_capacities_unavailability_and_reachability():
    observation = Observation(0, Grid(3, 1, frozenset({(1, 0)})),
                              (Robot("small", (0, 0), 1), Robot("large", (0, 0), 2),
                               Robot("busy", (0, 0), 2, RobotState.PICKING),
                               Robot("unavailable", (0, 0), 2, unavailable=True)),
                              (Task("light", (0, 0), (0, 0)),
                               Task("heavy", (0, 0), (0, 0), load=2),
                               Task("unreachable", (2, 0), (2, 0))))
    domains = {agent.robot.id: agent.domain for agent in local_agents(observation)}
    assert domains == {"busy": (None,), "large": (None, "heavy", "light"),
                       "small": (None, "light"), "unavailable": (None,)}
    result = distributed(observation)
    assert result["neighbors"]["small"] == ["large"]
    assert result["neighbors"]["busy"] == []
    assert result["final_quality"]["feasible"]


def test_observations_do_not_leak_other_robot_costs_or_future():
    instance = from_dict({"width": 2, "height": 1, "horizon": 10,
                          "robots": [{"id": "r1", "position": [0, 0], "capacity": 1},
                                     {"id": "r2", "position": [1, 0], "capacity": 1}],
                          "tasks": [{"id": "now", "pickup": [0, 0], "destination": [0, 0]},
                                    {"id": "secret", "pickup": [1, 0], "destination": [1, 0],
                                     "arrival_tick": 5}],
                          "unavailability": [{"robot_id": "r2", "start_tick": 2, "end_tick": 4}]})
    agents = local_agents(Simulation(instance).observation())
    assert {field.name for field in fields(LocalAgent)} == {
        "grid", "robot", "tasks", "tick", "costs", "neighbors", "received", "value"}
    first = agents[0].llm_observation()
    assert first["own_costs"] == {"now": 2}
    assert "secret" not in json.dumps(first) and "unavailability" not in first
    assert "robots" not in first and "other_costs" not in first
    agents[1].robot.position = (99, 99)
    assert first == agents[0].llm_observation()
    first["own_costs"]["now"] = -100
    assert agents[0].costs["now"] == 2


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_exhaustive_reference_matches_cpsat(count):
    observation = symmetric(3, count)
    exact = exhaustive(observation)
    cp = cpsat(observation)
    assert exact["optimal"] and exact["states_checked"] == exact["states"]
    assert (len(exact["assignments"]), exact["estimated_cost"]) == (
        len(cp["assignments"]), cp["estimated_cost"])


def test_exhaustive_state_budget_never_claims_proof():
    result = exhaustive(symmetric(), max_states=1)
    assert not result["optimal"] and result["status"] == "STATE_BUDGET"
    assert result["states_checked"] == 1 and result["assignments"] == []


def test_distributed_confirmation_claims_resolve_conflicts_without_engine_repair():
    result = distributed(symmetric(), rounds=0)
    assert result["raw_quality"]["conflict_pairs"] == 1
    assert not result["raw_quality"]["feasible"]
    assert result["assignments"] == [["r0", "t0"]]
    assert result["final_quality"]["feasible"]
    assert result["confirmation_abandonments"] == 1
    assert result["confirmation_transmissions"] == 4
    assert {message["type"] for message in result["messages"]} == {"domain", "claim", "confirmed"}
    # Winner depends only on messages and own ID, not private costs.
    agent = LocalAgent(Grid(1, 1), Robot("b", (0, 0), 1), (Task("t", (0, 0), (0, 0)),), 0)
    agent.value, agent.received = "t", {"a": "t"}
    assert agent.confirm() is None
    agent.received = {"z": "t"}
    assert agent.confirm() == "t"


def test_dsa_oscillation_and_no_false_convergence_at_probability_zero():
    oscillating = distributed(symmetric(), rounds=4, probability=1, seed=3)
    assert oscillating["stop_reason"] == "ROUND_BUDGET"
    assert oscillating["oscillations"] == 3
    assert oscillating["rounds"] == 4 and oscillating["final_quality"]["cardinality"] == 1
    frozen = distributed(symmetric(), rounds=3, probability=0)
    assert frozen["stop_reason"] == "ROUND_BUDGET"
    assert all(row["changed"] == 0 and row["strict_improvements"] > 0 for row in frozen["history"])


def test_ties_retain_current_choice_and_local_stability_is_explicit():
    agent = local_agents(symmetric(1, 2))[0]
    agent.value = "t1"
    assert agent.best_value(10, 100) == ("t1", False)
    result = distributed(symmetric(1, 2))
    assert result["stop_reason"] == "LOCAL_OPTIMUM" and result["rounds"] == 1


@pytest.mark.parametrize("budget,rounds,assignments", [(0, 0, []), (5, 0, []), (6, 0, [["r0", "t0"]]),
                                                     (8, 1, [["r0", "t1"]])])
def test_transmission_budget_reserves_confirmation(budget, rounds, assignments):
    result = distributed(symmetric(), rounds=5, probability=1, message_budget=budget)
    assert result["transmissions"] <= budget
    assert result["rounds"] == rounds and result["assignments"] == assignments
    assert result["stop_reason"] == "COMMUNICATION_BUDGET"
    assert result["minimum_confirmation_budget"] == 6
    assert result["final_quality"]["feasible"]


@pytest.mark.parametrize("options", [{"rounds": -1}, {"rounds": True}, {"message_budget": -1},
                                      {"seed": -1}, {"probability": -0.1},
                                      {"probability": float("nan")}, {"probability": True}])
def test_invalid_dsa_parameters(options):
    with pytest.raises(ValueError):
        distributed(symmetric(), **options)


def test_empty_agents_or_idle_only_domain():
    for observation in (symmetric(0, 1), symmetric(2, 0)):
        result = distributed(observation)
        assert result["assignments"] == [] and result["final_quality"]["feasible"]


@pytest.mark.parametrize("assignments,error", [
    ([["r0", "t0"], ["r1", "t0"]], "duplicate_task"),
    ([["r0", "t0"], ["r0", "t1"]], "duplicate_robot"),
    ([["unknown", "t0"]], "unknown_id"),
])
def test_independent_checker_rejects_invalid_assignments(assignments, error):
    result = assignment_quality(symmetric(), assignments)
    assert not result["feasible"] and error in result["errors"]


def test_dsa_seed_reproduces_messages_and_values():
    observation = symmetric(4, 3)
    first = distributed(observation, seed=11, rounds=12)
    second = distributed(observation, seed=11, rounds=12)
    assert without_time(first) == without_time(second)


def test_comparison_proofs_and_cost_gap_only_at_equal_cardinality():
    comparison = compare_snapshot(symmetric(), rounds=0)
    assert comparison["reference_proven"]
    for name, result in comparison["methods"].items():
        assert result["quality"]["feasible"] and result["total_seconds"] >= 0
        assert result["cardinality_gap"] == int(name == "dsa")
    assert comparison["methods"]["dsa"]["cost_gap_at_optimal_cardinality"] is None
    assert comparison["methods"]["exhaustive"]["cost_gap_at_optimal_cardinality"] == 0


def test_final_assignments_execute_in_v1_without_rejections():
    instance = load_instance("examples/allocation.json")
    simulation = Simulation(instance)
    result = distributed(simulation.observation(), seed=11)
    for robot, task in result["assignments"]:
        simulation.assign(robot, task)
    simulation.run()
    simulation.check_invariants()
    assert simulation.all_tasks_completed and simulation.invalid_decisions == 0


def test_llm_local_decisions_use_messages_and_confirmation(monkeypatch):
    payloads = []

    def response(url, timeout, payload):
        payloads.append(json.loads(payload["messages"][1]["content"]))
        return {"message": {"content": '{"action":"t0"}'}}

    monkeypatch.setattr(ollama, "_request", response)
    client = OllamaClient(OllamaConfig(model="mock", call_budget=4))
    result = distributed(symmetric(), rounds=2, llm=client)
    assert result["llm_metrics"]["calls"] == 2
    assert result["stop_reason"] == "UNCHANGED_LLM"
    assert result["raw_quality"]["conflict_pairs"] == 1
    assert result["final_quality"]["feasible"] and len(result["assignments"]) == 1
    for payload in payloads:
        observation = payload["observation"]
        assert len(observation["messages"]) == 1
        assert set(observation["messages"][0]) == {"sender", "action"}
        assert "own_costs" in observation and "robots" not in observation
        assert set(payload["allowed_actions"]) == {None, "t0", "t1"}


def test_llm_invalid_action_and_budget_fall_back_to_idle(monkeypatch):
    monkeypatch.setattr(ollama, "_request", lambda *args: {"message": {"content": '{"action":"bad"}'}})
    client = OllamaClient(OllamaConfig(model="mock", call_budget=1))
    result = distributed(symmetric(), rounds=2, llm=client)
    assert result["assignments"] == [] and result["final_quality"]["feasible"]
    assert result["llm_metrics"]["calls"] == 1
    assert result["llm_metrics"]["errors"]["invalid_action"] == 1
    assert result["llm_metrics"]["errors"]["budget_exhausted"] >= 1


def test_cli_comparison_execution_json_and_invalid_budget(tmp_path, capsys):
    destination = tmp_path / "dcop.json"
    assert main(["examples/allocation.json", "--compare", "--execute", "--seed", "11",
                 "--output", str(destination)]) == 0
    result = json.loads(destination.read_text(encoding="utf-8"))
    assert result["execution"]["current"]["all_tasks_completed"]
    assert result["execution"]["invalid_decisions"] == 0
    assert set(result["methods"]) == {"fifo", "cpsat", "cnp", "dsa", "exhaustive"}
    capsys.readouterr()
    with pytest.raises(SystemExit) as error:
        main(["examples/allocation.json", "--message-budget", "-1"])
    assert error.value.code == 1


def test_reproducible_benchmark_preserves_known_failures():
    from scripts.benchmark_dcop import run_experiments
    report = run_experiments(seeds=(), dsa_seeds=(0,), rounds=(0, 4),
                             probabilities=(1,), communication_budgets=(0, 8))
    assert len(report["instances"]) == 2
    assert all(row["feasible"] for row in report["runs"])
    oscillation = [row for row in report["runs"] if row["scenario"] == "symmetric_oscillation"
                   and row["method"] == "dsa" and row.get("round_budget") == 4
                   and row.get("experiment") == "parameter_sweep"][0]
    assert oscillation["oscillations"] == 3 and oscillation["cardinality_gap"] == 1
    trap = [row for row in report["runs"] if row["scenario"] == "capacity_trap"
            and row["method"] == "fifo"][0]
    assert trap["cardinality_gap"] == 1
    assert all(row["transmissions"] <= row["message_budget"] for row in report["runs"]
               if "message_budget" in row)
