# Mémoire technique TaskFleet

**À lire explicitement au début des prochaines sessions Codex.** Ce fichier n'est pas
chargé automatiquement comme instruction du dépôt.

## État du projet

**V1, M0–M6 terminés.** Un moteur Python commun alimente CLI, benchmark et FastAPI ; Vue
rejoue les traces. Installation et utilisation : [README](../README.md) ; milestones : [roadmap](roadmap.md).

Branche `main`, historique organisé par M0–M6 puis documentation finale. Remote `origin` :
`https://github.com/Driw0x/TaskFleet.git`. Les étapes sont reconstruites rétrospectivement
depuis la V1 validée, pas des snapshots historiques originaux ; chaque étape est testée isolément.
Le générateur est introduit en M5 pour le benchmark étendu, puis exposé par l'interface M6.
Comparer `HEAD` à `origin/main` pour vérifier la synchronisation lors d'une reprise.

## Architecture et décisions

- `taskfleet/models.py`, `instances.py`, `paths.py` : états/configuration, validation stricte
  et génération, BFS déterministe. `engine.py` : transitions, invariants, événements et snapshots.
- `allocation.py`, `cnp.py` : décisions sur observations détachées ; `metrics.py`, `benchmark.py` :
  évaluation M5 commune ; `__main__.py` : CLI. `scripts/benchmark_extended.py` réutilise ces modules.
- `api/main.py` adapte directement le moteur ; validation/génération partagent le parseur Python.
  `frontend/src/App.vue` gère rejeu, inspection et comparaison ; composants SVG, éditeur et journal.

FIFO utilise `(arrival_tick, ID)` puis les robots par ID. CP-SAT maximise la cardinalité,
puis minimise distances BFS + deux manutentions, sans échéances ni files. Ses statuts
et son budget déterministe par phase restent explicites : optimum de l'affectation courante
seulement, `FEASIBLE` sans preuve, seconde phase sans solution conservant l'affectation faisable.
CNP utilise des offres locales, engagements et réannonces ; l'initiateur choisit les offres
reçues sans recalcul privé. Aucun réseau distribué. [Détails scientifiques](benchmark.md#problème-et-stratégies).

Vue calcule une trace par lancement puis navigue sans appels API. Réponses obsolètes ignorées,
timers nettoyés ; comparaison conservée entre ticks/stratégies, invalidée après édition,
import/génération réussie ou changement du budget. Le chemin bleu vient de `robot.path` ;
la liaison violette collecte/livraison est logique. Sélections et filtres suivent le snapshot,
les robots superposés restent dans leur case ; les broadcasts CNP restent dans le filtre robot.

## Fonctionnement

- Grille `[x, y]`, quatre voisins, une case par tick. BFS départagé droite/bas/gauche/haut ;
  chemin sans le départ, avec la destination. Affectation unique robot/tâche, charge compatible.
- Manutention positive à chaque extrémité, même si elles coïncident. Station réservée pendant
  la manutention, capacité en robots indépendante de la charge. Échéance informative.
- Ordre : allocation au tick courant ; incrément ; arrivées puis disponibilités ; une action
  par robot en ordre lexicographique ; libérations ; snapshot. Une affectation au tick n
  agit au tick n+1 ; une place libérée est réutilisable au tick suivant.
- Indisponibilités `[start_tick, end_tick)` : chemin, contrat, manutention et réservation
  conservés. Périodes adjacentes continues, chevauchements rejetés. Les observations excluent
  tâches futures et calendrier ; les affectations illégales ne modifient ni état ni événements.
- `run(until)` utilise un tick absolu ; sans argument, poursuit jusqu'à l'horizon.
  `finished` indique cet horizon, `all_tasks_completed` l'achèvement. Aucune livraison fictive.
- `trace()` contient configuration initiale, événements, snapshots et état courant détachés.
  Snapshot initial avant affectation ; suivants après tick. Vue utilise `current` au dernier tick.
  Une trace automatique se reproduit avec la même instance, stratégie et budget ; temps réels exclus.

Le JSON décrit la configuration initiale : `width`, `height`, `robots`, `tasks` requis ;
obstacles, ressources, horizon, graine et indisponibilités facultatifs. Robot : ID, position,
capacité ; tâche : ID, collecte, destination, charge/manutention, échéance et arrivée ;
indisponibilité : robot et intervalle. Entiers stricts, IDs uniques par type, une ressource
par case ; booléens/flottants, champs inconnus et clés JSON dupliquées rejetés.
`from_dict`, `loads_instance`, `load_instance` et `Instance.to_dict()` partagent le format.
Les [exemples](../examples/simple.json) et le parseur donnent les champs/defaults exhaustifs.

La validation garantit capacité/accessibilité, pas achèvement avant l'horizon. La génération
utilise une graine locale, conserve grille/obstacles/ressources/horizon, remplace robots/tâches
et retire l'ancien calendrier. Le moteur ne tire aucun hasard. CP-SAT : un worker, graine 0 ;
choix reproductibles aux versions/plateforme vérifiées. Mutations directes des modèles non prises en charge.

## Vérifications

Dernière validation exécutée le **14 octobre 2026** : **245 tests Python**, dont **175 M0–M5**,
**25 tests Vue**, TypeScript/build Vite, compilation/imports et `pip check` réussis ; audit npm
sans vulnérabilité signalée. Les snapshots rétrospectifs M0–M6 sont également validés.
Benchmark M5 et étendu reproduits précédemment, agrégats/CSV cohérents et aucune divergence.
Résultats, effectifs et commandes : [benchmark](benchmark.md#reproduction) et [README](../README.md).

Versions Python dans `constraints.txt`, frontend dans le lockfile. Environnement vérifié :
Python 3.13.1, OR-Tools 9.15.6755, Node 24.13.0. Les versions figées exigent Python 3.12+.
Windows sandbox peut refuser TEMP ou `realpath` : utiliser des vérifications autorisées hors
sandbox, sans contournement dans le projet. Exports temporaires hors dépôt.

## Limites connues

Pas de collisions physiques, batterie, préemption, transfert ou prévision des files.
Une suspension peut monopoliser une station ; ordre lexicographique sans équité garantie.
Cinq graines synthétiques, makespan conditionnel à la complétion, temps variables :
pas de supériorité universelle ni d'optimum dynamique global.

API locale synchrone, traces complètes en mémoire, sans persistance serveur ; petites instances.
CORS limité au frontend local sur 5173. Erreurs internes journalisées et masquées au client ;
statut HTTP conservé pour les erreurs non JSON. Dépréciation Starlette/HTTPX dans TestClient.
Streamlit retiré ; Matplotlib optionnel pour les figures, pandas transitif d'OR-Tools.
