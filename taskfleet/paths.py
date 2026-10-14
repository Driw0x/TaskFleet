"""Shortest paths on a uniform four-neighbour grid."""

from collections import deque

from .models import Grid, Position


def shortest_path(grid: Grid, start: Position, goal: Position) -> list[Position] | None:
    """Return cells after start, including goal; None means unreachable."""
    # ponytail: BFS per query; cache routes only if repeated searches dominate profiling.
    if not grid.accessible(start) or not grid.accessible(goal):
        return None
    frontier = deque([start])
    previous: dict[Position, Position | None] = {start: None}
    while frontier:
        cell = frontier.popleft()
        if cell == goal:
            path = []
            while cell != start:
                path.append(cell)
                cell = previous[cell]
            return list(reversed(path))
        x, y = cell
        for neighbour in ((x + 1, y), (x, y + 1), (x - 1, y), (x, y - 1)):
            if grid.accessible(neighbour) and neighbour not in previous:
                previous[neighbour] = cell
                frontier.append(neighbour)
    return None
