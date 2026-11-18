[English](README.md) | [Français](README.fr.md) | [简体中文](README.zh-CN.md)

<a id="taskfleet--multi-agent-warehouse-simulation"></a>
# TaskFleet - 多智能体仓库模拟

TaskFleet 是一个确定性仓库仿真器，结合多智能体系统、运筹学与规划。统一的 Python 引擎用于比较 FIFO 分配、CP-SAT 优化与合同网协议（CNP）协调。

<a id="fonctionnalités"></a>
## 功能

- 离散网格包含障碍物、自主机器人、容量限制以及取货/配送任务。
- 共享站点，延迟到达，以及机器人的暂停/恢复。
- FIFO 分配、即时 CP-SAT 优化和本地 CNP 协商。
- 可复现的基准测试、指标和 CSV/JSON 导出。
- Vue 界面包含 SVG 仓库视图、播放/暂停、速度控制、时间导航，以及机器人、任务、站点、路径和 CNP 消息的检查功能；回放期间无需请求 API。
- 编辑、基于种子的生成、JSON 导入/导出以及三种策略的对比。

<a id="technologies"></a>
## 技术栈

Python 3.12+，OR-Tools，FastAPI/Uvicorn，Vue 3，TypeScript 和 Vite。测试：pytest，FastAPI TestClient，Vitest 和 Vue Test Utils。Matplotlib 用于基准测试图表是可选的。已验证环境：Python 3.13.1 和 Node 24.13.0。

<a id="installation"></a>
## 安装

PowerShell，从仓库根目录开始。安装 Python 3.12+ 和 Node 20（≥20.19）、22（≥22.12）或 24+：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt
Push-Location frontend
npm.cmd ci
Pop-Location
```

`constraints.txt` 固定已验证的 Python 版本；lockfile 固定了前端依赖。

<a id="installation-ollama--optionnelle"></a>
### 安装 Ollama - 可选

V1/V2 经典策略无需 LLM 即可运行。对于 M8/M10 扩展功能，需从 [Windows 官方网站](https://ollama.com/download/windows) 安装 Ollama，然后打开一个新的 PowerShell 终端。Windows 应用程序通常会自动在后台启动服务器；请参阅 [Windows 官方文档](https://docs.ollama.com/windows)。在 `http://127.0.0.1:11434` 处验证 CLI 和本地 API 响应：

```powershell
ollama --version
Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version'
```

第二条命令应返回版本信息。如果服务器无响应，请从「开始」菜单打开 Ollama 并重试。仅在当前端口尚未监听时才手动启动：

```powershell
ollama serve
```

然后保持该终端打开，并在另一个终端中继续操作。查看已安装的模型，然后下载已保存的两个模型：

```powershell
ollama list
ollama pull qwen2.5:7b-instruct-q4_K_M
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama list
```

确切标签请参考官方目录：[模型 M8](https://ollama.com/library/qwen2.5:7b-instruct-q4_K_M) 和 [模型 M10](https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M)。TaskFleet 不会自动下载任何模型。性能和加载时间取决于硬件配置。随后运行 [LLM 示例](#extensions-llm)，使用 `--selection` 和 `--preload`；选择项将验证已安装的标识和哈希值。

<a id="utilisation"></a>
## 使用方法

第一个终端，从根目录开始：

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

第二个终端：

```powershell
Set-Location frontend
npm.cmd run dev
```

打开 [应用程序](http://127.0.0.1:5173) 或 [API 文档](http://127.0.0.1:8000/docs)。Vite 会将 `/api` 代理到 FastAPI。使用 `Ctrl+C` 停止每个服务。

V1 Web 请求限制为 100 KB、20 × 20 网格、8 个机器人、24 个任务、400 个障碍物、30 个资源、50 个不可用时段、300 个时间步，以及每阶段 1 个确定性秒的 CP-SAT 预算。CLI 实验保留其科研参数。

导航提供 **仓库模拟** 和 **科学实验**。模拟保留其编辑器、生成过程、三种策略和重玩功能。在实验中，选择 **问题**、**方法**、**实例**，然后点击 **运行实验** 或 **比较方法**。后端会验证实例与方法的兼容性；在模拟中创建或导入的实例若兼容，则被推荐为 **用户实例**。四个问题分别是 **调度**、**分布式分配**、**多智能体计划** 和 **协作学习**。返回/前进、重新加载和直接链接可恢复视图和问题，例如 `?view=experiences&problem=ordonnancement`。结果保留在页面内存中；重新加载需重新启动实验。

- **M7**：标称调度、滚动时域与极小极大优化；展示计划/实际日期、经过验证的计划、重新规划及回退方案。可使用 `m7.json` 和默认预算 0,2；鲁棒模式使用 `m7_robust.json` 与 `robust_scenarios.json`，再选择回放场景。比较时区分离线参考解与当前已揭示的状态；标称/鲁棒队列采用相同的预设日历进行比较。
- **M8**：DSA 或 LLM 协调；可检查变量域、决策、消息和确认。仅执行在初始时间步确认的分配，之后不再重新分配。FIFO/CNP/CP-SAT 也在同一观测条件下进行比较。
- **M9**：优先级规划或 CBS；展示网格、轨迹、等待、起点/目标及逐时间步回放。MAPF 的物理规则禁止碰撞，该模块与 V1 的规则分开。
- **M10**：合法随机策略、启发式策略、使用**固定检查点**的固定 Q 策略，或 LLM 策略。**短程表格型训练**最多运行 30 个回合，在验证集上选择策略，再在独立实例上评估固定策略。会话检查点可用于 Q 策略；观测、动作、奖励和事件均可检查。

对于 M8/M10 的 LLM 变体，点击**检查 Ollama**，然后保留基准测试所用的模型选择，或选择已安装的模型。系统会核验所选名称及摘要。预加载具有独立超时（默认 120 s），与决策预算分开；加载期间仍可查看进度。不会自动下载模型，也不会对 Ollama 模型进行 RL 训练。经典方法无需 Ollama 服务器即可使用。

结果和比较可导出为 JSON；播放/暂停、速度和播放条可在本地回放轨迹。不可行性、预算耗尽、执行不完整和错误均明确显示。API 增加 `GET /api/v2/catalog`、`GET /api/v2/ollama`、`POST /api/v2/jobs` 和 `GET /api/v2/jobs/{id}`，并保留 V1 路径。最多两个工作节点同时执行四个活跃实验；最多保存 24 个结果一小时。结果和会话检查点保留在内存中，重启后消失。完整的基准测试、完整的 MAPF oracle 和大规模训练仍保留在 CLI 中。

最终验证日期：**2026年10月15日**：**499个Python测试**，**101个Vue测试**，TypeScript、Vite构建、编译/导入及`pip check`均成功。Chrome已验证了模拟过程以及V1版本的三种策略、四个科学问题、对比分析、重试机制和原生导航。两组Ollama扩展程序均生成了真正执行的结构化且合法的决策，无需辅助：在分布式分配中发出四次调用，在协作学习中发出两次调用；每次执行均完成两项任务。这些功能测试并不代表对模型的新比较性评估。

从根目录开始的M5模拟与基准测试：

```powershell
.\.venv\Scripts\python.exe -m taskfleet examples/allocation.json --strategy cpsat --verify-repeat
.\.venv\Scripts\python.exe -m taskfleet.benchmark --repeats 2 --json "$env:TEMP/taskfleet-m5.json" --csv "$env:TEMP/taskfleet-m5.csv"
```

命令行界面也接受`--strategy fifo`和`--strategy cnp`。为了重现扩展基准测试：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-benchmark.txt -c constraints.txt
.\.venv\Scripts\python.exe -m scripts.benchmark_extended --seeds 11 23 37 51 79 --repeats 2 --output "$env:TEMP/taskfleet-v1" --plots --compare docs/results/v1/report.json
```

测试与验证：

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

目录`--basetemp`专用于pytest，该工具会将其在仓库之外重新创建。V1版本验证日期：**2026年10月14日**：**245个Python测试**，**25个Vue测试**，TypeScript和Vite构建均成功。

<a id="résultats"></a>
## 结果

扩展基准测试：**87个独立实例**，**92个解决方案场景**，**552次模拟**；**468次完整**，**84次不完整**，无技术错误或无效决策。五个场景`low_budget`复用了实例`base`。重现过程保留了实例、确定性指标、状态以及轨迹指纹。

在 `examples/complex_comparison.json` 上，三种策略完成 18 项任务：

|指标|FIFO|CP-SAT|CNP|
|---|---:|---:|---:|
|最大跨度（时钟周期）| 154 | 114 | 142 |
|总延迟（时钟周期）| 437 | 179 | 365 |
|距离（情况数）| 340 | 279 | 312 |
|逻辑消息| 0 | 0 | 1919 |

这些观察仅适用于所研究的场景。CP-SAT 优化当前分配，但未证明全局动态最优性；CNP 为同步模拟，不涉及真实分布式网络。不模拟物理碰撞。未完成运行的 makespan 保持未定义。[结果、假设、图表与复现命令](docs/benchmark.zh-CN.md)详细说明了样本数量与限制。

<a id="état-du-projet"></a>
## 项目状态

**V1：M0 至 M6 已完成。** 请参阅 [路线图](docs/roadmap.zh-CN.md)。接下来的 Codex 会话必须明确读取 [技术文档](docs/memory.zh-CN.md)；该文档不会像仓库指令一样自动加载。

**V2 - 已完成。** M7 至 M10 已实现、测试并评估：

- [M7](docs/results/v2/m7.json)：时间调度、滑动时间界和鲁棒优化。
- [M8](docs/results/v2/m8.json)：分布式 DSA 优化和可选的本地 LLM 代理。
- [M9](docs/results/v2/m9.json)：多智能体路径规划、优先级规划和 CBS。
- [M10](docs/results/v2/m10.json)：表格协同学习和可选的本地 LLM 政策。

保证仅限于已经评估的问题范围，尚未证明普遍优越性。M10 尚未证明通过学习获得了泛化能力。

V2 方法也已集成到上述网页界面中。现有的 CLI 命令仍然可用，并且默认情况下无需 Ollama 即可运行：

```powershell
.\.venv\Scripts\python.exe -m taskfleet.scheduling examples/m7.json --method nominal
.\.venv\Scripts\python.exe -m taskfleet.scheduling examples/dynamic.json --method rolling --window 18 --frequency 1
.\.venv\Scripts\python.exe -m taskfleet.scheduling examples/m7.json --method robust --scenarios examples/m7/scenarios.json
.\.venv\Scripts\python.exe -m taskfleet.dcop examples/allocation.json --compare --execute --rounds 20 --probability 0.7 --seed 11
.\.venv\Scripts\python.exe -m taskfleet.mapf examples/mapf/crossing.json --algorithm cbs
.\.venv\Scripts\python.exe -m taskfleet.marl evaluate docs/results/v2/m10-test-0.json --policy q --checkpoint docs/results/v2/m10-q-seed-0.json --trace --json "$env:TEMP/taskfleet-m10-evaluation.json"
```

重现 V2 的基准测试：

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_scheduling --json "$env:TEMP/taskfleet-m7-benchmark.json"
.\.venv\Scripts\python.exe -m scripts.benchmark_dcop --output "$env:TEMP/taskfleet-m8-benchmark.json"
.\.venv\Scripts\python.exe -m scripts.benchmark_mapf --output "$env:TEMP/taskfleet-m9-benchmark.json"
.\.venv\Scripts\python.exe -m scripts.benchmark_marl --episodes 60 --seeds 0 1 2 --output "$env:TEMP/taskfleet-m10"
```

<a id="extensions-llm"></a>
### LLM 扩展

在[可选安装Ollama](#installation-ollama--optionnelle)之后，从根目录执行，并确保本地服务器处于活动状态。重用[固定选择](docs/results/v2/ollama-selection-20261015/report.json)：`--selection`在执行前验证模型的精确名称和哈希值；`--preload`加载已安装的模型，并与决策预算保持独立的延迟。若哈希值不同，则拒绝执行：重新进行选择，或明确指定一个模型使用`--model`。

```powershell
$selection = 'docs/results/v2/ollama-selection-20261015/report.json'
.\.venv\Scripts\python.exe -m taskfleet.dcop examples/allocation.json --method llm --selection $selection --preload --compare --execute --rounds 2 --output "$env:TEMP/taskfleet-m8-selected.json"
.\.venv\Scripts\python.exe -m taskfleet.marl evaluate examples/allocation.json --policy llm --selection $selection --preload --trace --json "$env:TEMP/taskfleet-m10-selected.json"
```

在新建文件夹中重现此选择，包含已安装的五个候选模型：

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_ollama --models qwen2.5-coder:7b qwen2.5-coder:14b devstral:24b qwen3:4b-instruct-2507-q4_K_M qwen2.5:7b-instruct-q4_K_M --url http://127.0.0.1:11434 --cold --repeats 2 --think false --output "$env:TEMP/taskfleet-ollama-benchmark"
```
