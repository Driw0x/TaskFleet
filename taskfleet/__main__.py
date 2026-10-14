"""Text interface for explicit assignments and JSON trace export."""

import argparse
import json
from pathlib import Path

from .engine import Simulation
from .instances import load_instance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TaskFleet deterministic warehouse simulation")
    parser.add_argument("instance", type=Path, help="JSON instance path")
    parser.add_argument("--assign", nargs=2, action="append", default=[],
                        metavar=("ROBOT", "TASK"), help="explicit assignment (repeatable)")
    parser.add_argument("--until", type=int, help="absolute ending tick (default: instance horizon)")
    parser.add_argument("--trace", type=Path, help="write the complete JSON trace")
    parser.add_argument("--verify-repeat", action="store_true", help="repeat and compare complete traces")
    arguments = parser.parse_args(argv)
    try:
        instance = load_instance(arguments.instance)
        simulation = Simulation(instance)
        print("Initial state:")
        print(json.dumps(simulation.snapshot(), indent=2, ensure_ascii=False))
        for robot_id, task_id in arguments.assign:
            simulation.assign(robot_id, task_id)
        simulation.run(arguments.until)
        trace = simulation.trace()
        print("Final state:")
        print(json.dumps(trace["current"], indent=2, ensure_ascii=False))
        print("Events:")
        print(json.dumps(trace["events"], indent=2, ensure_ascii=False))
        if arguments.verify_repeat:
            repeat = Simulation(instance)
            for robot_id, task_id in arguments.assign:
                repeat.assign(robot_id, task_id)
            repeat.run(arguments.until)
            if repeat.trace() != trace:
                raise RuntimeError("Reproducibility check failed")
            print("Reproducibility: identical traces")
        if arguments.trace:
            arguments.trace.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"Error: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
