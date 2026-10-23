[English](memory.md) | [Français](memory.fr.md) | [简体中文](memory.zh-CN.md)

<a id="mémoire-technique-taskfleet"></a>
# Technical development log TaskFleet



**Read explicitly at the beginning of the next Codex sessions.** This file is not automatically loaded as a repository instruction.

<a id="état-du-projet"></a>
## Project status

**V1, M0–M6 completed; V2, M7–M10 completed.** A common Python engine powers CLI, benchmark, and FastAPI; the View replays traces. Installation and usage: [README](../README.md); milestones: [roadmap](roadmap.md).

Branch `main`, history organized by M0–M6 followed by final documentation. Remote `origin`: `https://github.com/Driw0x/TaskFleet.git`. Steps are retrospectively reconstructed from the validated V1, not from original historical snapshots; each step is tested in isolation. The generator is introduced in M5 for extended benchmarking, then exposed via the M6 interface. Compare `HEAD` to `origin/main` to verify synchronization during a restart.

<a id="architecture-et-décisions"></a>
## Architecture and decisions

- `taskfleet/models.py`, `instances.py`, `paths.py`: states/configuration, strict validation and generation, deterministic BFS. `engine.py`: transitions, invariants, events, and snapshots.
- `allocation.py`, `cnp.py`: decisions on detached observations; `metrics.py`, `benchmark.py`: common M5 evaluation; `__main__.py`: CLI. `scripts/benchmark_extended.py` reuses these modules.
- `api/main.py` directly adapts the engine; validation and generation share the Python parser. `frontend/src/App.vue` manages replay, inspection, and comparison; SVG components, editor, and journal.

FIFO uses `(arrival_tick, ID)` then robots by ID. CP-SAT maximizes cardinality, then minimizes BFS distances plus two handling operations, without deadlines or queues. Its statuses and deterministic phase-by-phase budget remain explicit: only current assignment optimum, `FEASIBLE` without proof, a second phase with no solution preserves the feasible assignment from the first phase. CNP uses local offers, commitments, and reannouncements; the initiator selects received offers without private recalculation. No distributed network. [Scientific details](benchmark.md#problème-et-stratégies).

Vue computes a trace per launch and navigates without API calls. Obsolete responses are ignored, timers are cleaned; comparisons are preserved between ticks/strategies, invalidated after editing, successful import/generation, or budget change. The blue path comes from `robot.path`; the violet link collects and delivers is logical. Selections and filters follow the snapshot; overlapping robots remain in their cells; CNP broadcasts remain in the robot filter.

<a id="fonctionnement"></a>
## Operation

- Grid `[x, y]`, four neighbors, one cell per tick. Deterministic BFS with right/bottom/left/top priority; path without origin, with destination. Unique robot-task assignment, compatible load.
- Positive manipulation at each end, even if they coincide. A dedicated station is reserved during manipulation; robot capacity is independent of load. Deadline is informative.
- Scheduling: allocation at current tick; increment; arrivals then availabilities; one action per robot in lexicographic order; releases; snapshot. An assignment at tick n takes effect at tick n+1; a released cell is reusable at the next tick.
- Unschedulable `[start_tick, end_tick)`: path, contract, handling and reservation preserved. Adjacent periods are continuous, overlaps are rejected. Observations exclude future tasks and calendar; illegal assignments do not modify state or events.
- `run(until)` uses an absolute tick; without argument, it proceeds until the horizon. `finished` indicates this horizon, `all_tasks_completed` indicates completion. No fictitious deliveries.
- `trace()` contains initial configuration, events, snapshots, and current state detached. Initial snapshot before assignment; subsequent snapshots after tick. The view uses `current` at the last tick. An automatic trace is reproduced with the same instance, strategy, and budget; real-time exclusions apply.

The JSON describes the initial configuration: `width`, `height`, `robots`, `tasks` required; obstacles, resources, horizon, seed, and unavailability optional. Robot: ID, position, capacity; task: ID, collection, destination, handling/load, deadline, and arrival; unavailability: robot and interval. Strict integers, unique IDs per type, one resource per cell; booleans/floats, unknown fields, and duplicated JSON keys are rejected. `from_dict`, `loads_instance`, `load_instance`, and `Instance.to_dict()` share the same format. The [examples](../examples/simple.json) and parser provide exhaustive field/defaults.

Validation ensures capacity/accessibility, not completion before the horizon. Generation uses a local seed, preserves grid/obstacles/resources/horizon, replaces robots/tasks, and removes the old calendar. The engine does not draw any randomness. CP-SAT: one worker, seed 0; choices are reproducible across verified versions/platforms. Direct model mutations are not supported.

<a id="vérifications"></a>
## Checks

Validation V1 executed on **October 14, 2026**: **245 Python tests**, including **175 M0–M5**, **25 Vue tests**, TypeScript/build Vite, compilation/imports, and `pip check` successful; npm audit shows no vulnerabilities. Retrospective snapshots M0–M6 are also validated. Benchmark M5 and extended results have been previously reproduced, aggregates/CSVs are consistent and show no divergence. Results, counts, and commands: [benchmark](benchmark.md#reproduction) and [README](../README.md).

Python versions in `constraints.txt`, frontend in the lockfile. Environment verified: Python 3.13.1, OR-Tools 9.15.6755, Node 24.13.0. Fixed versions require Python 3.12+. Windows sandbox may reject TEMP or `realpath`: use authorized checks outside sandbox, without circumvention in the project. Temporary exports outside the repository.

<a id="limites-connues"></a>
## Known limits

No physical collisions, battery, preemption, transfer, or queue prediction. A suspension may monopolize a station; lexicographic ordering does not guarantee fairness. Five synthetic seeds, makespan conditional on completion, variable times: no universal superiority or global dynamic optimum is guaranteed.

V1 routes are synchronous; V2 experiments are asynchronous and bounded, complete traces stored in memory, no server persistence; small instances. CORS is limited to local frontend on port 5173. Internal errors are logged and masked to the client; HTTP status is preserved for non-JSON errors. Starlette/HTTPX deprecated in TestClient. Streamlit removed; Matplotlib optional for figures, pandas transitively from OR-Tools.

<a id="v2--terminée"></a>
## V2 - Completed

<a id="architecture-et-décisions-1"></a>
### Architecture and decisions

- `taskfleet/scheduling.py` (M7): temporal CP-SAT scheduling, sliding horizon and minimax over fixed queues. The local transition tables come from `Simulation.step`; plans are replayed in engine V1, without second physical simulation or new V1 strategy.
- `taskfleet/dcop.py` (M8): local agents, synchronous DSA-B and distributed confirmation of an allocation on fixed observation. Exhaustive/CP-SAT references are used for evaluation only, never for repairing DSA. `--execute` applies only confirmed assignments to V1.
- `taskfleet/mapf.py` (M9): separated problem with fixed origins and goals, A* in space-time, prioritized planning, CBS, exhaustive joint oracle and independent verifier. Reuses the V1 grid; does not modify the warehouse engine movements.
- `taskfleet/marl.py` (M10): `AllocationEnv` wraps `Simulation` in mode `manual`; `QPolicy` maintains a table per robot, trained by `train` with shared reward. Bounded domain to four robots, eight tasks, and an 8×8 grid; random, heuristic, Q, and LLM policies.
- `taskfleet/ollama.py`: common HTTP client M8/M10, inventory, preloading, decisions, and metrics; standard Python library, no additional SDK. `ollama_selection.py` loads the fixed choice. LLM replaces local decision, without training a model.

Scripts `scripts/benchmark_scheduling.py`, `benchmark_dcop.py`, `benchmark_mapf.py` and `benchmark_marl.py` evaluate respectively M7–M10; `scripts/benchmark_ollama.py` selects local models. V2 functions are also exposed in FastAPI/Vue; engine strategies remain `manual`, `fifo`, `cpsat`, `cnp`. Installation and commands remain in the README.

<a id="interface-web-v2"></a>
### Web interface V2

Navigation preserves V1 state and loads V2 on first opening. Public labels are "Warehouse Simulation", "Scientific Experiments", "Problem", "Method", and "Instance"; internal identifiers and routes M7–M10 remain stable. The History API synchronizes `view` and `problem` in the URL; `popstate` restores the view and the problem. Ticks and minor parameters do not create history entries. Results survive view changes during session but not page reload. `frontend/src/components/Experiments.vue` configures M7–M10 and follows computations; `MapfReplay.vue` displays the distinct MAPF physics. Warehouse and EventLog are reused for engine traces; modified parameters invalidate results and trigger local replay. `api/experiments.py` exposes `/api/v2/catalog`, `/api/v2/ollama`, `/api/v2/jobs` and `/api/v2/jobs/{id}`; `api/experiment_planning.py` adapts M7/M9. Historical scientific algorithms and results remain unchanged.

Instances, parameters, budgets, and file identifiers are validated before being queued. The catalog inspects the content of authorized JSON files, without inferring their compatibility from the file name. Selection and launches share backend validation; a valid but infeasible instance remains selectable. The simulation instance is proposed as the 'User Instance' after this check. Result invalidation respects native field events; expired asynchronous responses are ignored. Two workers, four active jobs, up to 24 results retained for one hour, and up to eight session checkpoints; no persistence. Historical checkpoints remain read-only. M7 verifies nominal replay and preserves rolling-horizon fallbacks; the robust comparison replays nominal/minimax queues within the same scenarios. M8 executes only the allocation confirmed at the initial tick. M9 publishes only verified plans. M10 distinguishes training, validation selection, and fixed evaluation on a separate instance. Scientific statuses remain distinct from the technical end of the job. Complete benchmarks and oracle MAPF remain in CLI.

Ollama is optional, on a fixed local URL. Selected and digests are reused; manual installation choice is possible, preloading with distinct decision delays and active tracking. No automatic downloads or RL model training. Errors, budgets, and fallbacks are measured.

Final validation on **October 15, 2026**: **499 Python tests**, **101 Vue tests**, TypeScript/build Vite, compilation/imports, and `pip check` passed. No test failed or skipped; a known deprecation warning for Starlette/HTTPX. Chrome verified V1/comparison, M7 nominal/sliding/robust, M8 DSA/messages, M9 CBS/priority, and M10 Q, as well as navigation and rejections. V1 witnesses on `complex_comparison.json` reproduce historical metrics and identical traces upon repetition. The 34 scientific V1 files are identical to `v1.0.0`; complete benchmarks are not re-run.

Real Ollama was verified via Chrome with both selected models and their exact digests: M8 produces four structured/legal responses, M10 produces two, with no rejection or fallback. Each execution applies two assignments to the engine and completes two tasks at tick 2; M10 ends with a return of −2, equal to the sum of rewards. Interface, API, and exports are consistent. Initial loads take approximately 41 seconds for M8 and 53 seconds for M10, outside decisional budget. These functional checks are distinct from the historical selection benchmark. Simulated tests also cover slow loading, invalid responses, timeouts, budgets, fallbacks, and expired follow-up responses after problem change.

<a id="fonctionnement-1"></a>
### Operation

**M7.** `plan_schedule` enforces completion of all observed tasks within a finite absolute horizon, including arrivals, precedences, BFS trajectories, capacities, handling, and stations. Active commitments are frozen. Stations retain priority ID, release at the next tick, and reservation during suspension. Lexicographic objective: total delay, then makespan; the second solution requires the proven optimum of the first. Deterministic time budget via CP-SAT per phase, one worker, seed 0; this is not a global hard deadline. A second phase without a solution may preserve the feasible plan from the first phase.

`solve_schedule` is an offline oracle that knows all tasks/arrivals and the provided schedule; the nominal benchmark excludes failures. `execute_schedule` replays assignment dates in V1, then performs rejection and collection/delivery discrepancies checks; it does not enforce movements. `run_rolling` observes only revealed tasks and current state, without a future schedule. Periodic or task/availability-based rescheduling occurs up to `min(horizon, tick + window)`, assuming an optimistic future availability. If no solution is available, FIFO resumes without losing the backlog or commitments.

`optimize_priority` enumerates fixed assignments and orders per robot, up to `max_policies`. The same policy is replayed in each declared schedule; only a revealed head of the queue can be assigned to a robot that is available. All scenarios must terminate. The minimax minimizes the worst delay, then the worst makespan. `OPTIMAL` requires a complete enumeration of this class; a truncated search yields `FEASIBLE` or `UNKNOWN`.

**M8.** Each `LocalAgent` receives its detached robot, the grid, and the public tasks `PENDING` that have already arrived, without the state or costs of other robots or future events. Domain: waiting at `None` and compatible tasks; individual cost equals two BFS distances plus two manipulations, without file forecasting. Waiting and conflict penalties are calibrated to prioritize conflict avoidance, then cardinality and cost. Domains are disseminated without cost; agents become neighbors if their domains share a task.

Independent initialization at minimum `(coût, ID tâche)`, followed by exchanges of chosen values. At each synchronous DSA-B round, a strict improvement is applied with probability `p`; equality preserves the choice. Oscillations are continuously counted. Confirmation exchanges `claim` then `confirmed`: the agent with the smallest ID retains a disputed task, the losers wait without reassignment. Neither the central solver nor the repair engine rejects actions to resolve conflicts. The budget accounts for transmissions, including domain discovery, and reserves space for confirmation; logical messages are counted separately.

The LLM experience reuses domains, messages, and confirmation, but directly applies the returned legal action without a DSA improvement filter or probabilistic selection. `UNCHANGED_LLM` means only unchanged actions. The solver remains a fixed allocation: `--execute` replays it in V1, without transforming DSA/LLM into a continuous strategy of the engine.

**M9.** A plan includes departure at tick 0, followed by cardinal movements or waits; vertex conflicts and simultaneous edge exchanges are forbidden. A goal is absorbing upon first visit, even at departure; its occupation persists until the horizon. Cost: sum of arrival ticks, including waits before arrival.

Priority scheduling reserves nodes `(position, tick)`, dated edges, and persistent goals; A* also verifies the goal's safety over the remaining horizon. CBS creates two branches at the first conflict; each branch constrains and replans only one of the affected agents; its queue minimizes the sum of costs. `validate_plan` independently controls cells, movements, goals, horizons, and collisions before any metric publication. `SOLVED` certifies a valid plan; `optimal` pertains only to CBS/oracle in this model. `NO_SOLUTION_WITHIN_HORIZON` proves the absence of a solution in the horizon; `PRIORITY_FAILED` proves no infeasibility and `BUDGET_EXHAUSTED` remains an unknown result.

M10. Local observation: grid, robot-owned, public tasks `PENDING`, own costs and legal mask, without other robots, future tasks, resources, or future schedule. Action: task ID or `None`; one robot omitted is waiting. Proposals use the same observation prior to transition; concurrent claims are resolved by robot ID. Actions outside the mask are rejected, conflicts are counted separately. After accepted assignments, `step` advances exactly one tick V1, without collision avoidance.

Common reward after transition: the negative of the number of non-completed arrived tasks, including those in progress; no direct terms for delay, distance, or conflict. `terminated` requires all completed tasks, even future ones; `truncated` signals the horizon reached without completion. Update Q does not extend the value beyond a termination/truncation.

Tables use the exact observation, without absolute tick or robot ID, with task age and remaining deadline; grid and task IDs remain in the key. Independent Q-learning, `alpha=0.2`, `gamma=1`, uniform exploration and random tie-breaking among legal maxima. The recorded experience trains 60 episodes for each seed 0/1/2 across four instances, with epsilon ranging from 0.30 to 0.05. Checkpoints every ten episodes; best average return on two validation instances, first in case of tie. JSON tables are saved, reloaded, and frozen before the three test instances and two out-of-distribution instances. Evaluation creates no state and performs no update.

Ollama. The client calls `/api/chat` with a JSON schema limiting `action` to legal values; it then verifies exactly this key and its domain membership. M8 transmits a proper observation and neighboring messages; M10 transmits its local observation. In case of error, invalid response, or budget exhaustion, the client chooses waiting `None`, counted and traced as fallback, never as a successful generation. Calls, tokens, latencies, server durations, and raw decisions remain traceable. Request delay, number of calls, and scenario budget are distinct; the socket timeout is not a strict hard boundary, overruns are recorded, and responses exceeding the scenario budget are rejected. `--preload` has a separate loading delay, outside the decisional budget. No server or automatic download is present.

The selection first compares local observations that are identical, then episodes whose trajectories may diverge. Eligibility on validation: 100% of structured and legal decisions, no fallback, at least one non-empty action actually applied. Lexicographic ranking by intrinsic quality for M8/M10, latency then disk size. Pre-fixed choices before independent test: M8 `qwen2.5:7b-instruct-q4_K_M`, M10 `qwen3:4b-instruct-2507-q4_K_M`. `--selection` loads registered parameters and names, then verifies exact name/digest via `/api/tags`; a change in digest triggers a rejection. Explicit CLI options take precedence; `--model` deliberately selects a different model without requiring the digest of the initial choice.

<a id="vérifications-1"></a>
### Verifications

The V2 audit recorded in the [Ollama report](results/v2/ollama-selection-20261015/report.json) validates **415 Python tests**, compilation/imports and `pip check`. The **25 Vue tests** and TypeScript/build Vite are those from the V1 validation of October 14, 2026; the frontend unchanged has not been retested during this Ollama phase. Historical V1 reproductions show no divergence; the V2 audit also records 34 historically controlled files verified by digest, with no change. These historical results are not re-executed here.

M7 tests compare against manually dispatched oracles, then verify stations, suspensions, backlogs, and absence of future information. The [M7 export](results/v2/m7.json) preserves a robust compromise validated: worst delay reduced from 11 to 9 for the same-class nominal policy, with increased nominal cost; incomplete out-of-set executions remain explicitly documented. M8 tests verify observations, messages, oscillations, and confirmation; the [M8 export](results/v2/m8.json) contains 780 DSA executions all feasible after confirmation, without general optimality or disappearance of conflicts during search.

M9 tests verify collisions, goals, reservations, and budgets; the [M9 export](results/v2/m9.json) contains no invalid plan and preserves 144 exhaustive CBS/oracle 2×2 comparisons without disagreement. M10 tests verify transitions/rewards, masks, checkpoint saving, and freezing. The [M10 export](results/v2/m10.json) and the checkpoints `m10-q-seed-*.json` from the same folder show zero previously seen decision observations on validation/test/out-of-distribution: Q exactly matches the legal random policy with paired seeds. No demonstrated learned generalization.

Automated client tests cover controls, errors, and budgets; real-world experiments are distinct. The previous [M8 diagnosis](results/v2/m8-ollama.json) retains two timeouts with no successful generated decision. Recent validation proves four successful M8 and two successful M10 decisions, legal and without fallback, with actions applied: [M8 proof](results/v2/ollama-selection-20261015/m8-validation-real.json) and [M10 proof](results/v2/ollama-selection-20261015/m10-validation-real.json). The report selects on two validation instances and evaluates on two other test instances, repeated twice; the repetitions are not independent instances. On test, the LLM M8 retains one task against two for DSA/CNP/CP-SAT; M10 completes tasks without demonstrated general superiority. A successful loading alone proves no real decision.

<a id="limites-connues-1"></a>
### Known limits

- M7: temporal tables intended for short horizons; short windows sometimes infeasible, with FIFO fallback. Limited robustness to declared schedules and fixed queue classes; no certificate of optimality if enumeration is truncated; no guarantee outside the set.
- M8: Synchronous DSA may oscillate, without general guarantee of convergence or optimality. Confirmation disadvantages larger IDs; losing robots remain unassigned; simulated network.
- M9: Isolated MAPF, without integration of tasks/manipulations/stations V1. Incomplete and order-sensitive prioritized planning; certificates limited to horizon and absorbing goals. The budget accumulates expansions A*/CBS/oracle; the wall time is cooperatively controlled. An exhausted budget does not prove infeasibility. Oracle limited to four agents, sixteen free cells, and a sixteen-tick horizon.
- M10: Exact tabular encoding is poorly transferable, without guaranteed Markov state. Evaluated instances have no obstacles, stations, suspensions, or delayed arrivals; no demonstrated tabular generalization learned, even on the out-of-distribution small set.
- Ollama: Runtime and loading depend on hardware, synthetic small sets, and legal outputs without guaranteed good coordination. Devstral 24B exceeds the preloading budget of 120 seconds, without on-the-fly quality measurement; no general superiority demonstrated.

Comparisons respect information boundaries: oracle M7 offline, V1 global baselines, local policies M8/M10. Synthetic small domains, no general superiority demonstrated.
