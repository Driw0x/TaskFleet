import csv
import json

import pytest

from taskfleet import Simulation, from_dict, load_instance
from taskfleet.benchmark import aggregate, export_results, instance_digest, main, run_benchmark
from taskfleet.metrics import metrics


def deterministic(row):
    return {key: value for key, value in row.items() if key != "strategy_seconds"}


def test_simple_metrics_and_units():
    simulation = Simulation(load_instance("examples/simple.json"), "fifo")
    simulation.run()
    measured = metrics(simulation)
    assert measured["tasks_completed"] == 1 and measured["completion_rate"] == 1
    assert measured["makespan"] == measured["last_completion_tick"] == 4
    assert measured["observed_ticks"] == 6 and measured["distance"] == 2
    assert measured["robot_utilization"] == {"r1": 4 / 6}
    assert measured["productive_robot_ticks"] == 4
    assert measured["messages"] == measured["invalid_decisions"] == 0
    assert measured["strategy_seconds"] >= 0


@pytest.mark.parametrize("horizon", [0, 1, 3, 4])
def test_incomplete_horizons(horizon):
    data = load_instance("examples/simple.json").to_dict()
    data["horizon"] = horizon
    simulation = Simulation(from_dict(data), "fifo")
    simulation.run()
    measured = metrics(simulation)
    assert measured["makespan"] == (4 if horizon == 4 else None)
    assert measured["tasks_completed"] == (1 if horizon == 4 else 0)
    assert measured["productive_robot_ticks"] == horizon
    assert measured["robot_utilization"]["r1"] == (1 if horizon else 0)


def test_empty_metrics():
    simulation = Simulation(from_dict({"width": 1, "height": 1, "robots": [], "tasks": [], "horizon": 0}), "fifo")
    measured = metrics(simulation)
    assert measured["tasks_total"] == measured["makespan"] == measured["distance"] == 0
    assert measured["completion_rate"] == 1.0
    assert measured["last_completion_tick"] is None
    assert measured["mean_robot_utilization"] == 0.0


def test_tardiness_and_unfinished_lower_bound():
    data = load_instance("examples/simple.json").to_dict()
    data["tasks"][0]["deadline"] = 2
    simulation = Simulation(from_dict(data), "fifo")
    simulation.run(3)
    measured = metrics(simulation)
    assert measured["total_tardiness"] == 0
    assert measured["unfinished_tardiness_lower_bound"] == 1
    simulation.run()
    measured = metrics(simulation)
    assert measured["task_tardiness"] == {"t1": 2}
    assert measured["total_tardiness"] == 2 and measured["late_completed_tasks"] == 1


def test_messages_waiting_suspension_and_distance():
    simulation = Simulation(load_instance("examples/dynamic.json"), "cnp")
    simulation.run()
    measured = metrics(simulation)
    assert measured["messages"] == sum(measured["message_types"].values())
    assert measured["message_types"]["execution"] == 6
    assert measured["unavailable_robot_ticks"] == 3
    assert measured["waiting_robot_ticks"] > 0
    assert measured["distance"] == sum(e["kind"] == "moved" for e in simulation.events)
    assert all(0 <= value <= 1 for value in measured["robot_utilization"].values())
    assert measured["productive_robot_ticks"] + measured["waiting_robot_ticks"] + measured["unavailable_robot_ticks"] <= 60


def test_invalid_strategy_proposals_counted_and_refused(monkeypatch):
    monkeypatch.setattr("taskfleet.engine.allocate", lambda *args:
                        {"assignments": [["r1", "t1"], ["r1", "t1"], ["unknown", "t1"]]})
    simulation = Simulation(load_instance("examples/simple.json"), "fifo")
    simulation.step()
    assert metrics(simulation)["invalid_decisions"] == 2
    assert sum(e["kind"] == "assigned" for e in simulation.events) == 1
    simulation.check_invariants()


def test_scenario_identity_repetitions_and_aggregation():
    instance = load_instance("examples/allocation.json")
    report = run_benchmark({"allocation": instance}, repeats=2)
    assert len(report["runs"]) == 6
    assert {r["instance_sha256"] for r in report["runs"]} == {instance_digest(instance)}
    for strategy in ("fifo", "cpsat", "cnp"):
        rows = [r for r in report["runs"] if r["strategy"] == strategy]
        left, right = map(deterministic, rows)
        left.pop("repetition")
        right.pop("repetition")
        assert left == right
        aggregate_row = next(r for r in report["aggregates"] if r["strategy"] == strategy)
        assert aggregate_row["complete_runs"] == aggregate_row["runs"] == 2
        assert aggregate_row["metrics"]["distance"]["stddev"] == 0
    assert {r["strategy"]: r["makespan"] for r in report["runs"]} == {"fifo": 6, "cpsat": 2, "cnp": 2}


def test_paired_perturbations_and_same_constraints():
    original = load_instance("examples/dynamic.json")
    report = run_benchmark({"dynamic": original})
    assert len(report["scenarios"]) == 3
    assert len(report["perturbation_impacts"]) == 6
    for instance_data in report["scenarios"].values():
        for key in ("width", "height", "robots", "resources", "horizon", "seed"):
            assert instance_data[key] == original.to_dict()[key]
    assert {p["perturbation"] for p in report["perturbation_impacts"]} == {"arrivals", "unavailability"}
    assert all(p["delta"]["makespan"] is not None for p in report["perturbation_impacts"])
    repeat = run_benchmark({"dynamic": original})
    assert list(map(deterministic, report["runs"])) == list(map(deterministic, repeat["runs"]))
    assert report["perturbation_impacts"] == repeat["perturbation_impacts"]


def test_failure_and_incomplete_identification(monkeypatch):
    data = load_instance("examples/simple.json").to_dict()
    data["horizon"] = 1
    report = run_benchmark({"short": from_dict(data)})
    assert all(r["status"] == "incomplete" and r["makespan"] is None for r in report["runs"])
    assert all(r["metrics"]["makespan"]["count"] == 0 for r in report["aggregates"])
    monkeypatch.setattr("taskfleet.benchmark.Simulation.run", lambda *args: (_ for _ in ()).throw(RuntimeError("test failure")))
    report = run_benchmark({"failed": from_dict(data)})
    assert all(r["status"] == "failed" and "test failure" in r["error"] for r in report["runs"])
    assert all(r["failed_runs"] == 1 for r in report["aggregates"])


def test_exports_and_cli(tmp_path, capsys):
    json_path, csv_path = tmp_path / "report.json", tmp_path / "report.csv"
    assert main(["examples/allocation.json", "--repeats", "2", "--json", str(json_path), "--csv", str(csv_path)]) == 0
    report = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(report["runs"]) == 6 and "makespan=2" in capsys.readouterr().out
    with csv_path.open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    assert len(rows) == 6 and float(rows[0]["completion_rate"]) == 1.0
    assert json.loads(rows[0]["robot_utilization"])
    export_results(report, json_path=json_path)
    assert json.loads(json_path.read_text(encoding="utf-8")) == report


def test_aggregation_known_samples():
    rows = [{"scenario": "s", "strategy": "fifo", "repetition": i, "status": "complete",
             "error": None, "distance": distance} for i, distance in enumerate((2, 4))]
    result = aggregate(rows)[0]
    assert result["metrics"]["distance"]["mean"] == 3
    assert result["metrics"]["distance"]["stddev"] == pytest.approx(2 ** 0.5)
    assert "error" not in result["metrics"]


@pytest.mark.parametrize("repeats", [0, -1, True, 1.5])
def test_invalid_repeats(repeats):
    with pytest.raises(ValueError, match="repeats"):
        run_benchmark({"s": load_instance("examples/simple.json")}, repeats=repeats)
