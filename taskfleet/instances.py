"""Strict validation of JSON warehouse instances."""

import json
from pathlib import Path

from .models import Grid, Instance, Resource, Robot, Task, TaskState
from .paths import shortest_path


def _object(value, required: set[str], optional: set[str], label: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label}: expected an object")
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise ValueError(f"{label}: missing fields {sorted(missing)}")
    if unknown:
        raise ValueError(f"{label}: unknown fields {sorted(unknown)}")
    return value


def _integer(value, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label}: expected an integer >= {minimum}")
    return value


def _array(value, label: str) -> list:
    if not isinstance(value, list):
        raise ValueError(f"{label}: expected an array")
    return value


def _position(value, grid: Grid, label: str, *, allow_obstacle=False) -> tuple[int, int]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label}: expected [x, y]")
    position = tuple(_integer(n, label) for n in value)
    x, y = position
    if x >= grid.width or y >= grid.height:
        raise ValueError(f"{label}: outside grid")
    if not allow_obstacle and position in grid.obstacles:
        raise ValueError(f"{label}: position is on an obstacle")
    return position


def _id(value, existing: dict, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: expected a non-empty string ID")
    if value in existing:
        raise ValueError(f"{label}: duplicate ID {value!r}")
    return value


def from_dict(data: dict) -> Instance:
    """Build a fresh, validated instance; input data is never modified."""
    data = _object(data, {"width", "height", "robots", "tasks"},
                   {"obstacles", "resources", "horizon", "seed", "unavailability"}, "instance")
    grid = Grid(_integer(data["width"], "width", 1),
                _integer(data["height"], "height", 1))
    obstacles = [_position(p, grid, "obstacles", allow_obstacle=True)
                 for p in _array(data.get("obstacles", []), "obstacles")]
    if len(set(obstacles)) != len(obstacles):
        raise ValueError("obstacles: duplicate position")
    grid = Grid(grid.width, grid.height, frozenset(obstacles))
    horizon = _integer(data.get("horizon", 100), "horizon")
    seed = data.get("seed")
    if seed is not None:
        seed = _integer(seed, "seed")

    robots = {}
    for index, entry in enumerate(_array(data["robots"], "robots")):
        label = f"robots[{index}]"
        entry = _object(entry, {"id", "position", "capacity"}, set(), label)
        robot_id = _id(entry["id"], robots, label)
        robots[robot_id] = Robot(robot_id, _position(entry["position"], grid, label),
                                 _integer(entry["capacity"], f"{label}.capacity", 1))

    resources = {}
    for index, entry in enumerate(_array(data.get("resources", []), "resources")):
        label = f"resources[{index}]"
        entry = _object(entry, {"id", "position"}, {"capacity"}, label)
        resource_id = _id(entry["id"], resources, label)
        position = _position(entry["position"], grid, label)
        if any(r.position == position for r in resources.values()):
            raise ValueError(f"{label}: only one resource per position is allowed")
        resources[resource_id] = Resource(
            resource_id, position, _integer(entry.get("capacity", 1), f"{label}.capacity", 1))

    tasks = {}
    for index, entry in enumerate(_array(data["tasks"], "tasks")):
        label = f"tasks[{index}]"
        entry = _object(entry, {"id", "pickup", "destination"},
                        {"load", "handling_ticks", "deadline", "arrival_tick"}, label)
        task_id = _id(entry["id"], tasks, label)
        deadline = entry.get("deadline")
        if deadline is not None:
            deadline = _integer(deadline, f"{label}.deadline")
        task = Task(task_id, _position(entry["pickup"], grid, f"{label}.pickup"),
                    _position(entry["destination"], grid, f"{label}.destination"),
                    _integer(entry.get("load", 1), f"{label}.load", 1),
                    _integer(entry.get("handling_ticks", 1), f"{label}.handling_ticks", 1),
                    deadline, _integer(entry.get("arrival_tick", 0), f"{label}.arrival_tick"))
        if task.arrival_tick > 0:
            task.state = TaskState.SCHEDULED
        if shortest_path(grid, task.pickup, task.destination) is None:
            raise ValueError(f"{label}: destination is unreachable from pickup")
        capable = [r for r in robots.values() if r.capacity >= task.load]
        if not capable:
            raise ValueError(f"{label}: no robot has sufficient capacity")
        if not any(shortest_path(grid, r.position, task.pickup) is not None for r in capable):
            raise ValueError(f"{label}: pickup is unreachable by every capable robot")
        tasks[task_id] = task
    unavailability = []
    for index, entry in enumerate(_array(data.get("unavailability", []), "unavailability")):
        label = f"unavailability[{index}]"
        entry = _object(entry, {"robot_id", "start_tick", "end_tick"}, set(), label)
        robot_id = entry["robot_id"]
        if not isinstance(robot_id, str) or robot_id not in robots:
            raise ValueError(f"{label}: unknown robot {robot_id!r}")
        start = _integer(entry["start_tick"], f"{label}.start_tick")
        end = _integer(entry["end_tick"], f"{label}.end_tick")
        if end <= start:
            raise ValueError(f"{label}: end_tick must be greater than start_tick")
        if any(e["robot_id"] == robot_id and start < e["end_tick"] and e["start_tick"] < end
               for e in unavailability):
            raise ValueError(f"{label}: overlapping unavailability periods")
        unavailability.append({"robot_id": robot_id, "start_tick": start, "end_tick": end})
    return Instance(grid, robots, tasks, resources, horizon, seed, unavailability)


def _unique_keys(pairs: list[tuple]) -> dict:
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f"JSON: duplicate field {key!r}")
        data[key] = value
    return data


def loads_instance(content: str | bytes) -> Instance:
    """Validate JSON content, including duplicate fields, for files and UI imports."""
    try:
        data = json.loads(content, object_pairs_hook=_unique_keys)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON at line {error.lineno}: {error.msg}") from error
    return from_dict(data)


def load_instance(path: str | Path) -> Instance:
    return loads_instance(Path(path).read_text(encoding="utf-8"))
