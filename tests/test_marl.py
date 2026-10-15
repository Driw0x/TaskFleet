from copy import deepcopy
import json
import random

import pytest

from taskfleet import Simulation, from_dict, load_instance
from taskfleet.marl import AllocationEnv, QPolicy, main, run_episode, train


def micro(horizon=8):
    return from_dict({"width": 2, "height": 1, "horizon": horizon,
                      "robots": [{"id": r, "position": [0, 0], "capacity": 1}
                                 for r in ("a", "b")],
                      "tasks": [{"id": "t", "pickup": [0, 0], "destination": [1, 0]}]})


def test_reset_transitions_match_manual_engine():
    env = AllocationEnv(micro())
    replay = Simulation(micro())
    replay.assign("a", "t")
    observations, reward, done, truncated, info = env.step({"a": "t"})
    replay.step()
    assert env.simulation.trace() == replay.trace()
    assert reward == -1 and not done and not truncated
    assert observations["a"]["legal_actions"] == [None]
    while not env.terminated:
        env.step({})
        replay.step()
        assert env.simulation.trace() == replay.trace()
    assert env.rewards == [-1, -1, 0]
    assert sum(env.rewards) == -2
    with pytest.raises(ValueError, match="ended"):
        env.step({})
    assert env.reset(17) == AllocationEnv(micro()).reset(17)
    assert env.conflicts == 0 and env.decisions == []


def test_local_observations_hide_other_robots_and_future():
    data = micro().to_dict()
    future = deepcopy(data)
    future["tasks"].append({"id": "secret", "pickup": [1, 0], "destination": [0, 0],
                            "arrival_tick": 7})
    future["unavailability"] = [{"robot_id": "b", "start_tick": 5, "end_tick": 7}]
    future["robots"][1]["capacity"] = 3
    first, second = AllocationEnv(from_dict(data)), AllocationEnv(from_dict(future))
    assert first.observations()["a"] == second.observations()["a"]
    text = json.dumps(second.observations()["a"])
    assert "secret" not in text and "unavailability" not in text
    first.step({"a": "t"})
    second.step({"a": "t"})
    assert first.observations()["a"] == second.observations()["a"]
    assert first.rewards == second.rewards


def test_illegal_actions_conflicts_and_busy_mask():
    env = AllocationEnv(micro())
    _, _, _, _, info = env.step({"a": "t", "b": "t"})
    assert info["accepted"] == [["a", "t"]] and info["conflicts"] == ["b"]
    assert env.conflicts == 1 and env.simulation.invalid_decisions == 0
    _, _, _, _, info = env.step({"a": "t", "b": "forbidden"})
    assert info["rejected"] == ["a", "b"] and env.rejected_actions == 2
    assert env.wins == {"a": 1, "b": 0}
    with pytest.raises(ValueError, match="known robot"):
        env.step({"ghost": None})
    other = AllocationEnv(micro())
    assert other.step({"a": ["t"]})[-1]["rejected"] == ["a"]
    env.simulation.check_invariants()


def test_reward_termination_truncation_and_empty():
    short = AllocationEnv(micro(1))
    _, reward, terminated, truncated, _ = short.step({"a": "t"})
    assert reward == -1 and not terminated and truncated
    assert run_episode(micro(), "heuristic")["return"] == -2
    empty = AllocationEnv(from_dict({"width": 1, "height": 1, "horizon": 0,
                                    "robots": [], "tasks": []}))
    assert empty.terminated and not empty.truncated
    assert run_episode(empty.instance)["return"] == 0


def test_environment_domain_and_seed_validation():
    data = micro().to_dict()
    data["width"] = 9
    with pytest.raises(ValueError, match="tabular domain"):
        AllocationEnv(from_dict(data))
    with pytest.raises(ValueError, match="seed"):
        AllocationEnv(micro()).reset(-1)


def test_q_learning_common_reward_legal_masks_save_reload(tmp_path):
    env = AllocationEnv(micro())
    before = env.observations()
    after, reward, done, truncated, _ = env.step({"a": "t", "b": None})
    policy = QPolicy()
    for robot in before:
        policy.update(robot, before[robot], "t" if robot == "a" else None,
                      reward, after[robot], done or truncated, alpha=1)
    assert policy.values("a", before["a"])[json.dumps("t")] == -1
    assert policy.values("b", before["b"])["null"] == -1
    assert policy.action("a", after["a"], random.Random(2)) is None
    path = tmp_path / "q.json"
    policy.save(path)
    loaded = QPolicy.load(path)
    assert loaded.to_dict() == policy.to_dict()
    frozen = loaded.to_dict()
    first = run_episode(micro(), "q", policy=loaded, seed=7, trace=True)
    second = run_episode(micro(), "q", policy=loaded, seed=7, trace=True)
    assert first["trace"] == second["trace"]
    assert loaded.to_dict() == frozen
    path.write_text('{"schema": 9}')
    with pytest.raises(ValueError, match="unsupported"):
        QPolicy.load(path)


def test_train_validation_only_seeded_and_disjoint():
    validation = load_instance("examples/simple.json")
    first, report = train([micro()], [validation], episodes=4, seed=19, checkpoint_every=2)
    second, repeated = train([micro()], [validation], episodes=4, seed=19, checkpoint_every=2)
    assert first.to_dict() == second.to_dict()
    assert report["curve"] == repeated["curve"]
    assert report["validation"] == repeated["validation"]
    assert report["selected_episode"] in (2, 4)
    assert len(report["curve"]) == 4
    with pytest.raises(ValueError, match="disjoint"):
        train([micro()], [micro()], episodes=1)
    duplicate = micro()
    duplicate.seed = 998
    with pytest.raises(ValueError, match="disjoint"):
        train([micro()], [duplicate], episodes=1)
    with pytest.raises(ValueError):
        train([], [validation])


@pytest.mark.parametrize("reply", ["valid", "invalid", "malformed", "timeout", "unavailable", "empty"])
def test_llm_policy_uses_local_observations_and_safe_fallback(monkeypatch, reply):
    from taskfleet.ollama import OllamaClient, OllamaConfig
    import taskfleet.ollama as ollama

    client = OllamaClient(OllamaConfig(model="fake", call_budget=2, timeout=0.1))
    seen = []

    def request(url, timeout, payload=None):
        from urllib.error import URLError
        local = json.loads(payload["messages"][1]["content"])["observation"]
        seen.append(local)
        assert "robots" not in local and "unavailability" not in local
        if reply == "timeout":
            raise TimeoutError("simulated")
        if reply == "unavailable":
            raise URLError("simulated")
        content = {"valid": '{"action":"t"}', "invalid": '{"action":"secret"}',
                   "malformed": 'broken', "empty": ''}[reply]
        return {"message": {"content": content}, "eval_count": 4}

    monkeypatch.setattr(ollama, "_request", request)
    row = run_episode(micro(4), "llm", client=client, trace=True)
    assert seen and row["invalid_decisions"] == 0 and row["rejected_actions"] == 0
    if reply == "valid":
        assert row["terminated"] and row["conflicts"] == 1
    else:
        assert row["truncated"] and row["tasks_completed"] == 0
        assert row["llm"]["fallbacks"] > 0 and row["llm"]["calls"] == 2
        assert "budget_exhausted" in row["llm"]["errors"]


def test_cli_train_evaluate(tmp_path, capsys):
    checkpoint, output = tmp_path / "q.json", tmp_path / "run.json"
    assert main(["train", "--train", "examples/allocation.json", "--validation",
                 "examples/simple.json", "--episodes", "2", "--checkpoint", str(checkpoint)]) == 0
    assert main(["evaluate", "examples/simple.json", "--policy", "q", "--checkpoint",
                 str(checkpoint), "--trace", "--json", str(output)]) == 0
    row = json.loads(output.read_text())
    assert row["policy"] == "q" and row["invalid_decisions"] == 0
    assert "trace" in row and "decisions" in row
