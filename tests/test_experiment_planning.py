"""Adapter checks: a solver plan is not an executed warehouse simulation."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from api import experiment_planning as web
from taskfleet import from_dict, load_instance
from taskfleet.mapf import load_problem

ROOT = Path(__file__).resolve().parents[1]


def parameters(**changes):
    return SimpleNamespace(**{
        "method": "nominal", "compare": False, "solver_budget": 0.2,
        "window": 18, "frequency": 3, "max_policies": 200,
        "priorities": None, "max_nodes": 10_000, "seconds": 2.0, **changes,
    })


@pytest.mark.parametrize("method", ["nominal", "rolling", "robust"])
def test_m7_executes_existing_engine_and_compares_compatible_runs(method):
    instance = load_instance(ROOT / "examples/m7.json")
    scenarios = [[], instance.unavailability]
    result = web.run_m7(parameters(method=method, compare=True), instance, scenarios)
    assert result["milestone"] == "m7" and result["method"] == method
    assert result["execution_status"] == "valid"
    assert result["metrics"]["tasks_completed"] == 3
    assert result["trace"]["current"]["all_tasks_completed"]
    assert len(result["comparison"]) == 2
    if method == "nominal":
        assert result["execution"]["executable"]
        assert all(item["completed_delta"] == 0 for item in result["execution"]["deviations"])
    if method == "rolling":
        assert result["rolling"]["replans"] > 0
        rolling_comparison = next(row for row in result["comparison"] if row["method"] == "rolling")
        for field in ("replans", "fallback_dispatches", "solver_seconds", "total_seconds"):
            assert rolling_comparison["metrics"][field] == result["rolling"][field]
        nominal_comparison = next(row for row in result["comparison"] if row["method"] == "nominal")
        assert nominal_comparison["metrics"]["solver_seconds"] >= 0
    if method == "robust":
        assert len(result["scenario_runs"]) == len(scenarios)
        for row in result["comparison"]:
            assert [run["calendar"] for run in row["scenario_runs"]] == scenarios


def test_m7_infeasible_has_no_fictitious_execution():
    data = load_instance(ROOT / "examples/m7.json").to_dict()
    data["horizon"] = 1
    result = web.run_m7(parameters(), from_dict(data), [[]])
    assert result["execution_status"] == "infeasible"
    assert not result.get("trace") and not result["metrics"]


def test_m7_budget_stop_is_not_infeasibility(monkeypatch):
    monkeypatch.setattr(web, "solve_schedule", lambda *args, **kwargs: {
        "feasible": False, "status": "UNKNOWN"})
    result = web.run_m7(parameters(), load_instance(ROOT / "examples/m7.json"), [[]])
    assert result["execution_status"] == "budget_exhausted"


def test_m7_execution_mismatch_is_not_success(monkeypatch):
    execute = web.execute_schedule

    def mismatched(*args):
        simulation, validation = execute(*args)
        validation["executable"] = False
        return simulation, validation

    monkeypatch.setattr(web, "execute_schedule", mismatched)
    result = web.run_m7(parameters(), load_instance(ROOT / "examples/m7.json"), [[]])
    assert result["execution_status"] == "invalid"


def test_rolling_short_window_keeps_explicit_fifo_fallback():
    result = web.run_m7(parameters(method="rolling", window=1),
                        load_instance(ROOT / "examples/m7.json"), [[]])
    assert result["rolling"]["fallback_dispatches"] > 0
    assert result["execution_status"] == "valid"
    dispatches = result["rolling"]["dispatches"]
    assert sum(row["source"] == "fifo" for row in dispatches) == result["rolling"]["fallback_dispatches"]
    assert [{key: row[key] for key in ("tick", "robot_id", "task_id")} for row in dispatches] == [
        {key: event[key] for key in ("tick", "robot_id", "task_id")}
        for event in result["trace"]["events"] if event["kind"] == "assigned"]


def test_rolling_commitments_are_from_revealed_state_before_replanning():
    result = web.run_m7(parameters(method="rolling", frequency=1),
                        load_instance(ROOT / "examples/m7.json"), [[]])
    snapshots = {row["tick"]: row for row in result["trace"]["snapshots"]}
    for plan in result["rolling"]["plans"]:
        robots = snapshots[plan["start_tick"]]["robots"]
        assert plan["committed"] == [
            {"robot_id": robot_id, "task_id": robot["task_id"]}
            for robot_id, robot in robots.items() if robot["task_id"] is not None]
    assert any(plan["committed"] for plan in result["rolling"]["plans"])
    assert all(row["source"] == "plan" for row in result["rolling"]["dispatches"])


def test_robust_infeasibility_and_truncated_enumeration(monkeypatch):
    for complete, expected in [(True, "infeasible"), (False, "budget_exhausted")]:
        monkeypatch.setattr(web, "optimize_priority", lambda *args, **kwargs: {
            "policy": None, "enumeration_complete": complete, "guarantee": "fixed queues"})
        result = web.run_m7(parameters(method="robust"),
                            load_instance(ROOT / "examples/m7.json"), [[]])
        assert result["execution_status"] == expected
        assert not result.get("trace")


@pytest.mark.parametrize("method", ["prioritized", "cbs"])
def test_m9_paths_checked_and_replay_uses_isolated_problem(method):
    problem = load_problem(ROOT / "examples/mapf/crossing.json")
    result = web.run_m9(parameters(method=method, compare=True), problem)
    assert "trace" not in result
    assert result["problem"] == problem.to_dict()
    assert result["execution_status"] == "valid"
    assert result["plan"]["validation"]["valid"]
    assert result["metrics"]["sum_of_costs"] >= result["metrics"]["distance"]
    assert {row["method"] for row in result["comparison"]} == {"prioritized", "cbs"}
    for agent, path in result["plan"]["paths"].items():
        assert path[0] == list(problem.starts[agent])
        assert path[-1] == list(problem.goals[agent])


def test_m9_exhausted_budget_has_no_fake_metrics_or_paths():
    result = web.run_m9(parameters(method="cbs", max_nodes=0),
                        load_problem(ROOT / "examples/mapf/crossing.json"))
    assert result["execution_status"] == "budget_exhausted"
    assert result["plan"]["paths"] is None and not result["metrics"]


def test_m9_priorities_forwarded_without_modifying_algorithm():
    result = web.run_m9(parameters(method="prioritized", priorities=["b", "a"]),
                        load_problem(ROOT / "examples/mapf/crossing.json"))
    assert result["plan"]["priorities"] == ["b", "a"]
