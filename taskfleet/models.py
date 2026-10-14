"""Warehouse configuration and simulation states."""

from dataclasses import dataclass, field
from enum import StrEnum

Position = tuple[int, int]


class RobotState(StrEnum):
    IDLE = "idle"
    TO_PICKUP = "to_pickup"
    PICKING = "picking"
    WAITING_PICKUP = "waiting_pickup"
    TO_DELIVERY = "to_delivery"
    DELIVERING = "delivering"
    WAITING_DELIVERY = "waiting_delivery"


class TaskState(StrEnum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    PICKED_UP = "picked_up"
    COMPLETED = "completed"


@dataclass(frozen=True)
class Grid:
    width: int
    height: int
    obstacles: frozenset[Position] = frozenset()

    def accessible(self, position: Position) -> bool:
        x, y = position
        return (0 <= x < self.width and 0 <= y < self.height
                and position not in self.obstacles)


@dataclass
class Robot:
    id: str
    position: Position
    capacity: int
    state: RobotState = RobotState.IDLE
    task_id: str | None = None
    path: list[Position] = field(default_factory=list)
    handling_remaining: int = 0
    resource_id: str | None = None

    @property
    def available(self) -> bool:
        return self.state == RobotState.IDLE


@dataclass
class Task:
    id: str
    pickup: Position
    destination: Position
    load: int = 1
    handling_ticks: int = 1
    deadline: int | None = None
    state: TaskState = TaskState.PENDING
    robot_id: str | None = None
    completed_tick: int | None = None


@dataclass
class Resource:
    id: str
    position: Position
    capacity: int = 1
    holders: set[str] = field(default_factory=set)


@dataclass
class Instance:
    grid: Grid
    robots: dict[str, Robot]
    tasks: dict[str, Task]
    resources: dict[str, Resource]
    horizon: int = 100
    seed: int | None = None

    def to_dict(self) -> dict:
        """Export configuration in the same format accepted by the loader."""
        return {
            "width": self.grid.width,
            "height": self.grid.height,
            "obstacles": [list(p) for p in sorted(self.grid.obstacles)],
            "robots": [
                {"id": r.id, "position": list(r.position), "capacity": r.capacity}
                for r in sorted(self.robots.values(), key=lambda r: r.id)
            ],
            "tasks": [
                {"id": t.id, "pickup": list(t.pickup),
                 "destination": list(t.destination), "load": t.load,
                 "handling_ticks": t.handling_ticks, "deadline": t.deadline}
                for t in sorted(self.tasks.values(), key=lambda t: t.id)
            ],
            "resources": [
                {"id": r.id, "position": list(r.position), "capacity": r.capacity}
                for r in sorted(self.resources.values(), key=lambda r: r.id)
            ],
            "horizon": self.horizon,
            "seed": self.seed,
        }
