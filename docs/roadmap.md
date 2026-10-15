# Roadmap

M0 à M6 sont terminés et vérifiés.

La consolidation V1 audite ces milestones et étend leur évaluation, sans nouveau milestone.
Résultats et reproduction : [benchmark](benchmark.md).

## M0 — Modélisation du problème

Définir le modèle commun aux simulations et aux stratégies.

- [x] Définir l’environnement, les agents, les tâches et les ressources partagées.
- [x] Formaliser les contraintes de déplacement, de capacité et d’utilisation des ressources.
- [x] Définir le format des instances de simulation.

## M1 — Moteur de simulation

Exécuter une simulation reproductible dans un environnement discret.

- [x] Implémenter l’environnement discret et les obstacles.
- [x] Représenter les robots, les tâches et les déplacements.
- [x] Gérer l’accès aux ressources partagées.
- [x] Charger les instances de simulation.
- [x] Assurer la reproductibilité, notamment par le contrôle des graines aléatoires.
- [x] Tester les comportements du moteur avec pytest.

## M2 — Allocation centralisée

Développer et comparer les stratégies d’allocation et d’ordonnancement centralisées.

- [x] Implémenter la baseline heuristique FIFO.
- [x] Implémenter l’optimisation avec OR-Tools CP-SAT.
- [x] Comparer les allocations sur des instances communes.

## M3 — Coordination multi-agent

Permettre aux agents de négocier et d’attribuer les tâches.

- [x] Implémenter les décisions locales des agents.
- [x] Ajouter la communication entre agents.
- [x] Implémenter le Contract Net Protocol (CNP), de l’annonce à l’attribution des tâches.
- [x] Intégrer les négociations au moteur de simulation.

## M4 — Environnement dynamique

Adapter les décisions aux changements pendant la simulation.

- [x] Gérer l’arrivée de nouvelles tâches.
- [x] Gérer l’indisponibilité des robots.
- [x] Adapter l’allocation et les plans aux perturbations.

## M5 — Benchmark et évaluation

Évaluer les stratégies dans des conditions comparables.

- [x] Définir les scénarios expérimentaux et les métriques.
- [x] Exécuter des expériences reproductibles avec les stratégies implémentées.
- [x] Comparer les stratégies sur les mêmes scénarios.
- [x] Analyser et documenter les résultats réellement obtenus.

## M6 — Interface web interactive

Explorer les simulations et créer des instances avec Vue 3, TypeScript, SVG et FastAPI.

- [x] Visualiser l’entrepôt, les obstacles et les robots.
- [x] Rejouer les simulations et afficher les interactions entre agents.
- [x] Lire, mettre en pause, régler la vitesse et naviguer localement dans les snapshots.
- [x] Sélectionner robots, tâches et stations ; inspecter état, affectation, occupation et historique au tick courant.
- [x] Fournir une légende permanente, des filtres de tâches et distinguer chemins Python et relations collecte/livraison.
- [x] Permettre la sélection des stratégies implémentées.
- [x] Créer des instances personnalisées : robots, capacités, tâches, obstacles, dimensions et ressources.
- [x] Importer et exporter les instances au format défini en M0.
- [x] Visualiser les résultats des simulations et des comparaisons.
- [x] Afficher automatiquement une comparaison persistante avec métriques M5, erreurs et invalidation des résultats périmés.
- [x] Fournir une instance complexe reproductible avec des différences opérationnelles mesurées entre les trois stratégies.
- [x] Générer des instances aléatoires valides à dimensions fixes et graine locale, éditables et exportables.
- [x] Remplacer Streamlit par une API locale et un frontend réactif, sans modifier le moteur scientifique.

## V2 scientifique — Terminée

### M7 — Advanced & Robust Scheduling

Évaluer ordonnancement temporel, horizon glissant et robustesse sur scénarios finis.

- [x] Modéliser précédences, trajets, capacités, stations, arrivées et retards avec CP-SAT ; minimiser retard total puis makespan.
- [x] Rejouer les plans dans le moteur V1 : priorité ID, libération différée et réservation conservée pendant suspension.
- [x] Replanifier sans événements futurs, avec tâches révélées, backlog conservé et engagements actifs gelés.
- [x] Optimiser les files fixes par minimax : pire retard, puis pire makespan, avec achèvement dans chaque calendrier déclaré ; limiter les garanties aux scénarios et politiques énumérés.
- [x] Vérifier les microcas par oracle, distinguer statuts et budgets, comparer les baselines à information explicite et publier les [résultats reproductibles](results/v2/m7.json).

### M8 — Distributed Constraint Optimization

Évaluer l'allocation distribuée par décisions locales et échanges entre agents.

- [x] Implémenter DSA sur domaines tâche/attente, coûts privés et objectif cardinalité puis coût, sans optimisation centrale cachée.
- [x] Confirmer les affectations par messages, sans doublons ni recours aux rejets du moteur.
- [x] Vérifier les allocations par énumération et CP-SAT ; comparer FIFO/CNP, oscillations, budgets, messages et temps complet dans les [résultats reproductibles](results/v2/m8.json).
- [x] Valider l'extension expérimentale Ollama : décisions locales réelles, légales et traçables, modèle choisi sur validation puis testé séparément ; cardinalité inférieure aux références ([preuves](results/v2/ollama-selection-20261015/report.json)).

### M9 — Multi-Agent Path Finding

Planifier des trajectoires compatibles sur un MAPF isolé à destinations fixes.

- [x] Formaliser mouvements, attentes, conflits de sommets et échanges d'arêtes, avec buts absorbants et persistants.
- [x] Implémenter planification prioritaire, réservations et CBS ; vérifier les plans indépendamment et comparer aux optima d'un oracle exhaustif.
- [x] Borner les certificats au modèle et à l'horizon déclarés ; distinguer infaisabilité dans l'horizon, échec prioritaire et budget épuisé.
- [x] Comparer coûts, réussites, priorités et budgets dans les [résultats reproductibles](results/v2/m9.json), sans modifier la physique V1.

### M10 — Cooperative Multi-Agent Reinforcement Learning

Évaluer l'apprentissage coopératif de l'allocation dans un domaine tabulaire borné.

- [x] Réutiliser le moteur V1 avec observations locales, masques d'actions et départage commun aux politiques comparées.
- [x] Tester récompense commune sur les tâches révélées restantes, transitions, terminaison et troncature d'horizon.
- [x] Entraîner des Q-learners tabulaires, sauvegarder et recharger les politiques ; séparer entraînement, validation et test, avec sélection sur validation uniquement.
- [x] Évaluer les politiques figées sur plusieurs graines face aux heuristiques ; conserver le résultat négatif de généralisation dans les [résultats reproductibles](results/v2/m10.json).
- [x] Valider l'extension expérimentale Ollama : actions réelles, légales et traçables, modèle choisi sur validation puis testé séparément, sans supériorité générale démontrée ([preuves](results/v2/ollama-selection-20261015/report.json)).
