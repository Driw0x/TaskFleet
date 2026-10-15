# Mémoire technique TaskFleet

**À lire explicitement au début des prochaines sessions Codex.** Ce fichier n'est pas
chargé automatiquement comme instruction du dépôt.

## État du projet

**V1, M0–M6 terminés ; V2, M7–M10 terminés.** Un moteur Python commun alimente CLI, benchmark et FastAPI ; Vue
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

Validation V1 exécutée le **14 octobre 2026** : **245 tests Python**, dont **175 M0–M5**,
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

Routes V1 synchrones ; expériences V2 asynchrones et bornées, traces complètes en mémoire,
sans persistance serveur ; petites instances.
CORS limité au frontend local sur 5173. Erreurs internes journalisées et masquées au client ;
statut HTTP conservé pour les erreurs non JSON. Dépréciation Starlette/HTTPX dans TestClient.
Streamlit retiré ; Matplotlib optionnel pour les figures, pandas transitif d'OR-Tools.

## V2 — Terminée

### Architecture et décisions

- `taskfleet/scheduling.py` (M7) : ordonnancement temporel CP-SAT, horizon glissant et minimax
  sur files fixes. Les tables de transitions locales proviennent de `Simulation.step` ;
  les plans sont rejoués dans le moteur V1, sans seconde physique ni nouvelle stratégie V1.
- `taskfleet/dcop.py` (M8) : agents locaux, DSA-B synchrone et confirmation distribuée d'une
  allocation sur observation figée. Les références exhaustive/CP-SAT servent à l'évaluation,
  jamais à réparer DSA. `--execute` applique seulement les affectations confirmées à V1.
- `taskfleet/mapf.py` (M9) : problème séparé à départs/buts fixes, A* espace-temps,
  planification prioritaire, CBS, oracle joint exhaustif et vérificateur indépendant.
  Réutilise la grille V1 ; ne modifie pas les déplacements du moteur d'entrepôt.
- `taskfleet/marl.py` (M10) : `AllocationEnv` enveloppe `Simulation` en mode `manual` ;
  `QPolicy` conserve une table par robot, entraînée par `train` avec récompense commune.
  Domaine borné à quatre robots, huit tâches et une grille 8×8 ; politiques aléatoire,
  heuristique, Q et LLM.
- `taskfleet/ollama.py` : client HTTP commun M8/M10, inventaire, préchargement, décisions et
  métriques ; bibliothèque standard Python, sans SDK supplémentaire. `ollama_selection.py`
  charge le choix figé. LLM remplace la décision locale, sans entraîner de modèle.

Les scripts `scripts/benchmark_scheduling.py`, `benchmark_dcop.py`, `benchmark_mapf.py` et
`benchmark_marl.py` évaluent respectivement M7–M10 ; `scripts/benchmark_ollama.py` sélectionne
les modèles locaux. Les fonctions V2 sont aussi exposées dans FastAPI/Vue ; les stratégies du moteur
restent `manual`, `fifo`, `cpsat`, `cnp`. L'installation et les commandes restent dans le README.

### Interface web V2

La navigation conserve l'état V1 et monte V2 à sa première ouverture.
Les libellés publics sont « Simulation d’entrepôt », « Expériences scientifiques »,
« Problème », « Méthode » et « Instance » ; les identifiants et routes internes M7–M10 restent stables.
La History API synchronise `view` et `problem` dans l'URL ; `popstate` restaure la vue et le problème.
Ticks et paramètres mineurs ne créent aucune entrée d'historique. Les résultats survivent aux changements
de vue pendant la session, mais pas au rechargement de la page.
`frontend/src/components/Experiments.vue` configure M7–M10 et suit les calculs ;
`MapfReplay.vue` affiche la physique MAPF distincte. Warehouse et EventLog sont réutilisés
pour les traces du moteur ; paramètres modifiés invalidant les résultats, rejeu local.
`api/experiments.py` expose `/api/v2/catalog`, `/api/v2/ollama`, `/api/v2/jobs`
et `/api/v2/jobs/{id}` ; `api/experiment_planning.py` adapte M7/M9.
Les algorithmes et résultats scientifiques historiques restent inchangés.

Instances, paramètres, budgets et identifiants de fichiers sont validés avant mise en attente.
Le catalogue inspecte le contenu des JSON autorisés, sans déduire leur compatibilité du nom de fichier.
La sélection et les lancements partagent la validation backend ; une instance valide mais infaisable reste sélectionnable.
L'instance de la simulation est proposée comme « Instance utilisateur » après ce contrôle.
L'invalidation des résultats respecte les événements natifs des champs ; les réponses asynchrones périmées sont ignorées.
Deux workers, quatre jobs actifs, 24 résultats conservés une heure et huit checkpoints de session
au maximum ; aucune persistance. Les checkpoints historiques restent en lecture seule.
M7 vérifie le rejeu nominal et conserve les secours du glissant ; la comparaison robuste rejoue
les files nominales/minimax dans les mêmes scénarios. M8 n'exécute que l'allocation confirmée
au tick initial. M9 ne publie que des plans vérifiés. M10 distingue entraînement, sélection
sur validation et évaluation figée sur instance séparée. Les statuts scientifiques restent
distincts de la fin technique du job. Benchmarks complets et oracle MAPF restent en CLI.

Ollama est facultatif, sur URL locale fixe. Sélection et digests réutilisés, choix manuel installé
possible, préchargement avec délai distinct des décisions et suivi actif. Aucun téléchargement
automatique ni entraînement RL du modèle. Erreurs, budgets et secours sont mesurés.

Validation finale du **15 octobre 2026** : **499 tests Python**, **101 tests Vue**, TypeScript/build Vite,
compilation/imports et `pip check` réussis. Aucun test échoué ni ignoré ; avertissement de dépréciation
Starlette/HTTPX déjà connu. Chrome a vérifié V1/comparaison, M7 nominal/glissant/robuste, M8 DSA/messages,
M9 CBS/prioritaire et M10 Q, ainsi que la navigation et les rejeux. Les témoins V1 sur
`complex_comparison.json` reproduisent les métriques historiques et des traces identiques à la répétition.
Les 34 fichiers scientifiques V1 sont identiques à `v1.0.0` ; les benchmarks complets ne sont pas relancés.

Ollama réel a été vérifié depuis Chrome avec les deux modèles sélectionnés et leurs digests exacts :
M8 produit quatre réponses structurées/légales, M10 deux, sans rejet ni secours. Chaque exécution applique
deux affectations au moteur et termine deux tâches au tick 2 ; M10 termine avec un retour de −2,
égal à la somme des récompenses. Interface, API et exports concordent. Les chargements initiaux prennent
environ 41 s pour M8 et 53 s pour M10, hors budget décisionnel. Ces contrôles fonctionnels sont distincts
du benchmark de sélection historique. Les tests simulés couvrent aussi chargement lent, réponses invalides,
timeouts, budgets, secours et réponse de suivi périmée après changement de problème.

### Fonctionnement

**M7.** `plan_schedule` impose l'achèvement de toutes les tâches observées dans un horizon
absolu fini, avec arrivées, précédences, trajets BFS, capacités, manutentions et stations.
Les engagements actifs sont gelés. Les stations conservent priorité ID, libération au tick
suivant et réservation pendant suspension. Objectif lexicographique : retard total, puis
makespan ; la seconde résolution nécessite l'optimum prouvé de la première. Budget en temps
déterministe CP-SAT par phase, un worker et graine 0 ; ce n'est pas un délai mural global.
Une seconde phase sans solution peut conserver le plan faisable de la première.

`solve_schedule` est un oracle hors ligne connaissant toutes les tâches/arrivées et le
calendrier fourni ; le benchmark nominal retire les pannes. `execute_schedule` rejoue les
dates d'affectation dans V1, puis contrôle refus et écarts de collecte/livraison ; il ne force
pas les mouvements. `run_rolling` observe seulement tâches révélées et état courant, sans
calendrier futur. Replanification périodique ou sur changement de tâches/disponibilité,
jusqu'à `min(horizon, tick + window)`, avec disponibilité future supposée optimiste.
Si aucune solution n'est disponible, FIFO reprend sans perdre le backlog ni les engagements.

`optimize_priority` énumère affectations et ordres fixes par robot, jusqu'à `max_policies`.
Une même politique est rejouée dans chaque calendrier déclaré ; seule une tête de file
révélée peut être affectée à un robot disponible. Tous les scénarios doivent finir.
Le minimax minimise le pire retard, puis le pire makespan. `OPTIMAL` exige l'énumération
complète de cette classe ; une recherche tronquée donne `FEASIBLE` ou `UNKNOWN`.

**M8.** Chaque `LocalAgent` reçoit son robot détaché, la grille et les tâches publiques
`PENDING` déjà arrivées, sans état/coûts des autres robots ni événements futurs. Domaine :
attente `None` et tâches compatibles ; coût propre = deux distances BFS + deux manutentions,
sans prévision des files. Pénalités d'attente et de conflit calibrées pour privilégier
l'absence de conflits, puis la cardinalité et le coût. Les domaines sont diffusés sans coûts ;
les agents deviennent voisins si leurs domaines partagent une tâche.

Initialisation indépendante au minimum `(coût, ID tâche)`, puis échanges de valeurs choisies.
À chaque tour synchrone DSA-B, une amélioration stricte est appliquée avec probabilité `p` ;
une égalité conserve le choix. Les oscillations sont comptées sans arrêt spécifique.
La confirmation échange `claim` puis `confirmed` : le plus petit ID conserve une tâche
disputée, les perdants attendent sans réaffectation. Aucun rejet du moteur ni réparateur
central ne sert à résoudre les conflits. Le budget compte les transmissions, y compris
découverte des domaines et réserve pour confirmation ; messages logiques comptés séparément.

L'expérience LLM réutilise domaines, messages et confirmation, mais applique directement
l'action légale retournée, sans filtre d'amélioration DSA ni tirage de probabilité.
`UNCHANGED_LLM` signifie seulement actions inchangées. Le solveur reste une allocation figée :
`--execute` la rejoue dans V1, sans transformer DSA/LLM en stratégie continue du moteur.

**M9.** Un plan inclut le départ au tick 0, puis mouvements cardinaux ou attentes ;
conflits de sommet et échanges simultanés d'arête interdits. Un but est absorbant dès
la première visite, même au départ ; son occupation persiste jusqu'à l'horizon. Coût :
somme des ticks de première arrivée, attentes avant arrivée comprises.

La planification prioritaire réserve sommets `(position, tick)`, arêtes datées et buts
persistants ; A* vérifie aussi la sûreté du but sur l'horizon restant. CBS crée deux branches
au premier conflit ; chaque branche contraint et replanifie un seul des agents concernés ;
sa file minimise la somme des coûts. `validate_plan` contrôle indépendamment cellules,
mouvements, buts, horizon et collisions avant toute publication de métriques.
`SOLVED` certifie un plan valide ; `optimal` concerne seulement CBS/oracle dans ce modèle.
`NO_SOLUTION_WITHIN_HORIZON` prouve l'absence de solution dans l'horizon ; `PRIORITY_FAILED`
ne prouve aucune infaisabilité et `BUDGET_EXHAUSTED` reste un résultat inconnu.

**M10.** Observation locale : grille, robot propre, tâches publiques `PENDING`, coûts propres
et masque légal, sans autres robots, tâches futures, ressources ni calendrier futur.
Action : ID de tâche ou `None` ; un robot omis attend. Les propositions utilisent la même
observation avant transition ; les revendications concurrentes sont départagées par ID robot.
Les actions hors masque sont rejetées, les conflits comptés séparément. Après affectations
acceptées, `step` avance exactement un tick V1, sans évitement de collisions.

Récompense commune après transition : opposé du nombre de tâches arrivées non terminées,
y compris celles en cours ; aucun terme direct de retard, distance ou conflit. `terminated`
exige toutes les tâches terminées, même futures ; `truncated` signale l'horizon atteint
sans achèvement. La mise à jour Q ne prolonge pas la valeur au-delà d'une fin/troncature.

Les tables utilisent l'observation exacte, sans tick absolu ni ID robot, avec âge des tâches
et délai restant ; grille et IDs tâches restent dans la clé. Q-learning indépendant,
`alpha=0.2`, `gamma=1`, exploration uniforme et départage aléatoire des maxima légaux.
L'expérience enregistrée entraîne 60 épisodes pour chacune des graines 0/1/2 sur quatre
instances, avec epsilon de 0,30 à 0,05. Checkpoints tous les dix épisodes ; meilleur retour
moyen sur deux instances de validation, premier en cas d'égalité. Tables JSON sauvegardées,
rechargées et gelées avant les trois instances test et les deux hors distribution.
L'évaluation ne crée aucun état et n'effectue aucune mise à jour.

**Ollama.** Le client appelle `/api/chat` avec un schéma JSON limitant `action` aux valeurs
légales ; il vérifie ensuite exactement cette clé et l'appartenance au domaine.
M8 transmet observation propre et messages voisins ; M10 transmet son observation locale.
En cas d'erreur, de réponse invalide ou de budget épuisé, le client choisit l'attente `None`,
comptée et tracée comme secours, jamais comme génération réussie. Appels, tokens, latences, durées
serveur et décisions brutes restent traçables. Délai par requête, nombre d'appels et budget
scénario sont distincts ; le timeout socket n'est pas une borne murale stricte, les
dépassements sont enregistrés et les réponses dépassant le budget scénario rejetées.
`--preload` dispose d'un délai de chargement séparé, hors budget décisionnel.
Aucun serveur ni téléchargement automatique.

La sélection compare d'abord des observations locales identiques, puis des épisodes dont
les trajectoires peuvent diverger. Éligibilité sur validation : 100 % de décisions structurées
et légales, aucun secours, au moins une action non vide réellement appliquée. Classement
lexicographique par qualité propre à M8/M10, latence puis taille disque. Choix figés avant
test indépendant : M8 `qwen2.5:7b-instruct-q4_K_M`, M10 `qwen3:4b-instruct-2507-q4_K_M`.
`--selection` charge paramètres et noms enregistrés, puis vérifie nom exact/digest via
`/api/tags` ; un changement de digest provoque un refus. Les options CLI explicites priment ;
`--model` choisit délibérément un autre modèle sans exiger le digest du choix initial.

### Vérifications

L'audit V2 consigné dans le [rapport Ollama](results/v2/ollama-selection-20261015/report.json)
valide **415 tests Python**, compilation/imports et `pip check`. Les **25 tests Vue** et
TypeScript/build Vite sont ceux de la validation V1 du 14 octobre 2026 ; le frontend inchangé
n'a pas été retesté pendant cette phase Ollama. Les reproductions historiques V1 sont sans
divergence ; l'audit V2 consigne aussi 34 fichiers historiques contrôlés par empreinte,
sans changement. Ces résultats historiques ne sont pas des vérifications relancées ici.

Les tests M7 comparent à un oracle de dispatchs manuels, puis contrôlent stations,
suspensions, backlog et absence d'information future. L'[export M7](results/v2/m7.json)
conserve un compromis robuste validé : pire retard 9 contre 11 pour la politique nominale
de même classe, avec coût nominal accru ; les exécutions hors ensemble incomplètes restent
explicites. Les tests M8 contrôlent observations, messages, oscillations et confirmation ;
l'[export M8](results/v2/m8.json) contient 780 exécutions DSA toutes faisables après
confirmation, sans optimalité générale ni disparition des conflits pendant la recherche.

Les tests M9 vérifient collisions, buts, réservations et budgets ; l'[export M9](results/v2/m9.json)
ne contient aucun plan invalide et conserve 144 comparaisons exhaustives CBS/oracle 2×2
sans désaccord. Les tests M10 contrôlent transitions/récompenses, masques, sauvegarde et gel.
L'[export M10](results/v2/m10.json) et les checkpoints `m10-q-seed-*.json` du même dossier
montrent zéro observation décisionnelle connue sur validation/test/hors distribution :
Q égale exactement l'aléatoire légal avec les graines appariées. Aucune généralisation apprise démontrée.

Les tests automatisés du client couvrent contrôles, erreurs et budgets ; les expériences
réelles sont distinctes. L'ancien [diagnostic M8](results/v2/m8-ollama.json) conserve deux
timeouts sans décision générée réussie. La validation récente prouve quatre décisions M8
et deux M10 réussies, légales et sans secours, avec actions appliquées :
[preuve M8](results/v2/ollama-selection-20261015/m8-validation-real.json) et
[preuve M10](results/v2/ollama-selection-20261015/m10-validation-real.json).
Le rapport sélectionne sur deux instances de validation et évalue sur deux autres instances
test, répétées deux fois ; les répétitions ne sont pas des instances indépendantes.
Sur test, le LLM M8 retient une tâche contre deux pour DSA/CNP/CP-SAT ; M10 termine les tâches
sans supériorité générale démontrée. Un chargement réussi seul ne prouve aucune décision réelle.

### Limites connues

- M7 : tables temporelles destinées aux petits horizons ; fenêtres courtes parfois infaisables,
  avec secours FIFO. Robustesse limitée aux calendriers déclarés et à la classe de files fixes ;
  aucun certificat d'optimum si énumération tronquée, aucune garantie hors ensemble.
- M8 : DSA synchrone peut osciller, sans garantie générale de convergence ni d'optimalité.
  Confirmation défavorable aux grands IDs, perdants laissés sans tâche ; réseau simulé.
- M9 : MAPF isolé, sans intégration des tâches/manutentions/stations V1. Planification prioritaire
  incomplète et sensible à l'ordre ; certificats limités à l'horizon et aux buts absorbants.
  Le budget cumule les expansions A*/CBS/oracle ; le temps mural est contrôlé de façon coopérative.
  Un budget épuisé ne prouve aucune infaisabilité. Oracle limité à quatre agents, seize cellules
  libres et un horizon de seize ticks.
- M10 : encodage tabulaire exact peu transférable, sans état markovien garanti. Les instances
  évaluées n'ont ni obstacles, ni stations, ni suspensions, ni arrivées différées ; aucune
  généralisation tabulaire apprise démontrée, même sur le petit ensemble hors distribution.
- Ollama : temps et chargement dépendants du matériel, petits ensembles synthétiques et
  sorties légales sans garantie de bonne coordination. Devstral 24B dépasse le budget de
  préchargement de 120 s, sans mesure de qualité à chaud ; aucune supériorité générale démontrée.

Les comparaisons respectent les frontières d'information : oracle M7 hors ligne, baselines V1 globales,
politiques M8/M10 locales. Petits domaines synthétiques, sans supériorité générale démontrée.
