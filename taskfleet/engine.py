"""Deterministic tick engine, independent of task allocation strategies."""

from copy import deepcopy

from .instances import from_dict
from .models import Instance, Robot, RobotState, TaskState
from .paths import shortest_path


class Simulation:
    def __init__(self, instance: Instance):
        # Start from validated configuration, never from another run's mutable state.
        initial = from_dict(instance.to_dict())
        self._configuration = initial.to_dict()
        self.grid = initial.grid
        self.robots = initial.robots
        self.tasks = initial.tasks
        self.resources = initial.resources
        self.horizon = initial.horizon
        self.seed = initial.seed
        self.tick = 0
        self.events: list[dict] = []
        self._resources_at = {r.position: r for r in self.resources.values()}
        self.check_invariants()
        self.snapshots = [self.snapshot()]

    @property
    def finished(self) -> bool:
        """The time horizon has been reached, even if tasks remain unfinished."""
        return self.tick >= self.horizon

    @property
    def all_tasks_completed(self) -> bool:
        return all(t.state == TaskState.COMPLETED for t in self.tasks.values())

    def _event(self, kind: str, **details) -> None:
        self.events.append({"tick": self.tick, "kind": kind, **details})



    def _start_leg(self, robot: Robot, path: list, *, pickup: bool) -> None:
        robot.path = path
        if path:
            robot.state = RobotState.TO_PICKUP if pickup else RobotState.TO_DELIVERY
            robot.handling_remaining = 0
        else:
            robot.state = RobotState.PICKING if pickup else RobotState.DELIVERING
            robot.handling_remaining = self.tasks[robot.task_id].handling_ticks

    def assign(self, robot_id: str, task_id: str) -> None:
        """Assign one pending task; rejected assignments leave the state unchanged."""
        if self.finished:
            raise ValueError("Cannot assign after the simulation horizon")
        if robot_id not in self.robots:
            raise ValueError(f"Unknown robot {robot_id!r}")
        if task_id not in self.tasks:
            raise ValueError(f"Unknown task {task_id!r}")
        robot, task = self.robots[robot_id], self.tasks[task_id]
        if not robot.available:
            raise ValueError(f"Robot {robot_id!r} is already executing a task")
        if task.state != TaskState.PENDING:
            raise ValueError(f"Task {task_id!r} is already assigned or completed")
        if task.load > robot.capacity:
            raise ValueError(f"Task {task_id!r} exceeds robot capacity")
        path = shortest_path(self.grid, robot.position, task.pickup)
        if path is None:
            raise ValueError(f"Task {task_id!r} pickup is unreachable by robot {robot_id!r}")
        if shortest_path(self.grid, task.pickup, task.destination) is None:
            raise ValueError(f"Task {task_id!r} destination is unreachable")
        robot.task_id = task_id
        task.robot_id = robot_id
        task.state = TaskState.ASSIGNED
        self._start_leg(robot, path, pickup=True)
        self._event("assigned", robot_id=robot_id, task_id=task_id)
        self.check_invariants()

    def _handle(self, robot: Robot, releases: list[tuple[str, str]]) -> None:
        task = self.tasks[robot.task_id]
        pickup = task.state == TaskState.ASSIGNED
        resource = self._resources_at.get(robot.position)
        if resource is not None and robot.resource_id is None:
            if len(resource.holders) >= resource.capacity:
                robot.state = (RobotState.WAITING_PICKUP if pickup
                               else RobotState.WAITING_DELIVERY)
                self._event("waiting", robot_id=robot.id, task_id=task.id,
                            resource_id=resource.id)
                return
            resource.holders.add(robot.id)
            robot.resource_id = resource.id
            self._event("resource_acquired", robot_id=robot.id, resource_id=resource.id)

        robot.state = RobotState.PICKING if pickup else RobotState.DELIVERING
        robot.handling_remaining -= 1
        if robot.handling_remaining:
            return
        if robot.resource_id is not None:
            releases.append((robot.id, robot.resource_id))
        if pickup:
            task.state = TaskState.PICKED_UP
            self._event("collected", robot_id=robot.id, task_id=task.id)
            path = shortest_path(self.grid, robot.position, task.destination)
            if path is None:
                raise RuntimeError("Validated task destination became unreachable")
            self._start_leg(robot, path, pickup=False)
        else:
            task.state = TaskState.COMPLETED
            task.completed_tick = self.tick
            self._event("delivered", robot_id=robot.id, task_id=task.id,
                        late=task.deadline is not None and self.tick > task.deadline)
            robot.state = RobotState.IDLE
            robot.task_id = None

    def step(self) -> dict:
        """Advance one tick with one action per robot."""
        if self.finished:
            raise ValueError("Simulation horizon reached")
        self.tick += 1
        releases = []
        for robot_id in sorted(self.robots):
            robot = self.robots[robot_id]
            if robot.available:
                continue
            if robot.path:
                previous = robot.position
                robot.position = robot.path.pop(0)
                self._event("moved", robot_id=robot.id, task_id=robot.task_id,
                            from_position=list(previous), position=list(robot.position))
                if not robot.path:
                    self._start_leg(robot, [], pickup=robot.state == RobotState.TO_PICKUP)
            else:
                self._handle(robot, releases)

        for robot_id, resource_id in releases:
            self.resources[resource_id].holders.remove(robot_id)
            self.robots[robot_id].resource_id = None
            self._event("resource_released", robot_id=robot_id, resource_id=resource_id)
        self.check_invariants()
        state = self.snapshot()
        self.snapshots.append(state)
        return deepcopy(state)

    def run(self, until: int | None = None) -> dict:
        """Run to an absolute tick (default: horizon), including idle ticks."""
        until = self.horizon if until is None else until
        if type(until) is not int or not self.tick <= until <= self.horizon:
            raise ValueError(f"until must be an integer between {self.tick} and {self.horizon}")
        while self.tick < until:
            self.step()
        return self.snapshot()

    def snapshot(self) -> dict:
        """Return a detached, JSON-compatible view of the current state."""
        return {
            "tick": self.tick,
            "finished": self.finished,
            "all_tasks_completed": self.all_tasks_completed,
            "robots": {
                r.id: {"position": list(r.position), "capacity": r.capacity,
                       "state": r.state.value, "available": r.available,
                       "task_id": r.task_id, "handling_remaining": r.handling_remaining,
                       "resource_id": r.resource_id, "path": [list(p) for p in r.path]}
                for r in sorted(self.robots.values(), key=lambda r: r.id)
            },
            "tasks": {
                t.id: {"state": t.state.value, "robot_id": t.robot_id,
                       "completed_tick": t.completed_tick,
                       "late": t.completed_tick is not None and t.deadline is not None
                       and t.completed_tick > t.deadline}
                for t in sorted(self.tasks.values(), key=lambda t: t.id)
            },
            "resources": {
                r.id: {"position": list(r.position), "capacity": r.capacity,
                       "holders": sorted(r.holders)}
                for r in sorted(self.resources.values(), key=lambda r: r.id)
            },
        }

    def trace(self) -> dict:
        """Configuration, ordered events and tick snapshots for inspection or replay."""
        recorded = {"instance": deepcopy(self._configuration),
                    "events": deepcopy(self.events), "snapshots": deepcopy(self.snapshots),
                    "current": self.snapshot()}
        return recorded

    def check_invariants(self) -> None:
        """Assert state consistency after assignments and complete ticks."""
        assert 0 <= self.tick <= self.horizon
        active_tasks = set()
        moving = {RobotState.TO_PICKUP, RobotState.TO_DELIVERY}
        pickup_states = {RobotState.TO_PICKUP, RobotState.PICKING, RobotState.WAITING_PICKUP}
        handling_states = {RobotState.PICKING, RobotState.DELIVERING}
        for robot in self.robots.values():
            assert self.grid.accessible(robot.position)
            if robot.state == RobotState.IDLE:
                assert robot.task_id is None and not robot.path
                assert robot.handling_remaining == 0 and robot.resource_id is None
                continue
            assert robot.task_id in self.tasks and robot.task_id not in active_tasks
            active_tasks.add(robot.task_id)
            task = self.tasks[robot.task_id]
            assert task.robot_id == robot.id and task.load <= robot.capacity
            assert task.state == (TaskState.ASSIGNED if robot.state in pickup_states
                                  else TaskState.PICKED_UP)
            goal = task.pickup if task.state == TaskState.ASSIGNED else task.destination
            if robot.state in moving:
                assert robot.path and robot.path[-1] == goal
                assert robot.handling_remaining == 0 and robot.resource_id is None
                previous = robot.position
                for position in robot.path:
                    assert self.grid.accessible(position)
                    assert abs(position[0] - previous[0]) + abs(position[1] - previous[1]) == 1
                    previous = position
            else:
                assert not robot.path and robot.position == goal
                assert 1 <= robot.handling_remaining <= task.handling_ticks
            if robot.resource_id is not None:
                resource = self.resources[robot.resource_id]
                assert robot.id in resource.holders and robot.position == resource.position
                assert robot.state in handling_states

        for task in self.tasks.values():
            if task.state == TaskState.PENDING:
                assert task.robot_id is None and task.completed_tick is None
            elif task.state == TaskState.COMPLETED:
                assert task.robot_id in self.robots
                assert task.completed_tick is not None and 1 <= task.completed_tick <= self.tick
                assert task.id not in active_tasks
            else:
                assert task.id in active_tasks and task.completed_tick is None
        for resource in self.resources.values():
            assert len(resource.holders) <= resource.capacity
            for robot_id in resource.holders:
                assert robot_id in self.robots
                assert self.robots[robot_id].resource_id == resource.id
