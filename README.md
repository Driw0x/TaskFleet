[English](README.md) | [Français](README.fr.md) | [简体中文](README.zh-CN.md)

<a id="taskfleet--multi-agent-warehouse-simulation"></a>
# TaskFleet - Multi-Agent Warehouse Simulation



TaskFleet is a deterministic warehouse simulator combining multi-agent systems, operations research, and scheduling. A common Python engine compares FIFO allocation, CP-SAT optimization, and local negotiation via the Contract Net Protocol (CNP).

<a id="fonctionnalités"></a>
## Features

- Discrete grid with obstacles, autonomous robots, capacities, and pickup/delivery tasks.
- Shared stations, delayed arrivals, and robot suspension/resumption.
- FIFO allocation, instant CP-SAT optimization, and local CNP negotiation.
- Reproducible benchmarks, metrics, and CSV/JSON exports.
- Vue Interface: SVG warehouse view, play/pause, speed controls, temporal navigation, and inspection of robots, tasks, stations, trajectories, and CNP messages, without API requests during replay.
- Editing, generation from seed, JSON import/export, and comparison of the three strategies.

<a id="technologies"></a>
## Technologies

Python 3.12+, OR-Tools, FastAPI/Uvicorn, Vue 3, TypeScript and Vite. Tests: pytest, FastAPI TestClient, Vitest and Vue Test Utils. Matplotlib is optional for benchmark graphs. Verified environment: Python 3.13.1 and Node 24.13.0.

<a id="installation"></a>
## Installation

PowerShell, from the root of the repository. Install Python 3.12+ and Node 20 (≥20.19), 22 (≥22.12) or 24+, as required:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt
Push-Location frontend
npm.cmd ci
Pop-Location
```

`constraints.txt` pins the verified Python package versions; the lockfile pins the frontend dependencies.

<a id="installation-ollama--optionnelle"></a>
### Ollama Installation - optional

The classic V1/V2 strategies work without LLM. For M8/M10 extensions, install Ollama from the [official Windows site](https://ollama.com/download/windows), then open a new PowerShell terminal. The Windows application typically starts the server in the background; see the [official Windows documentation](https://docs.ollama.com/windows). Verify the CLI and the local API response on `http://127.0.0.1:11434` :

```powershell
ollama --version
Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version'
```

The second command should return a version. If the server does not respond, open Ollama from the Start menu and retry. For manual startup, only if no server is already listening on this port :

```powershell
ollama serve
```

Leave this terminal open and proceed in another terminal. Check the installed models, then download the two models from the recorded selection :

```powershell
ollama list
ollama pull qwen2.5:7b-instruct-q4_K_M
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama list
```

The exact tags are listed in the official catalog: [model M8](https://ollama.com/library/qwen2.5:7b-instruct-q4_K_M) and [model M10](https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M). No model is automatically downloaded by TaskFleet. Performance and loading times depend on hardware. Run the [LLM examples](#extensions-llm) with `--selection` and `--preload`; the selection verifies the installed identifiers and digests.

<a id="utilisation"></a>
## Usage

First terminal, from the root :

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Second terminal :

```powershell
Set-Location frontend
npm.cmd run dev
```

Open [the application](http://127.0.0.1:5173) or [the API documentation](http://127.0.0.1:8000/docs). Vite forwards `/api` to FastAPI. Stop each server with `Ctrl+C`.

Navigation offers **Simulation d’entrepôt** (Warehouse Simulation) and **Expériences scientifiques** (Scientific Experiments). The simulation preserves its editor, generation, three strategies, and replay capability. In experiments, select **Problème** (Problem), **Méthode** (Method), **Instance**, then **Lancer l'expérience** (Launch experiment) or **Comparer les méthodes** (Compare methods). The backend validates compatibility between instances and methods; an instance created or imported into the simulation is proposed as **Instance utilisateur** (User instance) if it is compatible. The four problems are **Ordonnancement** (Scheduling), **Allocation distribuée** (Distributed Allocation), **Planification multi-agent** (Multi-Agent Planning), and **Apprentissage coopératif** (Cooperative Learning). Back/Forward, reload, and direct links restore the view and the problem, for example `?view=experiences&problem=ordonnancement`. Results remain in the page's memory; a reload requires relaunching the experiment.

- M7: nominal, sliding horizon, and minimax; planned versus actual dates, verified plans, replanning, and fallbacks. Try `m7.json` with the default budget of 0.2; for robust, try `m7_robust.json` and `robust_scenarios.json`, then select the replayed scenario. The comparison distinguishes offline oracle and revealed state; nominal and robust queues are compared within the same declared calendars.
- M8: DSA or LLM coordination; domains, decisions, messages, and confirmations. Only the allocation confirmed at the initial tick is executed, without any subsequent reassignment. FIFO/CNP/CP-SAT remain comparable on this same observation.
- M9: priority or CBS; grid, trajectories, waits, departures/goals, and replay per tick. The MAPF physics prevents collisions and remains distinct from V1.
- M10: legal random, heuristic, Q fixed with **Fixed Checkpoint**, or LLM. **Short tabular training** limits computation to 30 episodes, selects on validation, and evaluates the fixed policy on a distinct instance. The session checkpoint becomes selectable for Q; observations, actions, rewards, and events remain inspectable.

For LLM variants M8/M10, click **Vérifier Ollama** (Verify Ollama), then keep the benchmark selection or choose an installed model. The selected names and digests are verified. Preloading has its own delay (120 seconds by default), separate from decision budgets; tracking remains active during loading. No automatic model download or RL training of the Ollama model. Classical methods remain available without an Ollama server.

Results and comparisons can be exported in JSON; playback/pause, speed, and cursor replay traces locally. Infeasibility, budget exhausted, incomplete execution, and errors are explicitly reported. The API adds `GET /api/v2/catalog`, `GET /api/v2/ollama`, `POST /api/v2/jobs`, and `GET /api/v2/jobs/{id}`, preserving V1 routes. At most two workers execute four active experiments; 24 results are retained for one hour. Results and session checkpoints are in memory and disappear upon restart. Complete benchmarks, exhaustive MAPF oracle, and large trainings remain in CLI.

Final validation on **October 15, 2026**: **499 Python tests**, **101 Vue tests**, TypeScript, Vite build, compilation/import operations, and `pip check` successfully completed. Chrome verified the simulation and the three V1 strategies, the four scientific problems, the comparisons, the re-runs, and native navigation. The two Ollama extensions produced genuinely applied, structured, and legal decisions, without fallback: four calls in distributed allocation and two in cooperative learning; each execution completes two tasks. These functional checks do not constitute a new comparative evaluation of the models.

Simulation and benchmark M5, from the root:

```powershell
.\.venv\Scripts\python.exe -m taskfleet examples/allocation.json --strategy cpsat --verify-repeat
.\.venv\Scripts\python.exe -m taskfleet.benchmark --repeats 2 --json "$env:TEMP/taskfleet-m5.json" --csv "$env:TEMP/taskfleet-m5.csv"
```

The CLI also accepts `--strategy fifo` and `--strategy cnp`. To reproduce the extended benchmark:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-benchmark.txt -c constraints.txt
.\.venv\Scripts\python.exe -m scripts.benchmark_extended --seeds 11 23 37 51 79 --repeats 2 --output "$env:TEMP/taskfleet-v1" --plots --compare docs/results/v1/report.json
```

Tests and verifications:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp "$env:TEMP/taskfleet-v1-pytest"
.\.venv\Scripts\python.exe -m compileall -q taskfleet api scripts tests
.\.venv\Scripts\python.exe -m pip check
Push-Location frontend
npm.cmd test
npm.cmd run type-check
npm.cmd run build
npm.cmd ls --depth=0
npm.cmd audit
Pop-Location
```

The directory `--basetemp` is reserved for pytest, which recreates it outside the repository. V1 validation on **October 14, 2026**: **245 Python tests**, **25 Vue tests**, TypeScript and Vite build successfully completed.

<a id="résultats"></a>
## Results

Extended benchmark: **87 distinct instances**, **92 solution scenarios**, **552 simulations**; **468 complete**, **84 incomplete**, no technical errors or invalid decisions. The five scenarios `low_budget` reuse the instances `base`. The reproduction preserves instances, deterministic metrics, statuses, and trace fingerprints.

On `examples/complex_comparison.json`, the three strategies deliver the 18 tasks:

|Metric|FIFO|CP-SAT|CNP|
|---|---:|---:|---:|
|Makespan (ticks)| 154 | 114 | 142 |
|Total delay (ticks)| 437 | 179 | 365 |
|Distance (cases)| 340 | 279 | 312 |
|Logical messages| 0 | 0 | 1919 |

These observations pertain to the scenarios studied. CP-SAT optimizes the current assignment, without proof of global dynamic optimality; CNP is synchronous, without a real distributed network. Physical collisions are not simulated. An incomplete makespan remains undefined. The [results, hypotheses, graphics, and reproduction commands](docs/benchmark.md) detail the staffing and limitations.

<a id="état-du-projet"></a>
## Project status

**V1: M0 to M6 completed.** See the [roadmap](docs/roadmap.md). The next Codex sessions must explicitly read the [technical development log](docs/memory.md); this document is not automatically loaded as a repository instruction.

**V2 - Completed.** M7 to M10 implemented, tested, and evaluated:

- [M7](docs/results/v2/m7.json): temporal scheduling, sliding horizon, and robust optimization.
- [M8](docs/results/v2/m8.json): distributed optimization DSA and optional local LLM agents.
- [M9](docs/results/v2/m9.json): Multi-Agent Path Finding, priority planning, and CBS.
- [M10](docs/results/v2/m10.json): tabular cooperative learning and optional local LLM policy.

Guarantees are limited to the evaluated domains, without demonstrated general superiority. No learned generalization is proven for M10.

The V2 methods are also accessible via the web interface above. The existing CLI commands remain available and function without Ollama by default:

```powershell
.\.venv\Scripts\python.exe -m taskfleet.scheduling examples/m7.json --method nominal
.\.venv\Scripts\python.exe -m taskfleet.scheduling examples/dynamic.json --method rolling --window 18 --frequency 1
.\.venv\Scripts\python.exe -m taskfleet.scheduling examples/m7.json --method robust --scenarios examples/m7/scenarios.json
.\.venv\Scripts\python.exe -m taskfleet.dcop examples/allocation.json --compare --execute --rounds 20 --probability 0.7 --seed 11
.\.venv\Scripts\python.exe -m taskfleet.mapf examples/mapf/crossing.json --algorithm cbs
.\.venv\Scripts\python.exe -m taskfleet.marl evaluate docs/results/v2/m10-test-0.json --policy q --checkpoint docs/results/v2/m10-q-seed-0.json --trace --json "$env:TEMP/taskfleet-m10-evaluation.json"
```

Reproduce the V2 benchmarks:

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_scheduling --json "$env:TEMP/taskfleet-m7-benchmark.json"
.\.venv\Scripts\python.exe -m scripts.benchmark_dcop --output "$env:TEMP/taskfleet-m8-benchmark.json"
.\.venv\Scripts\python.exe -m scripts.benchmark_mapf --output "$env:TEMP/taskfleet-m9-benchmark.json"
.\.venv\Scripts\python.exe -m scripts.benchmark_marl --episodes 60 --seeds 0 1 2 --output "$env:TEMP/taskfleet-m10"
```

<a id="extensions-llm"></a>
### LLM extensions

After [optional installation of Ollama](#installation-ollama--optionnelle), run from the root directory with the local server active. Reuse the [fixed selection](docs/results/v2/ollama-selection-20261015/report.json): `--selection` verifies the exact model name and digest before execution; `--preload` loads the already installed model with a distinct delay from the decision budget. A different digest results in rejection: redo the selection or explicitly choose a model with `--model`.

```powershell
$selection = 'docs/results/v2/ollama-selection-20261015/report.json'
.\.venv\Scripts\python.exe -m taskfleet.dcop examples/allocation.json --method llm --selection $selection --preload --compare --execute --rounds 2 --output "$env:TEMP/taskfleet-m8-selected.json"
.\.venv\Scripts\python.exe -m taskfleet.marl evaluate examples/allocation.json --policy llm --selection $selection --preload --trace --json "$env:TEMP/taskfleet-m10-selected.json"
```

Reproduce this selection in a new directory, with the five already installed candidates:

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_ollama --models qwen2.5-coder:7b qwen2.5-coder:14b devstral:24b qwen3:4b-instruct-2507-q4_K_M qwen2.5:7b-instruct-q4_K_M --url http://127.0.0.1:11434 --cold --repeats 2 --think false --output "$env:TEMP/taskfleet-ollama-benchmark"
```
