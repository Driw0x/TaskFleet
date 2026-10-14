# TaskFleet — Multi-Agent Warehouse Simulation

TaskFleet est un simulateur déterministe d'entrepôt combinant systèmes multi-agents,
recherche opérationnelle et planification. Un moteur Python commun compare l'allocation
FIFO, l'optimisation CP-SAT et la coordination Contract Net Protocol (CNP).

## Fonctionnalités

- Grille discrète avec obstacles, robots autonomes, capacités et tâches de collecte/livraison.
- Stations partagées, arrivées différées et suspension/reprise des robots.
- Allocation FIFO, optimisation instantanée CP-SAT et négociation locale CNP.
- Benchmarks reproductibles, métriques et exports CSV/JSON.
- Interface Vue : entrepôt SVG, lecture/pause, vitesses, navigation temporelle et inspection
  des robots, tâches, stations, chemins et messages CNP, sans requête API pendant le rejeu.
- Édition, génération par graine, import/export JSON et comparaison des trois stratégies.

## Technologies

Python 3.12+, OR-Tools, FastAPI/Uvicorn, Vue 3, TypeScript et Vite.
Tests : pytest, FastAPI TestClient, Vitest et Vue Test Utils.
Matplotlib est optionnel pour les graphiques du benchmark.
Environnement vérifié : Python 3.13.1 et Node 24.13.0.

## Installation

PowerShell, depuis la racine du dépôt. Installer Python 3.12+ et Node 20 (≥20.19),
22 (≥22.12) ou 24+ :

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt
Push-Location frontend
npm.cmd ci
Pop-Location
```

`constraints.txt` fixe les versions Python vérifiées ; le lockfile fixe les dépendances frontend.

## Utilisation

Premier terminal, depuis la racine :

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Deuxième terminal :

```powershell
Set-Location frontend
npm.cmd run dev
```

Ouvrir [l'application](http://127.0.0.1:5173) ou la
[documentation API](http://127.0.0.1:8000/docs). Vite relaie `/api` vers FastAPI.
Arrêter chaque serveur avec `Ctrl+C`.

Simulation et benchmark M5, depuis la racine :

```powershell
.\.venv\Scripts\python.exe -m taskfleet examples/allocation.json --strategy cpsat --verify-repeat
.\.venv\Scripts\python.exe -m taskfleet.benchmark --repeats 2 --json "$env:TEMP/taskfleet-m5.json" --csv "$env:TEMP/taskfleet-m5.csv"
```

La CLI accepte aussi `--strategy fifo` et `--strategy cnp`.
Pour reproduire le benchmark étendu :

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-benchmark.txt -c constraints.txt
.\.venv\Scripts\python.exe -m scripts.benchmark_extended --seeds 11 23 37 51 79 --repeats 2 --output "$env:TEMP/taskfleet-v1" --plots --compare docs/results/v1/report.json
```

Tests et vérifications :

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

Le répertoire `--basetemp` est réservé à pytest, qui le recrée hors du dépôt.
Vérification finale : **245 tests Python**, **25 tests Vue**, TypeScript et build Vite réussis.

## Résultats

Benchmark étendu : **87 instances distinctes**, **92 scénarios de résolution**,
**552 simulations** ; **468 complètes**, **84 incomplètes**, aucune erreur technique ni
décision invalide. Les cinq scénarios `low_budget` réutilisent les instances `base`.
La reproduction conserve instances, métriques déterministes, statuts et empreintes des traces.

Sur `examples/complex_comparison.json`, les trois stratégies livrent les 18 tâches :

| Métrique | FIFO | CP-SAT | CNP |
|---|---:|---:|---:|
| Makespan (ticks) | 154 | 114 | 142 |
| Retard total (ticks) | 437 | 179 | 365 |
| Distance (cases) | 340 | 279 | 312 |
| Messages logiques | 0 | 0 | 1919 |

Ces observations concernent les scénarios étudiés. CP-SAT optimise l'affectation courante,
sans preuve d'optimalité dynamique globale ; CNP est synchrone, sans réseau distribué réel.
Les collisions physiques ne sont pas simulées. Un makespan incomplet reste indéfini.
Les [résultats, hypothèses, graphiques et commandes de reproduction](docs/benchmark.md)
détaillent les effectifs et les limites.

## État du projet

**V1 : M0 à M6 terminés.** Voir la [roadmap](docs/roadmap.md).
Les prochaines sessions Codex doivent lire explicitement la [mémoire technique](docs/memory.md) ;
ce document n'est pas chargé automatiquement comme instruction du dépôt.
