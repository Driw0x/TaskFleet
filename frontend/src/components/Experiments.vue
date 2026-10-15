<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import { download, request } from '../api'
import { data, display, list } from '../experiments'
import type { ExperimentCatalog, ExperimentJob, Milestone, OllamaStatus, ScientificData, ScientificResult } from '../experiments'
import { robotLabels, taskLabels, type Instance } from '../types'
import Warehouse from './Warehouse.vue'
import EventLog from './EventLog.vue'
import MapfReplay from './MapfReplay.vue'
import ScheduleDetails from './ScheduleDetails.vue'
import CoordinationDetails from './CoordinationDetails.vue'
import MarlDetails from './MarlDetails.vue'
import MarlTraining from './MarlTraining.vue'
import ComparisonCharts from './ComparisonCharts.vue'
import OllamaDetails from './OllamaDetails.vue'

const props = withDefaults(defineProps<{ active?: boolean; userInstance?: Instance | null }>(), { active: true })
const form = ref<HTMLFormElement | null>(null)
const milestones = [['m7', 'Ordonnancement'], ['m8', 'Allocation distribuée'], ['m9', 'Planification multi-agent'], ['m10', 'Apprentissage coopératif']] as const
const methods: Record<Milestone, [string, string][]> = {
  m7: [['nominal', 'Temporel nominal'], ['rolling', 'Horizon glissant'], ['robust', 'Robuste minimax']],
  m8: [['dsa', 'DSA classique'], ['llm', 'Coordination LLM Ollama']],
  m9: [['prioritized', 'Planification prioritaire'], ['cbs', 'CBS']],
  m10: [['random', 'Aléatoire légale'], ['heuristic', 'Heuristique locale'], ['q', 'Q-learning figée'], ['llm', 'Politique LLM Ollama'], ['train', 'Entraînement tabulaire court']],
}
const catalog = shallowRef<ExperimentCatalog | null>(null)
const compatibleCatalog = shallowRef<ExperimentCatalog | null>(null)
const checkingCompatibility = ref(false)
const compatibilityError = ref('')
let catalogRevision = 0
const milestone = defineModel<Milestone>('milestone', { default: 'm7' })
const method = ref(methods[milestone.value][0]![0])
const instanceId = ref('')
const horizon = ref(30)
const solverBudget = ref(.2)
const windowSize = ref(18)
const frequency = ref(3)
const scenarioSet = ref('')
const maxPolicies = ref(200)
const rounds = ref(20)
const probability = ref(.7)
const seed = ref(0)
const messageBudget = ref(10000)
const priorities = ref('')
const maxNodes = ref(10000)
const seconds = ref(2)
const checkpoint = ref('')
const episodes = ref(10)
const checkpointEvery = ref(5)
const alpha = ref(.2)
const trainingInstances = ref<string[]>([])
const validationInstances = ref<string[]>([])
const model = ref('')
const preload = ref(true)
const timeout = ref(30)
const loadTimeout = ref(120)
const callBudget = ref(8)
const scenarioBudget = ref(120)
const maxTokens = ref(64)
const ollama = shallowRef<OllamaStatus | null>(null)
const checkingOllama = ref(false)
const ollamaError = ref('')
const job = shallowRef<ExperimentJob | null>(null)
const submitting = ref(false)
const error = ref('')
const tick = ref(0)
const playing = ref(false)
const speed = ref(1)
const selectedRobot = ref<string | null>(null)
const selectedTask = ref<string | null>(null)
const selectedResource = ref<string | null>(null)
const scenarioIndex = ref(0)
let pollTimer: ReturnType<typeof setTimeout> | undefined
let playbackTimer: ReturnType<typeof setInterval> | undefined
let disposed = false
let executionRevision = 0

const entries = computed(() => milestone.value === 'm9' ? compatibleCatalog.value?.mapf_instances ?? []
  : compatibleCatalog.value?.warehouse_instances ?? [])
const chosenInstance = computed(() => entries.value.find(entry => entry.id === instanceId.value)?.instance)
const maximumHorizon = computed(() => milestone.value === 'm7' ? 60 : milestone.value === 'm10' ? 200 : milestone.value === 'm9' ? 100 : 300)
const running = computed(() => submitting.value || job.value?.status === 'queued' || job.value?.status === 'running')
const result = computed(() => job.value?.result)
const resultTitle = computed(() => result.value ? `${milestones.find(([id]) => id === result.value!.milestone)?.[1]} · ${methods[result.value.milestone].find(([id]) => id === result.value!.method)?.[1] ?? result.value.method}` : '')
const llm = computed(() => method.value === 'llm')
const training = computed(() => milestone.value === 'm10' && method.value === 'train')
const benchmarkModel = computed(() => milestone.value === 'm8' || milestone.value === 'm10' ? catalog.value?.selected_models[milestone.value] : '')
const selectedModel = computed(() => model.value || benchmarkModel.value)
const missingModel = computed(() => llm.value && ollama.value?.available && !ollama.value.models.some(item => item.name === selectedModel.value))
const scenarioRuns = computed(() => result.value?.scenario_runs ?? [])
const replayTrace = computed(() => scenarioRuns.value.length
  ? (scenarioRuns.value[scenarioIndex.value]?.trace as ScientificResult['trace']) ?? result.value?.trace : result.value?.trace)
const snapshot = computed(() => replayTrace.value?.current.tick === tick.value ? replayTrace.value.current
  : replayTrace.value?.snapshots.find(state => state.tick === tick.value))
const plan = computed(() => result.value?.plan ?? {})
const mapfPaths = computed(() => data(plan.value.paths) as Record<string, unknown[]>)
const maximum = computed(() => milestone.value === 'm9' ? Math.max(0, Number(result.value?.problem?.horizon ?? 0))
  : replayTrace.value?.current.tick ?? 0)
const hasReplay = computed(() => milestone.value === 'm9' ? !!plan.value.paths : !!replayTrace.value)
const events = computed(() => replayTrace.value?.events ?? [])
const robots = computed(() => Object.keys(snapshot.value?.robots ?? {}))
const tasks = computed(() => replayTrace.value?.instance.tasks.map(task => task.id) ?? [])
const currentEvents = computed(() => events.value.filter(event => event.tick === tick.value))
const participants = computed(() => new Set(currentEvents.value.flatMap(event => [event.sender, event.recipient]).filter((id): id is string => !!id && id !== 'all')))
const coordination = computed(() => result.value?.coordination ?? {})
const episode = computed(() => result.value?.episode ?? {})
const inspectedRobot = computed(() => selectedRobot.value ? snapshot.value?.robots[selectedRobot.value] : null)
const inspectedTask = computed(() => selectedTask.value ? snapshot.value?.tasks[selectedTask.value] : null)
const inspectedResource = computed(() => selectedResource.value ? snapshot.value?.resources[selectedResource.value] : null)
function select(kind: 'robot' | 'task' | 'resource', id: string | null) {
  selectedRobot.value = kind === 'robot' ? id : null
  selectedTask.value = kind === 'task' ? id : null
  selectedResource.value = kind === 'resource' ? id : null
}
const metricLabels: Record<string, string> = {
  tasks_completed: 'Tâches terminées', makespan: 'Makespan (ticks)', total_tardiness: 'Retard total',
  distance: 'Distance (cases)', sum_of_costs: 'Coût total', waits: 'Attentes',
  cardinality: 'Cardinalité', estimated_cost: 'Coût estimé', logical_messages: 'Messages logiques', inference_seconds: 'Temps de décision (s)',
  transmissions: 'Transmissions', rounds: 'Tours', total_seconds: 'Durée (s)',
  return: 'Retour cumulé', conflicts: 'Conflits de propositions', rejected_actions: 'Actions rejetées',
  replans: 'Replanifications', fallback_dispatches: 'Décisions de secours',
  terminated: 'Terminaison', truncated: 'Troncature', calls: 'Appels', successes: 'Générations valides',
  legal_actions: 'Actions légales', rejections: 'Rejets', fallbacks: 'Actions de secours',
  latency_seconds: 'Latence cumulée (s)', prompt_tokens: 'Tokens en entrée', generated_tokens: 'Tokens générés',
  mean_latency_seconds: 'Latence moyenne (s)', llm_actions_accepted: 'Actions LLM appliquées',
  selected_episode: 'Épisode retenu', validation_return: 'Retour de validation', training_seconds: 'Entraînement (s)',
  seconds: 'Recherche (s)', total_nodes: 'Nœuds explorés', feasible: 'Faisabilité', waiting_robot_ticks: 'Attentes de station (ticks robots)', solver_seconds: 'Solveur (s)',
  q_observations: 'Observations consultées (Q)', q_known_observations: 'Observations connues de la table Q',
  worst_tardiness: 'Pire retard (scénarios)', worst_makespan: 'Pire makespan (scénarios)', completed_scenarios: 'Scénarios terminés',
}
const metricKeys = computed(() => milestone.value === 'm7'
  ? ['tasks_completed', 'makespan', 'total_tardiness', 'waiting_robot_ticks', 'replans', 'fallback_dispatches', 'solver_seconds', 'total_seconds']
  : milestone.value === 'm8' ? ['cardinality', 'estimated_cost', 'logical_messages', 'transmissions', 'rounds', 'total_seconds']
  : milestone.value === 'm9' ? ['sum_of_costs', 'makespan', 'distance', 'waits', 'seconds', 'total_nodes']
  : ['return', 'tasks_completed', 'makespan', 'conflicts', 'rejected_actions', 'terminated', 'truncated', 'q_observations', 'q_known_observations'])
const metrics = computed(() => ({ ...plan.value, ...result.value?.robust, ...episode.value, ...coordination.value, ...data(coordination.value.final_quality),
  ...result.value?.rolling, ...result.value?.metrics, ...data(scenarioRuns.value[scenarioIndex.value]?.metrics) }))
const shownMetrics = computed(() => metricKeys.value.filter(key => metrics.value[key] !== undefined))
const primaryMetrics = computed(() => shownMetrics.value.filter(key => ({
  m7: ['tasks_completed', 'makespan', 'total_tardiness'],
  m8: ['cardinality', 'estimated_cost', 'rounds', 'transmissions'],
  m9: ['sum_of_costs', 'makespan', 'distance', 'waits'],
  m10: ['return', 'tasks_completed', 'conflicts', 'terminated'],
})[milestone.value].includes(key)))
const additionalMetrics = computed(() => shownMetrics.value.filter(key => !primaryMetrics.value.includes(key)))
const comparisonRows = computed<NonNullable<ScientificResult['comparison']>>(() => (result.value?.comparison ?? []).map(row => {
  const runs = list(row.scenario_runs).map(data)
  if (!runs.length) return { ...row, metrics: { ...data(row.coordination), ...row.plan, ...row.metrics } }
  const allComplete = runs.every(run => run.execution_status === 'valid')
  return { ...row, metrics: { worst_tardiness: Math.max(...runs.map(run => Number(data(run.metrics).total_tardiness))),
    worst_makespan: allComplete ? Math.max(...runs.map(run => Number(data(run.metrics).makespan))) : null,
    completed_scenarios: `${runs.filter(run => run.execution_status === 'valid').length} / ${runs.length}` } }
}))
const comparisonKeys = computed(() => [...new Set(comparisonRows.value.flatMap(row => Object.keys(row.metrics)))].filter(key => key in metricLabels))
const comparisonChartRows = computed(() => comparisonRows.value.map(row => ({ label: methodLabel(row.method), metrics: row.metrics })))
const comparisonChartMetrics = computed(() => {
  const keys = milestone.value === 'm8' ? ['cardinality', 'estimated_cost', 'logical_messages', 'rounds', 'total_seconds']
    : milestone.value === 'm10' ? ['return', 'tasks_completed', 'makespan', 'inference_seconds']
    : method.value === 'robust' ? ['worst_tardiness', 'worst_makespan'] : ['tasks_completed', 'makespan', 'total_tardiness']
  return keys.map(key => ({ key, label: milestone.value === 'm8' && key === 'total_seconds' ? 'Temps de décision (s)' : metricLabels[key]! }))
})
const statusLabels: Record<string, string> = {
  valid: 'Résultat vérifié', incomplete: 'Exécution incomplète', infeasible: 'Infaisable dans le modèle et l’horizon déclarés',
  budget_exhausted: 'Budget épuisé — résultat sans preuve complète', invalid: 'Résultat invalide',
  priority_failed: 'Échec de priorité — infaisabilité non prouvée', queued: 'En attente', running: 'En cours',
  completed: 'Calcul terminé', failed: 'Échec technique',
  ollama_error: 'Erreur Ollama — décisions de secours possibles',
}
function formatted(value: unknown) {
  return typeof value === 'boolean' ? value ? 'Oui' : 'Non' : typeof value === 'number' && !Number.isInteger(value) ? value.toFixed(4) : display(value)
}
function methodLabel(value: string) {
  return methods[milestone.value].find(([id]) => id === value)?.[1]
    ?? ({ fifo: 'FIFO', cnp: 'CNP', cpsat: 'CP-SAT', exhaustive: 'Énumération exhaustive', nominal_fixed_queues: 'Files fixes nominales' } as Record<string, string>)[value] ?? value
}
function failure(caught: unknown) { return caught instanceof Error ? caught.message : 'Erreur inconnue.' }
function pause() { playing.value = false; clearInterval(playbackTimer); playbackTimer = undefined }
function seek(value: number) { pause(); tick.value = Math.max(0, Math.min(maximum.value, value)) }
function play() {
  if (playing.value) return pause()
  if (!hasReplay.value || tick.value >= maximum.value) return
  playing.value = true
  playbackTimer = setInterval(() => { tick.value++; if (tick.value >= maximum.value) pause() }, 500 / speed.value)
}
function clearResult() {
  executionRevision++; submitting.value = false
  pause(); clearTimeout(pollTimer); job.value = null; error.value = ''; tick.value = 0; scenarioIndex.value = 0
  select('robot', null)
}
function parameterInput(event: Event) {
  // Selects and checkboxes update v-model on change, after the native input event.
  if (event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLInputElement
    && !['checkbox', 'radio'].includes(event.target.type)) clearResult()
}
function chooseInstance() {
  const preferred = milestone.value === 'm7' ? 'm7.json' : milestone.value === 'm9' ? 'crossing.json' : milestone.value === 'm10' ? 'm10-test-0.json' : 'allocation.json'
  if (!entries.value.some(entry => entry.id === instanceId.value)) {
    instanceId.value = entries.value.find(entry => entry.id.endsWith(preferred))?.id ?? entries.value[0]?.id ?? ''
    setInstanceDefaults()
  }
}
function setInstanceDefaults() {
  if (!chosenInstance.value) return
  horizon.value = Math.min(maximumHorizon.value, chosenInstance.value?.horizon ?? 30)
  priorities.value = milestone.value === 'm9' && chosenInstance.value && 'agents' in chosenInstance.value
    ? chosenInstance.value.agents.map(agent => agent.id).join(', ') : ''
}
watch(milestone, () => {
  method.value = methods[milestone.value][0]![0]; model.value = ''
  horizon.value = Math.min(horizon.value, maximumHorizon.value)
  clearResult()
})
watch(instanceId, setInstanceDefaults)
watch(speed, () => { if (playing.value) { pause(); play() } })
watch(() => props.active, active => { if (!active) pause() })
watch(scenarioIndex, () => seek(0))
async function loadCatalog() {
  try {
    catalog.value = await request<ExperimentCatalog>('v2/catalog')
    if (disposed) return
    scenarioSet.value = catalog.value.scenario_sets[0]?.id ?? ''
    checkpoint.value = catalog.value.checkpoints[0]?.id ?? ''
    trainingInstances.value = catalog.value.training_sets.map(entry => entry.id)
    validationInstances.value = catalog.value.validation_sets.map(entry => entry.id)
    await refreshCompatibility()
  } catch (caught) { if (!disposed) error.value = failure(caught) }
}
async function refreshCompatibility(preserveResult = false) {
  if (!catalog.value) return
  const revision = ++catalogRevision
  if (!preserveResult) clearResult()
  checkingCompatibility.value = true; compatibilityError.value = ''; compatibleCatalog.value = null
  const parameters: ScientificData = { milestone: milestone.value, method: method.value }
  if (props.userInstance) parameters.instance = props.userInstance
  if (milestone.value === 'm7') {
    if (method.value !== 'robust') parameters.solver_budget = solverBudget.value
    if (method.value === 'robust') parameters.scenario_set = scenarioSet.value
  }
  if (milestone.value === 'm10') {
    if (method.value === 'q') parameters.checkpoint = checkpoint.value
    if (training.value) Object.assign(parameters, { training_instances: trainingInstances.value, validation_instances: validationInstances.value })
  }
  try {
    const value = await request<ExperimentCatalog>('v2/catalog', parameters)
    if (disposed || revision !== catalogRevision) return
    compatibleCatalog.value = value; chooseInstance()
  } catch (caught) { if (!disposed && revision === catalogRevision) compatibilityError.value = failure(caught) }
  finally { if (!disposed && revision === catalogRevision) checkingCompatibility.value = false }
}
watch([milestone, method, () => method.value === 'robust' ? scenarioSet.value : '',
  () => method.value === 'q' ? checkpoint.value : '',
  () => milestone.value === 'm7' && method.value !== 'robust' ? solverBudget.value : 0,
  () => training.value ? trainingInstances.value : null, () => training.value ? validationInstances.value : null], () => {
  void refreshCompatibility()
})
watch(() => props.userInstance, () => { void refreshCompatibility(instanceId.value !== 'user:current') })
async function checkOllama() {
  checkingOllama.value = true; ollamaError.value = ''
  try { const value = await request<OllamaStatus>('v2/ollama'); if (!disposed) ollama.value = value }
  catch (caught) { if (!disposed) ollamaError.value = failure(caught) }
  finally { if (!disposed) checkingOllama.value = false }
}
function body(compare: boolean): ScientificData {
  const value: ScientificData = { milestone: milestone.value, method: method.value, instance: chosenInstance.value, horizon: horizon.value }
  if (!training.value) value.compare = compare
  if (milestone.value === 'm7') {
    if (method.value !== 'robust') value.solver_budget = solverBudget.value
    if (method.value === 'rolling') Object.assign(value, { window: windowSize.value, frequency: frequency.value })
    if (method.value === 'robust') Object.assign(value, { scenario_set: scenarioSet.value, max_policies: maxPolicies.value })
  } else if (milestone.value === 'm8') Object.assign(value, { rounds: rounds.value, probability: probability.value, seed: seed.value, message_budget: messageBudget.value, solver_budget: solverBudget.value })
  else if (milestone.value === 'm9') {
    Object.assign(value, { max_nodes: maxNodes.value, seconds: seconds.value })
    if (method.value === 'prioritized') value.priorities = priorities.value.split(',').map(id => id.trim()).filter(Boolean)
  } else {
    value.seed = seed.value
    if (method.value === 'q') value.checkpoint = checkpoint.value
    if (training.value) Object.assign(value, { episodes: episodes.value, checkpoint_every: checkpointEvery.value,
      alpha: alpha.value, training_instances: trainingInstances.value, validation_instances: validationInstances.value })
  }
  if (llm.value) Object.assign(value, { model: model.value || null, preload: preload.value, timeout: timeout.value,
    load_timeout: loadTimeout.value, call_budget: callBudget.value, scenario_budget: scenarioBudget.value, max_tokens: maxTokens.value })
  return value
}
function acceptJob(value: ExperimentJob) {
  job.value = value
  if (value.result?.checkpoint && catalog.value && !catalog.value.checkpoints.some(entry => entry.id === value.result!.checkpoint)) {
    catalog.value = { ...catalog.value, checkpoints: [...catalog.value.checkpoints,
      { id: value.result.checkpoint, label: 'Checkpoint entraîné dans cette session', source: 'session' }] }
    checkpoint.value = value.result.checkpoint
  }
  if (value.status === 'failed') error.value = `${value.error?.kind ?? 'Erreur'} : ${value.error?.message ?? value.message}`
}
async function poll(id: string) {
  try {
    const value = await request<ExperimentJob>(`v2/jobs/${encodeURIComponent(id)}`)
    if (disposed || job.value?.id !== id) return
    error.value = ''; acceptJob(value)
  } catch (caught) {
    if (disposed || job.value?.id !== id) return
    error.value = `Suivi temporairement indisponible : ${failure(caught)}. Nouvelle tentative automatique.`
  }
  if (!disposed && job.value?.id === id && (job.value.status === 'queued' || job.value.status === 'running')) {
    pollTimer = setTimeout(() => { void poll(id) }, 500)
  }
}
async function run(compare = false) {
  if (running.value || checkingCompatibility.value || compatibilityError.value || !chosenInstance.value || !form.value?.reportValidity()) return
  clearResult(); const revision = executionRevision; submitting.value = true
  try {
    const value = await request<ExperimentJob>('v2/jobs', body(compare))
    if (disposed || revision !== executionRevision) return
    acceptJob(value)
    if (value.status === 'queued' || value.status === 'running') pollTimer = setTimeout(() => { void poll(value.id) }, 500)
  } catch (caught) { if (!disposed && revision === executionRevision) error.value = failure(caught) }
  finally { if (revision === executionRevision) submitting.value = false }
}
onMounted(loadCatalog)
onUnmounted(() => { disposed = true; catalogRevision++; clearTimeout(pollTimer); pause() })
</script>

<template>
  <main class="experiments" data-testid="experiments">
    <section class="panel">
      <div class="panel-heading"><h2>Expériences scientifiques</h2><span class="hint">Algorithmes Python existants · Budgets bornés</span></div>
      <form ref="form" data-testid="experiment-form" @submit.prevent="run()" @input="parameterInput" @change="clearResult">
        <fieldset class="experiment-fields" :disabled="running">
          <div class="fields">
            <label>Problème<select v-model="milestone" data-testid="milestone"><option v-for="[id, label] in milestones" :key="id" :value="id">{{ label }}</option></select></label>
            <label>Méthode<select v-model="method" data-testid="method"><option v-for="[id, label] in methods[milestone]" :key="id" :value="id">{{ label }}</option></select></label>
            <label>Instance<select v-model="instanceId" data-testid="v2-instance" :disabled="checkingCompatibility" required><option v-if="!entries.length" value="">{{ checkingCompatibility ? 'Vérification…' : 'Aucune instance compatible' }}</option><option v-for="entry in entries" :key="entry.id" :value="entry.id">{{ entry.label }}</option></select></label>
            <label>Horizon (ticks)<input v-model.number="horizon" data-testid="v2-horizon" type="number" min="0" :max="maximumHorizon" required></label>
          </div>
          <div class="fields experiment-parameters">
            <label v-if="(milestone === 'm7' && method !== 'robust') || milestone === 'm8'">Budget solveur par phase<input v-model.number="solverBudget" data-testid="solver-budget" type="number" min="0.001" max="0.5" step="0.001" required></label>
            <template v-if="milestone === 'm7' && method === 'rolling'">
              <label>Fenêtre (ticks)<input v-model.number="windowSize" data-testid="window" type="number" min="1" max="60" required></label>
              <label>Fréquence de replanification<input v-model.number="frequency" data-testid="frequency" type="number" min="1" max="60" required></label>
            </template>
            <template v-if="milestone === 'm7' && method === 'robust'">
              <label>Ensemble de scénarios<select v-model="scenarioSet" data-testid="scenario-set" required><option v-for="entry in catalog?.scenario_sets" :key="entry.id" :value="entry.id">{{ entry.label }}</option></select></label>
              <label>Budget de politiques<input v-model.number="maxPolicies" data-testid="max-policies" type="number" min="1" max="500" required></label>
            </template>
            <template v-if="milestone === 'm8'">
              <label>Tours<input v-model.number="rounds" data-testid="rounds" type="number" min="0" max="100" required></label>
              <label v-if="!llm">Probabilité de mise à jour<input v-model.number="probability" data-testid="probability" type="number" min="0" max="1" step="0.05" required></label>
              <label>Budget de transmissions<input v-model.number="messageBudget" data-testid="message-budget" type="number" min="0" max="100000" required></label>
            </template>
            <template v-if="milestone === 'm9'">
              <label v-if="method === 'prioritized'">Priorités (IDs séparés par virgules)<input v-model="priorities" data-testid="priorities" required></label>
              <label>Budget de nœuds<input v-model.number="maxNodes" data-testid="max-nodes" type="number" min="1" max="100000" required></label>
              <label>Budget de recherche (s)<input v-model.number="seconds" data-testid="search-seconds" type="number" min="0.001" max="10" step="0.001" required></label>
            </template>
            <label v-if="milestone === 'm8' || milestone === 'm10'">Graine<input v-model.number="seed" data-testid="v2-seed" type="number" min="0" max="2147483647" required></label>
            <label v-if="milestone === 'm10' && method === 'q'">Checkpoint figé<select v-model="checkpoint" data-testid="checkpoint" required><option v-for="entry in catalog?.checkpoints" :key="entry.id" :value="entry.id">{{ entry.label }} · {{ entry.source }}</option></select></label>
            <template v-if="training">
              <label>Épisodes (maximum 30)<input v-model.number="episodes" data-testid="episodes" type="number" min="1" max="30" required></label>
              <label>Validation tous les N épisodes<input v-model.number="checkpointEvery" data-testid="checkpoint-every" type="number" min="1" max="30" required></label>
              <label>Taux d’apprentissage alpha<input v-model.number="alpha" data-testid="alpha" type="number" min="0.001" max="1" step="0.001" required></label>
            </template>
          </div>
          <div v-if="training" class="fields experiment-parameters">
            <label>Instances d’entraînement<select v-model="trainingInstances" data-testid="training-instances" multiple required><option v-for="entry in catalog?.training_sets" :key="entry.id" :value="entry.id">{{ entry.label }}</option></select></label>
            <label>Instances de validation<select v-model="validationInstances" data-testid="validation-instances" multiple required><option v-for="entry in catalog?.validation_sets" :key="entry.id" :value="entry.id">{{ entry.label }}</option></select></label>
          </div>
          <div v-if="llm" class="ollama-controls">
            <div class="fields">
              <label>Modèle Ollama<select v-model="model" data-testid="ollama-model"><option value="">Sélection benchmark : {{ benchmarkModel }}</option><option v-for="entry in ollama?.models" :key="entry.name" :value="entry.name">{{ entry.name }}</option></select></label>
              <label>Choix manuel (identifiant exact)<input v-model="model" data-testid="manual-model" placeholder="Vide : sélection benchmark"></label>
              <button type="button" data-testid="check-ollama" :disabled="checkingOllama" @click="checkOllama">{{ checkingOllama ? 'Vérification…' : 'Vérifier Ollama' }}</button>
            </div>
            <p v-if="ollama" class="hint" role="status">{{ ollama.available ? 'Serveur accessible' : 'Serveur inaccessible' }} · {{ ollama.models.length }} modèle(s) installé(s). {{ !model ? 'Digest de la sélection vérifié avant exécution.' : 'Choix manuel explicite, sans digest de la sélection.' }}</p>
            <details v-if="ollama?.errors.length"><summary>Diagnostics Ollama</summary><pre>{{ JSON.stringify(ollama.errors, null, 2) }}</pre></details>
            <p v-if="ollamaError" class="error" role="alert">{{ ollamaError }}</p>
            <p v-if="missingModel" class="error" role="alert">Modèle absent : {{ selectedModel }}. Choisir un modèle installé. Aucun téléchargement automatique.</p>
            <details><summary>Budgets et chargement Ollama</summary><div class="fields">
              <label class="checkbox-label"><input v-model="preload" data-testid="preload" type="checkbox">Précharger avant les décisions</label>
              <label>Chargement maximal (s)<input v-model.number="loadTimeout" data-testid="load-timeout" type="number" min="0.001" max="180" step="0.001" required></label>
              <label>Délai par appel (s)<input v-model.number="timeout" data-testid="ollama-timeout" type="number" min="0.001" max="30" step="0.001" required></label>
              <label>Nombre maximal d’appels<input v-model.number="callBudget" data-testid="call-budget" type="number" min="0" max="16" required></label>
              <label>Budget des décisions (s)<input v-model.number="scenarioBudget" data-testid="scenario-budget" type="number" min="0.001" max="180" step="0.001" required></label>
              <label>Tokens maximaux par appel<input v-model.number="maxTokens" data-testid="max-tokens" type="number" min="1" max="256" required></label>
            </div></details>
            <p class="hint">Chargement initial parfois long. Le suivi reste actif pendant le chargement. LLM choisit des actions locales ; aucun entraînement RL du modèle.</p>
          </div>
          <p v-if="milestone === 'm7'" class="hint">Nominal : oracle hors ligne. Horizon glissant : tâches révélées, sans calendrier futur ; horizon × budget solveur ≤ 12. Robuste : files fixes, garantie limitée aux scénarios déclarés.</p>
          <p v-if="milestone === 'm8'" class="hint">Allocation sur observation figée, confirmée avant application au moteur. Comparaison FIFO/CNP/CP-SAT au même tick.</p>
          <p v-if="milestone === 'm10'" class="hint">{{ training ? 'Entraînement sur le premier ensemble ; sélection du checkpoint sur validation uniquement ; évaluation figée sur l’instance choisie.' : 'Évaluation figée : aucune mise à jour des tables Q. Domaine borné à 4 robots, 8 tâches et une grille 8 × 8.' }}</p>
          <p v-if="checkingCompatibility" class="hint" role="status">Vérification des instances compatibles…</p>
          <p v-else-if="compatibilityError" class="error" role="alert">{{ compatibilityError }}</p>
          <p v-else-if="catalog && !entries.length" class="notice" data-testid="no-compatible-instance">Aucune instance compatible avec ce problème et cette méthode. Vérifier les scénarios ou le checkpoint.</p>
          <details v-if="compatibleCatalog?.incompatible_instances?.length"><summary>Instances incompatibles ({{ compatibleCatalog.incompatible_instances.length }})</summary><ul><li v-for="entry in compatibleCatalog.incompatible_instances" :key="entry.id">{{ entry.label }} : {{ entry.reason }}</li></ul></details>
          <div class="controls experiment-actions"><button type="submit" class="primary" data-testid="v2-run" :disabled="checkingCompatibility || !!compatibilityError || !chosenInstance">{{ training ? 'Entraîner puis évaluer' : 'Lancer l’expérience' }}</button><button v-if="!training" type="button" data-testid="v2-compare" :disabled="checkingCompatibility || !!compatibilityError || !chosenInstance" @click="run(true)">Comparer les méthodes</button></div>
        </fieldset>
      </form>
      <p v-if="!catalog && !error" class="hint" role="status">Chargement des instances…</p>
      <div v-if="running" class="status-line" role="status" aria-live="polite" data-testid="job-status"><strong>{{ statusLabels[job?.status ?? 'running'] }}</strong><span>{{ job?.message ?? 'Envoi de l’expérience…' }}</span><span v-if="llm">Le modèle peut être en cours de chargement.</span></div>
    </section>
    <p v-if="error" class="error" role="alert" data-testid="v2-error">{{ error }}</p>
    <template v-if="result">
      <section class="panel" data-testid="v2-result">
        <div class="panel-heading"><h2>{{ resultTitle }} <span class="badge">{{ statusLabels[result.execution_status] ?? result.execution_status }}</span></h2><button @click="download(`taskfleet-${result.milestone}-${result.method}.json`, result)">Exporter résultat JSON</button></div>
        <p v-if="result.execution_status !== 'valid'" class="notice" data-testid="partial-result">{{ statusLabels[result.execution_status] ?? result.execution_status }}. Consulter statut scientifique et limites ci-dessous.</p>
        <p v-if="result.search_budget_exhausted" class="notice">Budget de recherche épuisé pour au moins un plan. L’exécution peut être terminée ; aucune preuve complète d’optimalité.</p>
        <p class="hint">Mesures finales de l’expérience. Les états, opérations et décisions ci-dessous suivent le tick du rejeu.</p>
        <p v-if="scenarioRuns.length" class="hint">Métriques du scénario rejoué {{ scenarioIndex + 1 }}. Les pires valeurs de l’ensemble figurent ci-dessous.</p>
        <div class="metrics"><div v-for="key in primaryMetrics" :key="key"><span>{{ metricLabels[key] }}</span><strong>{{ formatted(metrics[key]) }}</strong></div></div>
        <label v-if="scenarioRuns.length">Scénario robuste rejoué<select v-model.number="scenarioIndex" data-testid="replay-scenario"><option v-for="(run, index) in scenarioRuns" :key="index" :value="index">Scénario {{ index + 1 }} · {{ display(run.execution_status) }}</option></select></label>
        <div v-if="result.problem || replayTrace" class="playback experiment-playback" data-testid="experiment-playback"><div class="controls"><button aria-label="Recommencer le rejeu" :disabled="!hasReplay" @click="seek(0)">↤</button><button aria-label="Tick précédent" :disabled="!hasReplay || tick === 0" @click="seek(tick - 1)">Précédent</button><button class="primary" data-testid="v2-play" :disabled="!hasReplay || maximum === 0 || (!playing && tick === maximum)" @click="play">{{ playing ? 'Pause' : 'Lecture' }}</button><button aria-label="Tick suivant" :disabled="!hasReplay || tick === maximum" @click="seek(tick + 1)">Suivant</button><button aria-label="Dernier tick" :disabled="!hasReplay" @click="seek(maximum)">↦</button><label>Vitesse du rejeu<select v-model.number="speed"><option v-for="value in [.5, 1, 2, 4]" :key="value" :value="value">{{ value }}×</option></select></label></div><label class="timeline">Tick {{ tick }} / {{ maximum }}<input type="range" :value="tick" min="0" :max="maximum" :disabled="!hasReplay" aria-label="Tick du rejeu" @input="seek(Number(($event.target as HTMLInputElement).value))"></label></div>
        <ScheduleDetails v-if="milestone === 'm7'" :result="result" :trace="replayTrace" :tick="tick" :scenario-index="scenarioIndex" @seek="seek" />
        <CoordinationDetails v-if="milestone === 'm8'" :coordination="coordination" :tasks="tasks" />
        <template v-if="milestone === 'm9'">
          <p data-testid="mapf-status">Plan vérifié : {{ formatted(data(plan.validation).valid) }} · Optimal dans ce modèle : {{ formatted(plan.optimal) }}</p>
          <p v-if="plan.priorities" class="hint">Priorités : {{ display(plan.priorities) }}</p>
          <details v-if="list(data(plan.validation).violations).length" open><summary>Conflits et violations détectés</summary><pre>{{ JSON.stringify(data(plan.validation).violations, null, 2) }}</pre></details>
          <details><summary>Statut de recherche et trajectoires par agent</summary><p>Code du solveur : {{ plan.status }} · {{ plan.reason }}</p><pre>{{ JSON.stringify(mapfPaths, null, 2) }}</pre></details>
        </template>
        <template v-if="milestone === 'm10'">
          <MarlTraining v-if="result.training" :training="result.training" :checkpoint="result.checkpoint" />
          <MarlDetails :episode="episode" :tick="tick" :trace="replayTrace" @seek="seek" />
          <p v-if="result.checkpoint && !result.training" class="hint">Checkpoint évalué : {{ result.checkpoint }}. Historique d’entraînement et de validation non fourni avec ce checkpoint.</p>
        </template>
        <div v-if="result.problem || replayTrace" data-testid="v2-replay">
        <MapfReplay v-if="result.problem" :problem="result.problem" :plan="plan" :tick="tick" />
        <details v-else-if="snapshot && replayTrace" class="spatial-replay" data-testid="spatial-replay"><summary>Vue spatiale et inspection · tick {{ tick }}</summary>
          <div class="filters">
            <label>Inspecter un robot<select :value="selectedRobot ?? ''" data-testid="inspect-robot" @change="select('robot', ($event.target as HTMLSelectElement).value || null)"><option value="">Aucun</option><option v-for="id in robots" :key="id">{{ id }}</option></select></label>
            <label>Inspecter une tâche<select :value="selectedTask ?? ''" data-testid="inspect-task" @change="select('task', ($event.target as HTMLSelectElement).value || null)"><option value="">Aucune</option><option v-for="id in tasks" :key="id">{{ id }}</option></select></label>
            <label v-if="Object.keys(snapshot.resources).length">Inspecter une station<select :value="selectedResource ?? ''" data-testid="inspect-resource" @change="select('resource', ($event.target as HTMLSelectElement).value || null)"><option value="">Aucune</option><option v-for="(_, id) in snapshot.resources" :key="id">{{ id }}</option></select></label>
          </div>
          <Warehouse :instance="replayTrace.instance" :snapshot="snapshot" :selected="selectedRobot" :selected-task="selectedTask" :selected-resource="selectedResource" :participants="participants" :animate="playing" :duration="500 / speed" @select="select('robot', $event)" @select-task="select('task', $event)" @select-resource="select('resource', $event)" />
          <dl v-if="inspectedRobot" data-testid="robot-inspection"><dt>Robot</dt><dd>{{ selectedRobot }}</dd><dt>Position</dt><dd>{{ inspectedRobot.position }}</dd><dt>État</dt><dd>{{ inspectedRobot.unavailable ? 'Suspendu' : robotLabels[inspectedRobot.state] ?? inspectedRobot.state }}</dd><dt>Tâche active</dt><dd>{{ display(inspectedRobot.task_id) }}</dd><dt>Ressource réservée</dt><dd>{{ display(inspectedRobot.resource_id) }}</dd><dt>Manutention restante</dt><dd>{{ inspectedRobot.handling_remaining }}</dd></dl>
          <dl v-if="inspectedTask" data-testid="task-inspection"><dt>Tâche</dt><dd>{{ selectedTask }}</dd><dt>État</dt><dd>{{ taskLabels[inspectedTask.state] }}</dd><dt>Robot affecté</dt><dd>{{ display(inspectedTask.robot_id) }}</dd><dt>Fin réalisée à ce tick</dt><dd>{{ display(inspectedTask.completed_tick) }}</dd><dt>En retard à ce tick</dt><dd>{{ formatted(inspectedTask.late) }}</dd></dl>
          <dl v-if="inspectedResource" data-testid="resource-inspection"><dt>Station</dt><dd>{{ selectedResource }}</dd><dt>Position</dt><dd>{{ inspectedResource.position }}</dd><dt>Occupation effective</dt><dd>{{ inspectedResource.holders.length }} / {{ inspectedResource.capacity }}</dd><dt>Robots détenteurs</dt><dd>{{ inspectedResource.holders.join(', ') || 'Aucun' }}</dd></dl>
        </details>
        <details v-if="replayTrace"><summary>Événements de simulation</summary><EventLog :events="events" :tick="tick" :selected="selectedRobot" :selected-task="selectedTask" :robots="robots" :tasks="tasks" /></details>
        </div>
        <details v-if="additionalMetrics.length" class="secondary-metrics"><summary>Mesures complémentaires et temps de calcul</summary><div class="metrics"><div v-for="key in additionalMetrics" :key="key"><span>{{ metricLabels[key] }}</span><strong>{{ formatted(metrics[key]) }}</strong></div></div></details>
      </section>
      <section v-if="result.comparison?.length" class="panel" data-testid="v2-comparison">
        <h2>Comparaison sur la même instance</h2>
        <template v-if="milestone !== 'm9'">
          <p class="hint">{{ milestone === 'm10' ? 'Évaluations figées sur l’instance choisie, distinctes de l’entraînement et de la validation. Seules les politiques réellement évaluées figurent ici. Aucune généralisation apprise démontrée pour Q-learning.' : milestone === 'm8' ? 'Coûts séparés par cardinalité ; comparer les coûts uniquement à cardinalité égale. Tours et messages absents restent non disponibles.' : 'Mesures finales des exécutions comparées.' }}</p>
          <ComparisonCharts :rows="comparisonChartRows" :metrics="comparisonChartMetrics" :cost-by-cardinality="milestone === 'm8'" />
        </template>
        <details :open="milestone === 'm9'"><summary>Valeurs exactes et statuts des méthodes</summary><div class="table-scroll"><table><thead><tr><th>Méthode</th><th>Résultat</th><th v-for="key in comparisonKeys" :key="key">{{ metricLabels[key] }}</th></tr></thead><tbody><tr v-for="(row, index) in comparisonRows" :key="index"><th>{{ methodLabel(row.method) }}<span v-if="row.search_status" class="badge">{{ row.search_status }}</span></th><td>{{ statusLabels[row.execution_status] ?? row.execution_status }}</td><td v-for="key in comparisonKeys" :key="key">{{ formatted(row.metrics[key]) }}</td></tr></tbody></table></div></details>
        <p class="hint">{{ milestone === 'm7' ? method === 'robust' ? 'Files fixes robustes et nominales évaluées dans les mêmes calendriers déclarés.' : 'Nominal : oracle hors ligne connaissant le futur. Horizon glissant : tâches révélées et disponibilité courante uniquement.' : milestone === 'm8' ? 'Qualité de l’allocation sur observation figée, avant continuation de la simulation.' : 'Même instance, horizon et budgets déclarés. Les temps réels peuvent varier.' }} Aucun classement général.</p>
      </section>
      <OllamaDetails :result="result" :selected-model="selectedModel" />
      <section class="panel"><details><summary>Résultat scientifique complet</summary><pre>{{ JSON.stringify(result, null, 2) }}</pre></details></section>
    </template>
  </main>
</template>
