"""MAPF scientific microcases; selectors also support the manual validation guide."""

import copy
from itertools import permutations
import json
from pathlib import Path

import pytest

from taskfleet import Simulation, from_dict as warehouse_instance
from taskfleet.mapf import (Reservations, SearchBudget, _space_time, from_dict,
                            individual_bound, load_problem, main, solve, validate_plan)

ROOT = Path(__file__).resolve().parents[1]


def problem(width=3, height=3, horizon=6, agents=None, obstacles=None):
    return from_dict({"width": width, "height": height, "horizon": horizon,
                      "obstacles": obstacles or [], "agents": agents if agents is not None else [
                          {"id": "a", "start": [0, 1], "goal": [2, 1]},
                          {"id": "b", "start": [1, 0], "goal": [1, 2]}]})


def test_legal_moves_and_invalid_trajectories():
    p = problem(agents=[{"id": "a", "start": [0, 1], "goal": [2, 1]}])
    assert validate_plan(p, {"a": [[0, 1], [0, 1], [1, 1], [2, 1]]})["valid"]
    for path, kind in [([[0, 1], [2, 1]], "illegal_move"),
                       ([[0, 1], [-1, 1], [0, 1], [1, 1], [2, 1]], "illegal_cell"),
                       ([[0, 1], [1, 2], [2, 1]], "illegal_move"),
                       ([[0, 0], [1, 0], [2, 0], [2, 1]], "wrong_start"),
                       ([[0, 1], [1, 1]], "wrong_goal"),
                       ([[True, 1], [1, 1], [2, 1]], "invalid_position")]:
        checked = validate_plan(p, {"a": path})
        assert not checked["valid"]
        assert kind in {v["kind"] for v in checked["violations"]}


def test_vertex_conflict():
    checked = validate_plan(problem(), {"a": [[0, 1], [1, 1], [2, 1]],
                                        "b": [[1, 0], [1, 1], [1, 2]]})
    assert checked["vertex_conflicts"] == 1
    assert checked["edge_conflicts"] == 0
    assert checked["violations"][0]["tick"] == 1


def test_edge_swap_conflict():
    p = problem(2, 2, agents=[{"id": "a", "start": [0, 0], "goal": [1, 0]},
                             {"id": "b", "start": [1, 0], "goal": [0, 0]}])
    checked = validate_plan(p, {"a": [[0, 0], [1, 0]], "b": [[1, 0], [0, 0]]})
    assert checked["edge_conflicts"] == 1 and checked["vertex_conflicts"] == 0
    for algorithm in ("cbs", "oracle", "prioritized"):
        result = solve(p, algorithm)
        assert result["status"] == "SOLVED" and result["validation"]["valid"]
        assert result["validation"]["metrics"]["sum_of_costs"] == 4


def test_wait_required_and_metrics():
    p = problem()
    for algorithm in ("prioritized", "cbs", "oracle"):
        result = solve(p, algorithm)
        metrics = result["validation"]["metrics"]
        assert metrics == {"arrivals": metrics["arrivals"], "sum_of_costs": 5,
                           "distance": 4, "waits": 1, "makespan": 3}
    assert individual_bound(p)["sum_of_costs_lower_bound"] == 4


def test_goal_persistence_and_priority_order():
    p = load_problem(ROOT / "examples/mapf/blocking_goal.json")
    first = solve(p, "prioritized", priorities=["a", "b"])
    second = solve(p, "prioritized", priorities=["b", "a"])
    assert first["status"] == "PRIORITY_FAILED"
    assert not first["infeasible_within_horizon"]
    assert second["status"] == "SOLVED"
    assert second["validation"]["metrics"]["sum_of_costs"] == 4
    for algorithm in ("cbs", "oracle"):
        result = solve(p, algorithm)
        assert result["status"] == "SOLVED" and result["optimal"]
        assert result["validation"]["metrics"]["sum_of_costs"] == 4
    unsafe = {"a": [[1, 1], [1, 0]], "b": [[0, 0], [0, 0], [1, 0], [2, 0]]}
    assert any(v["kind"] == "vertex" and v["tick"] == 2
               for v in validate_plan(p, unsafe)["violations"])


def test_reservations_include_edges_and_persistent_goals():
    reservations = Reservations()
    reservations.reserve([(0, 0), (1, 0)], 4)
    assert ((0, 0), (1, 0), 1) in reservations.edges
    assert ((1, 0), 4) in reservations.vertices
    assert ((1, 0), (1, 0), 4) in reservations.edges


def test_low_level_future_goal_constraint_and_absorbing_goal():
    p = problem(3, 1, agents=[{"id": "a", "start": [0, 0], "goal": [1, 0]}])
    path = _space_time(p, "a", {((1, 0), 3)}, set(), SearchBudget())
    assert path == [(0, 0)] * 4 + [(1, 0)]
    checked = validate_plan(p, {"a": [[0, 0], [1, 0], [2, 0], [1, 0]]})
    assert any(v["kind"] == "left_goal" for v in checked["violations"])


def test_narrow_passage_cbs_and_oracle():
    p = load_problem(ROOT / "examples/mapf/narrow.json")
    cbs, oracle = solve(p, "cbs"), solve(p, "oracle")
    assert cbs["status"] == oracle["status"] == "SOLVED"
    assert cbs["validation"]["metrics"]["sum_of_costs"] == oracle["validation"]["metrics"]["sum_of_costs"]


def test_start_equals_goal_and_obstacle_detour():
    p = problem(3, 2, agents=[{"id": "a", "start": [1, 0], "goal": [1, 0]},
                             {"id": "b", "start": [0, 0], "goal": [2, 0]}])
    for algorithm in ("cbs", "oracle", "prioritized"):
        result = solve(p, algorithm)
        assert result["status"] == "SOLVED"
        assert result["validation"]["metrics"]["arrivals"] == {"a": 0, "b": 4}
        assert result["paths"]["a"] == [[1, 0]]
    p = problem(3, 2, obstacles=[[1, 0]],
                agents=[{"id": "a", "start": [0, 0], "goal": [2, 0]}])
    assert solve(p)["validation"]["metrics"]["distance"] == 4


def test_no_solution_within_horizon_is_not_general_infeasibility():
    p = problem(horizon=2)
    for algorithm in ("cbs", "oracle"):
        result = solve(p, algorithm)
        assert result["status"] == "NO_SOLUTION_WITHIN_HORIZON"
        assert result["infeasible_within_horizon"] and not result["optimal"]
    assert solve(problem(horizon=3))["status"] == "SOLVED"


def test_unsolvable_corridor_and_disconnected_goal():
    p = problem(3, 1, horizon=4, agents=[{"id": "a", "start": [0, 0], "goal": [2, 0]},
                                        {"id": "b", "start": [2, 0], "goal": [0, 0]}])
    assert solve(p)["status"] == solve(p, "oracle")["status"] == "NO_SOLUTION_WITHIN_HORIZON"
    p = problem(3, 1, obstacles=[[1, 0]], agents=[{"id": "a", "start": [0, 0], "goal": [2, 0]}])
    assert solve(p)["status"] == "NO_SOLUTION_WITHIN_HORIZON"
    assert individual_bound(p)["sum_of_costs_lower_bound"] is None


@pytest.mark.parametrize("algorithm", ["prioritized", "cbs", "oracle"])
def test_budget_exhaustion_does_not_claim_infeasible(algorithm):
    result = solve(problem(), algorithm, max_nodes=0)
    assert result["status"] == "BUDGET_EXHAUSTED" and result["reason"] == "max_nodes"
    assert result["total_nodes"] == 0 and not result["infeasible_within_horizon"]
    assert result["paths"] is None and not result["optimal"]


def test_wall_budget_explicit_and_checked():
    result = solve(problem(), seconds=1e-12)
    assert result["status"] == "BUDGET_EXHAUSTED" and result["reason"] == "seconds"


def test_reproducibility():
    p = load_problem(ROOT / "examples/mapf/narrow.json")
    a, b = solve(p), solve(p)
    a.pop("seconds")
    b.pop("seconds")
    assert a == b


def test_empty_problem_zero_horizon():
    p = problem(1, 1, horizon=0, agents=[])
    for algorithm in ("cbs", "prioritized", "oracle"):
        result = solve(p, algorithm)
        assert result["status"] == "SOLVED"
        assert result["validation"]["metrics"]["sum_of_costs"] == 0
    p = problem(1, 1, horizon=0, agents=[{"id": "a", "start": [0, 0], "goal": [0, 0]}])
    assert solve(p)["validation"]["metrics"]["arrivals"] == {"a": 0}


@pytest.mark.parametrize("field,value", [("horizon", -1), ("horizon", True), ("width", False),
                                         ("obstacles", [[0, 0], [0, 0]]), ("unknown", 1)])
def test_strict_problem_validation(field, value):
    data = problem().to_dict()
    data[field] = value
    with pytest.raises(ValueError):
        from_dict(data)


def test_distinct_starts_goals_ids_and_full_priority_permutation():
    for field in ("start", "goal", "id"):
        data = problem().to_dict()
        data["agents"][1][field] = copy.deepcopy(data["agents"][0][field])
        with pytest.raises(ValueError):
            from_dict(data)
    for priorities in (["a"], ["a", "a"], ["a", "z"]):
        with pytest.raises(ValueError, match="every agent exactly once"):
            solve(problem(), "prioritized", priorities=priorities)
    for kwargs in ({"seconds": 0}, {"seconds": float("inf")}, {"max_nodes": True}, {"max_nodes": -1}):
        with pytest.raises(ValueError):
            solve(problem(), **kwargs)


def test_checker_missing_empty_obstacle_and_horizon():
    p = problem(3, 2, horizon=1, obstacles=[[1, 0]],
                agents=[{"id": "a", "start": [0, 0], "goal": [2, 0]}])
    assert not validate_plan(p, None)["valid"]
    assert not validate_plan(p, {})["valid"]
    assert not validate_plan(p, {"a": []})["valid"]
    checked = validate_plan(p, {"a": [[0, 0], [1, 0], [2, 0]]})
    assert {v["kind"] for v in checked["violations"]} == {"horizon", "illegal_cell"}


def test_cbs_matches_exhaustive_oracle_all_two_agent_2x2_cases():
    cells = [(0, 0), (1, 0), (0, 1), (1, 1)]
    for starts in permutations(cells, 2):
        for goals in permutations(cells, 2):
            p = problem(2, 2, horizon=5, agents=[
                {"id": agent, "start": list(start), "goal": list(goal)}
                for agent, start, goal in zip(("a", "b"), starts, goals)])
            cbs, oracle = solve(p, "cbs"), solve(p, "oracle")
            assert cbs["status"] == oracle["status"], (starts, goals)
            assert cbs["validation"]["metrics"]["sum_of_costs"] == oracle["validation"]["metrics"]["sum_of_costs"]


def test_v1_physics_and_paths_unchanged():
    data = {"width": 3, "height": 1, "horizon": 3,
            "robots": [{"id": "a", "position": [0, 0], "capacity": 1},
                       {"id": "b", "position": [2, 0], "capacity": 1}],
            "tasks": [{"id": "a", "pickup": [2, 0], "destination": [2, 0]},
                      {"id": "b", "pickup": [0, 0], "destination": [0, 0]}]}
    simulation = Simulation(warehouse_instance(data))
    simulation.assign("a", "a")
    simulation.assign("b", "b")
    before = simulation.trace()
    solve(problem())
    assert simulation.trace() == before
    simulation.step()
    assert simulation.robots["a"].position == simulation.robots["b"].position == (1, 0)
    assert all(event["from_position"] != event["position"] for event in simulation.events if event["kind"] == "moved")


def test_cli_solve_and_independent_validate(tmp_path, capsys):
    output = tmp_path / "plan.json"
    instance = ROOT / "examples/mapf/crossing.json"
    assert main([str(instance), "--algorithm", "cbs", "--output", str(output)]) == 0
    exported = json.loads(output.read_text())
    assert exported["status"] == "SOLVED"
    assert main([str(instance), "--validate", str(output)]) == 0
    exported["paths"]["a"][1] = [2, 1]
    output.write_text(json.dumps(exported), encoding="utf-8")
    assert main([str(instance), "--validate", str(output)]) == 1
    with pytest.raises(SystemExit) as error:
        main([str(instance), "--horizon", "-1"])
    assert error.value.code == 2
    capsys.readouterr()


def test_json_duplicate_and_oracle_scope_limits(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"width": 1, "width": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate field"):
        load_problem(path)
    with pytest.raises(ValueError, match="joint oracle limited"):
        solve(problem(5, 5), "oracle")


def test_benchmark_budget_priority_sensitivity_and_certificates():
    from scripts.benchmark_mapf import benchmark
    report = benchmark(seeds=(11,), budgets=(1, 20_000))
    assert report["invalid_solutions"] == 0
    assert report["exhaustive_2x2"]["certified"] == 144
    assert not report["exhaustive_2x2"]["mismatches"]
    for row in report["runs"]:
        assert row["total_nodes"] <= row["node_budget"]
    blocking = next(r for r in report["priority_sensitivity"] if r["scenario"] == "blocking_goal")
    assert blocking["solved_orders"] == 2
    narrow = next(r for r in report["paired_oracle_gaps"] if r["scenario"] == "narrow_pockets")
    assert narrow["oracle_optimum"] == 11 and narrow["gaps"]["cbs"] == 0
    assert any(r["status"] == "BUDGET_EXHAUSTED" for r in report["runs"])
    assert all(r["conditional_cost_count"] == r["solved"] for r in report["aggregates"])
