import copy
import json
from pathlib import Path

import pytest

from taskfleet import from_dict, load_instance
from taskfleet.models import Grid, RobotState, TaskState
from taskfleet.paths import shortest_path

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "contention.json"


@pytest.fixture
def data():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_valid_instance_and_roundtrip(data):
    original = copy.deepcopy(data)
    instance = from_dict(data)
    assert data == original
    assert load_instance(EXAMPLE) == instance
    assert from_dict(instance.to_dict()) == instance
    assert instance.robots["r1"].available
    assert instance.robots["r1"].state == RobotState.IDLE
    assert instance.tasks["t1"].state == TaskState.PENDING
    assert instance.resources["pickup"].holders == set()


def test_defaults():
    instance = from_dict({"width": 1, "height": 1,
                          "robots": [{"id": "r", "position": [0, 0], "capacity": 1}],
                          "tasks": [{"id": "t", "pickup": [0, 0], "destination": [0, 0]}]})
    assert instance.horizon == 100
    assert instance.seed is None
    assert instance.tasks["t"].load == instance.tasks["t"].handling_ticks == 1
    assert instance.tasks["t"].deadline is None
    assert instance.resources == {}


@pytest.mark.parametrize("field,value,message", [
    ("width", 0, "width"), ("height", -1, "height"),
    ("width", True, "width"), ("height", 2.5, "height"),
    ("horizon", -1, "horizon"), ("horizon", False, "horizon"),
    ("seed", "42", "seed"), ("seed", -1, "seed"),
    ("obstacles", [[5, 0]], "outside grid"),
    ("obstacles", [[-1, 0]], "integer"),
    ("obstacles", [[2, 0], [2, 0]], "duplicate position"),
    ("obstacles", "invalid", "array"), ("robots", None, "array"),
    ("tasks", {}, "array"), ("resources", False, "array"),
    ("typo", 1, "unknown fields"),
])
def test_invalid_root(data, field, value, message):
    data[field] = value
    with pytest.raises(ValueError, match=message):
        from_dict(data)


@pytest.mark.parametrize("collection,field,value,message", [
    ("robots", "position", [5, 0], "outside grid"),
    ("robots", "position", [0, 3], "outside grid"),
    ("robots", "position", [-1, 0], "integer"),
    ("robots", "position", [True, 0], "integer"),
    ("robots", "position", [0.5, 0], "integer"),
    ("robots", "position", [0], "expected"),
    ("robots", "position", [2, 0], "obstacle"),
    ("robots", "capacity", 0, "capacity"),
    ("robots", "id", " ", "non-empty"),
    ("robots", "id", 1, "non-empty"),
    ("tasks", "pickup", [2, 0], "obstacle"),
    ("tasks", "destination", [5, 0], "outside grid"),
    ("tasks", "load", 0, "load"),
    ("tasks", "load", 3, "sufficient capacity"),
    ("tasks", "handling_ticks", 0, "handling_ticks"),
    ("tasks", "deadline", -1, "deadline"),
    ("resources", "position", [2, 0], "obstacle"),
    ("resources", "capacity", 0, "capacity"),
    ("resources", "capacity", True, "capacity"),
    ("resources", "position", [4, 0], "one resource per position"),
    ("tasks", "typo", 1, "unknown fields"),
])
def test_invalid_entries(data, collection, field, value, message):
    data[collection][0][field] = value
    with pytest.raises(ValueError, match=message):
        from_dict(data)


@pytest.mark.parametrize("collection", ["robots", "tasks", "resources"])
def test_duplicate_ids(data, collection):
    data[collection][1]["id"] = data[collection][0]["id"]
    with pytest.raises(ValueError, match="duplicate ID"):
        from_dict(data)


@pytest.mark.parametrize("value,message", [([], "object"), ({}, "missing fields")])
def test_missing_or_non_object(value, message):
    with pytest.raises(ValueError, match=message):
        from_dict(value)


@pytest.mark.parametrize("pickup,destination,message", [
    ([0, 0], [2, 0], "destination is unreachable"),
    ([2, 0], [2, 0], "pickup is unreachable"),
])
def test_unreachable_task(pickup, destination, message):
    data = {"width": 3, "height": 1, "obstacles": [[1, 0]],
            "robots": [{"id": "r", "position": [0, 0], "capacity": 1}],
            "tasks": [{"id": "t", "pickup": pickup, "destination": destination}]}
    with pytest.raises(ValueError, match=message):
        from_dict(data)


@pytest.mark.parametrize("text,message", [
    ('{"width":', "Invalid JSON at line"),
    ('{"width": 1, "width": 2}', "duplicate field"),
])
def test_invalid_json(tmp_path, text, message):
    path = tmp_path / "invalid.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_instance(path)


@pytest.mark.parametrize("grid,start,goal,expected", [
    (Grid(3, 1), (0, 0), (2, 0), [(1, 0), (2, 0)]),
    (Grid(1, 1), (0, 0), (0, 0), []),
    (Grid(3, 2, frozenset({(1, 0)})), (0, 0), (2, 0),
     [(0, 1), (1, 1), (2, 1), (2, 0)]),
    (Grid(3, 1, frozenset({(1, 0)})), (0, 0), (2, 0), None),
    (Grid(1, 1), (0, 0), (1, 0), None),
    (Grid(1, 1), (-1, 0), (0, 0), None),
    (Grid(2, 1, frozenset({(1, 0)})), (0, 0), (1, 0), None),
])
def test_paths(grid, start, goal, expected):
    path = shortest_path(grid, start, goal)
    assert path == expected
    if path is not None:
        previous = start
        for cell in path:
            assert grid.accessible(cell)
            assert abs(cell[0] - previous[0]) + abs(cell[1] - previous[1]) == 1
            previous = cell
