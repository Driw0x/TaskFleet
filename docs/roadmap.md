[English](roadmap.md) | [Français](roadmap.fr.md) | [简体中文](roadmap.zh-CN.md)

<a id="roadmap"></a>
# Roadmap


[Project README](../README.md)


M0 to M6 are completed and verified.

V1 consolidation audits these milestones and extends their evaluation, without introducing a new milestone. Results and reproduction: [benchmark](benchmark.md).

<a id="m0--modélisation-du-problème"></a>
## M0 - Problem modeling

Define the common model for simulations and strategies.

- [x] Define the environment, agents, tasks, and shared resources.
- [x] Formalize constraints on movement, capacity, and resource utilization.
- [x] Define the format of simulation instances.

<a id="m1--moteur-de-simulation"></a>
## M1 - Simulation engine

Execute a reproducible simulation in a discrete environment.

- [x] Implement the discrete environment and obstacles.
- [x] Represent robots, tasks, and movements.
- [x] Manage access to shared resources.
- [x] Load simulation instances.
- [x] Ensure reproducibility, particularly through random seed control.
- [x] Test the scheduler's behaviors with pytest.

<a id="m2--allocation-centralisée"></a>
## M2 - Centralized allocation

Develop and compare centralized allocation and scheduling strategies.

- [x] Implement the baseline heuristic FIFO.
- [x] Implement optimization using OR-Tools CP-SAT.
- [x] Compare allocations on common instances.

<a id="m3--coordination-multi-agent"></a>
## M3 - Multi-agent scheduling

Enable agents to negotiate and assign tasks.

- [x] Implement local agent decisions.
- [x] Add communication between agents.
- [x] Implement the Contract Net Protocol (CNP), from task announcement to task assignment.
- [x] Integrate negotiations into the simulation engine.

<a id="m4--environnement-dynamique"></a>
## M4 - Dynamic environment

Adapt decisions to changes during simulation.

- [x] Manage the arrival of new tasks.
- [x] Manage robot unavailability.
- [x] Adapt allocation and plans to disturbances.

<a id="m5--benchmark-et-évaluation"></a>
## M5 - Benchmark and evaluation

Evaluate strategies under comparable conditions.

- [x] Define experimental scenarios and metrics.
- [x] Execute reproducible experiments with implemented strategies.
- [x] Compare strategies on the same scenarios.
- [x] Analyze and document actually obtained results.

<a id="m6--interface-web-interactive"></a>
## M6 - Interactive web interface

Explore simulations and create instances using Vue 3, TypeScript, SVG and FastAPI.

- [x] Visualize the warehouse, obstacles, and robots.
- [x] Replay simulations and display interactions between agents.
- [x] Play, pause, adjust speed, and navigate locally within snapshots.
- [x] Select robots, tasks, and stations; inspect current tick state, assignment, occupancy, and history.
- [x] Provide a permanent legend, task filters, and distinguish Python paths from collection/delivery relationships.
- [x] Enable selection of implemented strategies.
- [x] Create custom instances: robots, capacities, tasks, obstacles, dimensions, and resources.
- [x] Import and export instances in the format defined in M0.
- [x] Visualize simulation and comparison results.
- [x] Automatically display a persistent comparison with M5 metrics, errors, and invalidation of expired results.
- [x] Provide a complex, reproducible instance with measurable operational differences between the three strategies.
- [x] Generate valid random instances with fixed dimensions and local seed, editable and exportable.
- [x] Replace Streamlit with a local API and a reactive frontend, without modifying the scientific engine.

<a id="v2-scientifique--terminée"></a>
## V2 scientific - Completed

<a id="m7--advanced--robust-scheduling"></a>
### M7 - Advanced & Robust Scheduling

Evaluate temporal scheduling, sliding horizon, and robustness on finite scenarios.

- [x] Model precedences, trajectories, capacities, stations, arrivals, and delays using CP-SAT; minimize total tardiness then makespan.
- [x] Replay plans in the V1 engine: preserve priority ID, deferred release, and reservation during suspension.
- [x] Reschedule without future events, with revealed tasks, backlog preserved, and active commitments frozen.
- [x] Optimize fixed queues via minimax: worst-case tardiness, then worst-case makespan, with completion in each declared schedule; limit guarantees to enumerated scenarios and policies.
- [x] Verify microcases via oracle, distinguish statuses and budgets, compare baselines with explicit information, and publish the [reproducible results](results/v2/m7.json).

<a id="m8--distributed-constraint-optimization"></a>
### M8 - Distributed Constraint Optimization

Evaluate distributed allocation through local decisions and agent-to-agent exchanges.

- [x] Implement DSA on task/waiting domains, private costs, and cardinality objective, then cost, without hidden central optimization.
- [x] Confirm assignments via messages, without duplicates or reliance on engine rejections.
- [x] Verify allocations via enumeration and CP-SAT; compare FIFO/CNP, oscillations, budgets, messages, and total time in the [reproducible results](results/v2/m8.json).
- [x] Validate the experimental extension Ollama: real, legal, and traceable local decisions, model selected based on validation and separately tested; cardinality below references ([proofs](results/v2/ollama-selection-20261015/report.json)).

<a id="m9--multi-agent-path-finding"></a>
### M9 - Multi-Agent Path Finding

Plan compatible trajectories on an isolated MAPF with fixed destinations.

- [x] Formalize movements, waits, vertex conflicts, and edge exchanges, with absorbing and persistent goals.
- [x] Implement prioritized planning, reservations, and CBS; independently verify plans and compare against exhaustive oracle optima.
- [x] Bound certificates to declared model and horizon; distinguish infeasibility within horizon, priority failure, and budget exhaustion.
- [x] Compare costs, successes, priorities, and budgets within [reproducible results](results/v2/m9.json), without modifying the V1 physics.

<a id="m10--cooperative-multi-agent-reinforcement-learning"></a>
### M10 - Cooperative Multi-Agent Reinforcement Learning

Evaluate cooperative learning of allocation in a bounded tabular domain.

- [x] Reuse the V1 engine with local observations, action masks, and shared policies among compared policies.
- [x] Test a common reward across remaining revealed tasks, transitions, termination, and horizon truncation.
- [x] Train tabular Q-learners, save and reload policies; separate training, validation, and testing, with selection only on validation.
- [x] Evaluate fixed policies across multiple seeds against heuristics; preserve the negative generalization result in the [reproducible results](results/v2/m10.json).
- [x] Validate the experimental extension Ollama: real, legal, and traceable actions, model selected on validation and separately tested on test, without demonstrated general superiority ([evidence](results/v2/ollama-selection-20261015/report.json)).
