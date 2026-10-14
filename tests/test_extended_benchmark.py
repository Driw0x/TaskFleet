import csv
import json

import pytest

from scripts.benchmark_extended import (CONFIGURATIONS, deterministic_runs, make_instance,
                                       paired_differences, run_experiments, summarize)
from taskfleet import Simulation, load_instance
from taskfleet.benchmark import run_benchmark
from taskfleet.metrics import metrics


def test_controlled_generator_identity_and_real_seed_variation():
    base, _ = make_instance("base", 11)
    repeat, _ = make_instance("base", 11)
    alternate, _ = make_instance("base", 23)
    assert base.to_dict() == repeat.to_dict()
    assert base.to_dict()["tasks"] != alternate.to_dict()["tasks"]
    for configuration in CONFIGURATIONS:
        instance, parameters = make_instance(configuration, 11)
        assert len(instance.robots) == parameters["robots"]
        assert len(instance.tasks) == parameters["tasks"]
        Simulation(instance).check_invariants()
    for configuration, changed in (("obstacles", "obstacles"), ("arrivals", "tasks"),
                                   ("downtime", "unavailability"), ("robots8", "robots"),
                                   ("short", "horizon")):
        control = base.to_dict()
        variant = make_instance(configuration, 11)[0].to_dict()
        assert {k: v for k, v in variant.items() if k != changed} == {k: v for k, v in control.items() if k != changed}
    free = make_instance("shared_free", 11)[0].to_dict()
    constrained = make_instance("shared1", 11)[0].to_dict()
    assert {k: v for k, v in free.items() if k != "resources"} == {k: v for k, v in constrained.items() if k != "resources"}


def test_diagnostics_match_engine_and_message_partition():
    instance = load_instance("examples/allocation.json")
    report = run_benchmark({"allocation": instance}, controls=False, diagnostics=True)
    for row in report["runs"]:
        direct = Simulation(instance, row["strategy"])
        direct.run()
        expected = metrics(direct)
        for key, value in expected.items():
            if key != "strategy_seconds":
                assert row[key] == value
        assert row["total_seconds"] == pytest.approx(row["setup_seconds"] + row["simulation_seconds"] + row["metrics_seconds"])
        assert row["simulation_seconds"] >= row["strategy_seconds"] >= 0
        assert row["messages"] == row["direct_transmissions"] + row["self_messages"] + row["broadcasts"]
        assert row["solver_calls"] == sum(row["solver_phase_statuses"].values())
    assert report["runs"][1]["solver_calls"] == 2
    assert report["runs"][2]["self_messages"] > 0
    assert "trace_sha256" not in run_benchmark({"allocation": instance})["runs"][0]


def test_seed_aggregation_does_not_treat_repetitions_as_independent():
    rows = [{"scenario": f"base/seed-{seed}", "configuration": "base", "seed": seed,
             "strategy": "fifo", "repetition": repetition, "status": "incomplete", "error": None,
             "distance": value, "makespan": None} for seed, values in ((11, (2, 4)), (23, (8, 10)))
            for repetition, value in enumerate(values, 1)]
    result = summarize(rows)[0]
    assert result["runs"] == 2 and result["simulations"] == 4
    assert result["metrics"]["distance"] == {"mean": 6, "stddev": pytest.approx(18 ** .5),
                                             "count": 2, "median": 6, "min": 3, "max": 9}
    assert result["metrics"]["makespan"]["count"] == 0
    assert result["metrics"]["makespan"]["mean"] is None


def test_small_experiment_export_repeat_incomplete_and_paired_effects(tmp_path):
    from taskfleet.benchmark import export_results
    report = run_experiments([11, 23], repeats=2, configurations=["small", "short", "base", "empty"],
                             include_reference=False)
    assert len(report["scenarios"]) == 7 and len(report["runs"]) == 42
    assert not report["reproducibility_mismatches"]
    assert all(r["makespan"] is None for r in report["runs"] if r["status"] == "incomplete")
    for scenario in report["scenarios"]:
        assert len({r["instance_sha256"] for r in report["runs"] if r["scenario"] == scenario}) == 1
    assert any(p["kind"] == "factor" for p in paired_differences(report["runs"]))
    export_results(report, json_path=tmp_path / "report.json", csv_path=tmp_path / "runs.csv")
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == report
    with (tmp_path / "runs.csv").open(encoding="utf-8", newline="") as stream:
        assert len(list(csv.DictReader(stream))) == 42
    changed = json.loads(json.dumps(report))
    for row in changed["runs"]:
        row["strategy_seconds"] = -1
    assert deterministic_runs(changed) == deterministic_runs(report)
    changed["runs"][0]["distance"] += 1
    assert deterministic_runs(changed) != deterministic_runs(report)


def test_real_solver_budget_exhaustion_is_visible():
    instance, _ = make_instance("base", 11)
    report = run_benchmark({"base": instance}, solver_budget=1e-8, controls=False, diagnostics=True)
    cpsat = report["runs"][1]
    assert cpsat["status"] == "incomplete" and cpsat["makespan"] is None
    assert cpsat["solver_phase_statuses"] == {"UNKNOWN": instance.horizon}
    assert cpsat["invalid_decisions"] == 0
