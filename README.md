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

### Installation Ollama — optionnelle

Les stratégies classiques V1/V2 fonctionnent sans LLM. Pour les extensions M8/M10,
installer Ollama depuis le [site officiel pour Windows](https://ollama.com/download/windows),
puis ouvrir un nouveau terminal PowerShell. L'application Windows lance normalement le serveur
en arrière-plan ; voir la [documentation Windows officielle](https://docs.ollama.com/windows).
Vérifier la CLI et la réponse de l'API locale sur `http://127.0.0.1:11434` :

```powershell
ollama --version
Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/version'
```

La seconde commande doit renvoyer une version. Si le serveur ne répond pas, ouvrir Ollama
depuis le menu Démarrer et réessayer. Pour un démarrage manuel, uniquement si aucun serveur
n'écoute déjà sur ce port :

```powershell
ollama serve
```

Laisser alors ce terminal ouvert et poursuivre dans un autre terminal. Consulter les modèles
installés, puis télécharger les deux modèles de la sélection enregistrée :

```powershell
ollama list
ollama pull qwen2.5:7b-instruct-q4_K_M
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama list
```

Les tags exacts figurent au catalogue officiel : [modèle M8](https://ollama.com/library/qwen2.5:7b-instruct-q4_K_M)
et [modèle M10](https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M).
Aucun modèle n'est téléchargé automatiquement par TaskFleet. Les performances et les temps
de chargement dépendent du matériel. Exécuter ensuite les [exemples LLM](#extensions-llm)
avec `--selection` et `--preload` ; la sélection vérifie les identifiants et les digests installés.

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

La navigation propose **Simulation d’entrepôt** et **Expériences scientifiques**.
La simulation conserve son éditeur, sa génération, ses trois stratégies et son rejeu.
Dans les expériences, choisir **Problème**, **Méthode**, **Instance**, puis **Lancer l'expérience**
ou **Comparer les méthodes**. Le backend valide la compatibilité des instances avec la méthode ;
l'instance créée ou importée dans la simulation est proposée comme **Instance utilisateur** si elle est compatible.
Les quatre problèmes sont **Ordonnancement**, **Allocation distribuée**, **Planification multi-agent**
et **Apprentissage coopératif**. Retour/Avancer, rechargement et liens directs restaurent la vue et le problème,
par exemple `?view=experiences&problem=ordonnancement`. Les résultats restent en mémoire de la page ;
un rechargement impose de relancer l'expérience.

- **M7** : nominal, horizon glissant et minimax ; dates prévues/réalisées, plans vérifiés,
  replanifications et secours. Essayer `m7.json` avec le budget par défaut de 0,2 ; pour
  le robuste, `m7_robust.json` et `robust_scenarios.json`, puis choisir le scénario rejoué.
  La comparaison distingue oracle hors ligne et état révélé ; les files nominales/robustes
  sont comparées dans les mêmes calendriers déclarés.
- **M8** : DSA ou coordination LLM ; domaines, décisions, messages et confirmations.
  Seule l'allocation confirmée au tick initial est exécutée, sans réaffectation ultérieure.
  FIFO/CNP/CP-SAT restent comparables sur cette même observation.
- **M9** : prioritaire ou CBS ; grille, trajectoires, attentes, départs/buts et rejeu par tick.
  La physique MAPF interdit les collisions et reste distincte de V1.
- **M10** : aléatoire légale, heuristique, Q figée avec **Checkpoint figé**, ou LLM.
  **Entraînement tabulaire court** limite le calcul à 30 épisodes, sélectionne sur validation
  et évalue la politique figée sur une instance distincte. Le checkpoint de session devient
  sélectionnable pour Q ; observations, actions, récompenses et événements restent inspectables.

Pour les variantes LLM M8/M10, cliquer **Vérifier Ollama**, puis conserver la sélection
benchmark ou choisir un modèle installé. Les noms et digests sélectionnés sont vérifiés.
Le préchargement possède son propre délai (120 s par défaut), distinct du budget des décisions ;
le suivi reste actif pendant le chargement. Aucun téléchargement automatique ni entraînement RL
du modèle Ollama. Les méthodes classiques restent disponibles sans serveur Ollama.

Résultats et comparaisons s'exportent en JSON ; lecture/pause, vitesse et curseur rejouent les
traces localement. Infaisabilité, budget épuisé, exécution incomplète et erreurs sont explicites.
L'API ajoute `GET /api/v2/catalog`, `GET /api/v2/ollama`, `POST /api/v2/jobs`
et `GET /api/v2/jobs/{id}`, en conservant les routes V1. Deux workers exécutent au maximum
quatre expériences actives ; 24 résultats sont conservés une heure. Résultats et checkpoints
de session sont en mémoire et disparaissent au redémarrage. Benchmarks complets, oracle MAPF
exhaustif et grands entraînements restent en CLI.

Validation finale du **15 octobre 2026** : **499 tests Python**, **101 tests Vue**, TypeScript,
build Vite, compilation/imports et `pip check` réussis. Chrome a vérifié la simulation et les
trois stratégies V1, les quatre problèmes scientifiques, les comparaisons, les rejeux et
la navigation native. Les deux extensions Ollama ont produit des décisions structurées et
légales réellement appliquées, sans secours : quatre appels en allocation distribuée et
deux en apprentissage coopératif ; chaque exécution termine deux tâches. Ces contrôles
fonctionnels ne constituent pas une nouvelle évaluation comparative des modèles.

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
Validation V1 du **14 octobre 2026** : **245 tests Python**, **25 tests Vue**, TypeScript et build Vite réussis.

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

**V2 — Terminée.** M7 à M10 implémentés, testés et évalués :

- [M7](docs/results/v2/m7.json) : ordonnancement temporel, horizon glissant et optimisation robuste.
- [M8](docs/results/v2/m8.json) : optimisation distribuée DSA et agents LLM locaux optionnels.
- [M9](docs/results/v2/m9.json) : Multi-Agent Path Finding, planification prioritaire et CBS.
- [M10](docs/results/v2/m10.json) : apprentissage coopératif tabulaire et politique LLM locale optionnelle.

Garanties limitées aux domaines évalués, sans supériorité générale démontrée.
Aucune généralisation apprise démontrée pour M10.

Les méthodes V2 sont aussi accessibles dans l'interface web ci-dessus. Les commandes CLI
existantes restent disponibles et fonctionnent sans Ollama par défaut :

```powershell
.\.venv\Scripts\python.exe -m taskfleet.scheduling examples/m7.json --method nominal
.\.venv\Scripts\python.exe -m taskfleet.scheduling examples/dynamic.json --method rolling --window 18 --frequency 1
.\.venv\Scripts\python.exe -m taskfleet.scheduling examples/m7.json --method robust --scenarios examples/m7/scenarios.json
.\.venv\Scripts\python.exe -m taskfleet.dcop examples/allocation.json --compare --execute --rounds 20 --probability 0.7 --seed 11
.\.venv\Scripts\python.exe -m taskfleet.mapf examples/mapf/crossing.json --algorithm cbs
.\.venv\Scripts\python.exe -m taskfleet.marl evaluate docs/results/v2/m10-test-0.json --policy q --checkpoint docs/results/v2/m10-q-seed-0.json --trace --json "$env:TEMP/taskfleet-m10-evaluation.json"
```

Reproduire les benchmarks V2 :

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_scheduling --json "$env:TEMP/taskfleet-m7-benchmark.json"
.\.venv\Scripts\python.exe -m scripts.benchmark_dcop --output "$env:TEMP/taskfleet-m8-benchmark.json"
.\.venv\Scripts\python.exe -m scripts.benchmark_mapf --output "$env:TEMP/taskfleet-m9-benchmark.json"
.\.venv\Scripts\python.exe -m scripts.benchmark_marl --episodes 60 --seeds 0 1 2 --output "$env:TEMP/taskfleet-m10"
```

### Extensions LLM

Après l'[installation optionnelle d'Ollama](#installation-ollama--optionnelle), exécuter depuis
la racine avec le serveur local actif. Réutiliser la
[sélection figée](docs/results/v2/ollama-selection-20261015/report.json) : `--selection` vérifie
le nom exact et le digest du modèle avant exécution ; `--preload` charge le modèle déjà installé
avec un délai distinct du budget des décisions. Un digest différent entraîne un refus :
refaire la sélection ou choisir explicitement un modèle avec `--model`.

```powershell
$selection = 'docs/results/v2/ollama-selection-20261015/report.json'
.\.venv\Scripts\python.exe -m taskfleet.dcop examples/allocation.json --method llm --selection $selection --preload --compare --execute --rounds 2 --output "$env:TEMP/taskfleet-m8-selected.json"
.\.venv\Scripts\python.exe -m taskfleet.marl evaluate examples/allocation.json --policy llm --selection $selection --preload --trace --json "$env:TEMP/taskfleet-m10-selected.json"
```

Reproduire cette sélection dans un dossier neuf, avec les cinq candidats déjà installés :

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_ollama --models qwen2.5-coder:7b qwen2.5-coder:14b devstral:24b qwen3:4b-instruct-2507-q4_K_M qwen2.5:7b-instruct-q4_K_M --url http://127.0.0.1:11434 --cold --repeats 2 --think false --output "$env:TEMP/taskfleet-ollama-benchmark"
```
