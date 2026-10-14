"""Synchronous Contract Net: agents compute private bids from local observations."""

from dataclasses import dataclass

from .allocation import Observation, estimated_cost
from .models import Grid, Robot, Task


@dataclass
class RobotAgent:
    """Only own robot state, static public grid and explicitly announced tasks."""

    grid: Grid
    robot: Robot
    commitment: str | None = None

    def bid(self, task: Task) -> dict:
        if self.commitment is not None or not self.robot.available:
            reason = "busy_or_unavailable"
        elif self.robot.capacity < task.load:
            reason = "capacity"
        else:
            cost = estimated_cost(self.grid, self.robot, task)
            if cost is not None:
                return {"type": "offer", "cost": cost}
            reason = "unreachable"
        return {"type": "refuse", "reason": reason}

    def accept(self, task: Task) -> bool:
        if self.bid(task)["type"] != "offer":
            return False
        self.commitment = task.id
        return True

    def execution_state(self) -> dict:
        return {"robot_id": self.robot.id, "state": self.robot.state.value,
                "task_id": self.robot.task_id, "commitment": self.commitment}


def contract_net(observation: Observation) -> dict:
    agents = [RobotAgent(observation.grid, robot, robot.task_id)
              for robot in sorted(observation.robots, key=lambda r: r.id)]
    available = [a for a in agents if a.robot.available]
    if not available:
        return {"assignments": [], "estimated_cost": 0, "status": "NO_INITIATOR",
                "optimal": False, "messages": []}
    initiator = available[0].robot.id
    messages = []
    assignments = []
    total_cost = 0

    def message(kind, sender, recipient, task, **details):
        messages.append({"type": kind, "sender": sender, "recipient": recipient,
                         "task_id": task.id, **details})

    for task in observation.tasks:
        message("announce", initiator, "all", task)
        offers = []
        for agent in agents:
            message("request", initiator, agent.robot.id, task)
            response = agent.bid(task)
            message(response["type"], agent.robot.id, initiator, task,
                    **{k: v for k, v in response.items() if k != "type"})
            if response["type"] == "offer":
                offers.append((response["cost"], agent.robot.id, agent))
        if not offers:
            message("no_award", initiator, "all", task, reason="no_valid_offer")
            continue
        cost, winner_id, winner = min(offers, key=lambda offer: (offer[0], offer[1]))
        message("award", initiator, winner_id, task, cost=cost)
        accepted = winner.accept(task)
        message("accept" if accepted else "reject", winner_id, initiator, task,
                reason="committed" if accepted else "commitment_conflict")
        for _, robot_id, _ in offers:
            if robot_id != winner_id:
                message("reject", initiator, robot_id, task, reason="other_offer_selected")
        if accepted:
            assignments.append([winner_id, task.id])
            total_cost += cost
    return {"assignments": assignments, "estimated_cost": total_cost,
            "status": "NEGOTIATED", "optimal": False, "messages": messages}
