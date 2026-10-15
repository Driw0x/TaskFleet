from scripts.benchmark_marl import benchmark


def test_split_isolation_checkpoint_freezing_and_reproducibility(tmp_path):
    first = benchmark(episodes=2, seeds=(7,), output=tmp_path)
    second = benchmark(episodes=2, seeds=(7,))
    assert first["digests"] == second["digests"]
    digests = [d for group in first["digests"].values() for d in group]
    assert len(digests) == len(set(digests)) == 11
    assert first["checkpoints"] == second["checkpoints"]
    assert first["training"][0]["curve"] == second["training"][0]["curve"]
    assert first["seed_summary"] == second["seed_summary"]
    keys = ("split", "index", "training_seed", "instance_sha256", "policy", "return",
            "completion_rate", "makespan", "distance", "total_tardiness", "invalid_decisions")
    assert [{k: r[k] for k in keys} for r in first["runs"]] == [
        {k: r[k] for k in keys} for r in second["runs"]]
    assert {r["policy"] for r in first["runs"]} == {"random", "heuristic", "q", "fifo", "cpsat", "cnp"}
    for row in first["runs"]:
        if row["completion_rate"] == 1:
            assert row["observed_ticks"] == row["makespan"]
        if row["policy"] == "q" and row["q_known_observations"] == 0:
            paired = next(r for r in first["runs"] if r["policy"] == "random"
                          and r["training_seed"] == row["training_seed"]
                          and r["split"] == row["split"] and r["index"] == row["index"])
            assert all(row[key] == paired[key] for key in ("return", "makespan", "distance"))
    assert (tmp_path / "m10-q-seed-7.json").exists()
    assert (tmp_path / "m10-test-0.json").exists()
    assert (tmp_path / "m10.json").exists()
