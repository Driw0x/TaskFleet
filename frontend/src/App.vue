<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, shallowRef, watch } from 'vue'
import { download, request } from './api'
import Warehouse from './components/Warehouse.vue'
import InstanceEditor from './components/InstanceEditor.vue'
import EventLog from './components/EventLog.vue'
import { robotLabels, taskLabels } from './types'
import type { Comparison, Instance, Preview, Result, Strategy, TraceEvent } from './types'

const examples = ref<string[]>([])
const example = ref('complex_comparison.json')
const instance = shallowRef<Instance | null>(null)
const preview = shallowRef<Preview | null>(null)
const result = shallowRef<Result | null>(null)
const comparison = shallowRef<Comparison | null>(null)
const strategy = ref<Strategy>('fifo')
const budget = ref(1)
const valid = ref(false)
const validationError = ref('')
const error = ref('')
const notice = ref('')
const busy = ref('')
const jsonDraft = ref('')
const tick = ref(0)
const selected = ref<string | null>(null)
const selectedTask = ref<string | null>(null)
const selectedResource = ref<string | null>(null)
const playing = ref(false)
const speed = ref(1)
const tab = ref('events')
let revision = 0
let operation = 0
let validationTimer: ReturnType<typeof setTimeout> | undefined
let playbackTimer: ReturnType<typeof setInterval> | undefined
let disposed = false

const duration = computed(() => 500 / speed.value)
const maximum = computed(() => result.value?.trace.current.tick ?? 0)
const snapshot = computed(() => result.value
  ? tick.value === maximum.value ? result.value.trace.current : result.value.trace.snapshots[tick.value]!
  : preview.value?.snapshot)
const displayedInstance = computed(() => result.value?.trace.instance ?? preview.value?.instance)
const events = computed(() => result.value?.trace.events ?? [])
const messages = computed(() => events.value.filter(event => event.kind === 'message'))
const byTick = computed(() => {
  const index = new Map<number, TraceEvent[]>()
  for (const event of events.value) {
    if (!index.has(event.tick)) index.set(event.tick, [])
    index.get(event.tick)!.push(event)
  }
  return index
})
const currentEvents = computed(() => byTick.value.get(tick.value) ?? [])
const participants = computed(() => new Set(currentEvents.value.filter(e => e.kind === 'message').flatMap(e => [e.sender!, e.recipient!]).filter(id => id && id !== 'all')))
const robot = computed(() => selected.value ? snapshot.value?.robots[selected.value] : null)
const currentTask = computed(() => displayedInstance.value?.tasks.find(task => task.id === (selectedTask.value ?? robot.value?.task_id)))
const taskState = computed(() => currentTask.value ? snapshot.value?.tasks[currentTask.value.id] : null)
const resource = computed(() => selectedResource.value ? snapshot.value?.resources[selectedResource.value] : null)
const waitingResource = computed(() => robot.value?.state.startsWith('waiting_')
  ? Object.keys(snapshot.value?.resources ?? {}).find(id => String(snapshot.value!.resources[id]!.position) === String(robot.value!.position)) : null)
const waitingRobots = computed(() => Object.entries(snapshot.value?.robots ?? {}).filter(([, state]) =>
  state.state.startsWith('waiting_') && String(state.position) === String(resource.value?.position)).map(([id]) => id))
const colocatedTasks = computed(() => currentTask.value ? displayedInstance.value?.tasks.filter(task => task.id !== currentTask.value!.id
  && [task.pickup, task.destination].some(position => [currentTask.value!.pickup, currentTask.value!.destination].some(other => String(position) === String(other)))) ?? [] : [])
const agentHistory = computed(() => events.value.filter(e => selected.value
  ? e.robot_id === selected.value || e.sender === selected.value || e.recipient === selected.value || e.recipient === 'all'
    || (Array.isArray(e.assignments) && e.assignments.some(pair => pair[0] === selected.value))
  : selectedTask.value ? e.task_id === selectedTask.value || (Array.isArray(e.assignments) && e.assignments.some(pair => pair[1] === selectedTask.value))
  : selectedResource.value ? e.resource_id === selectedResource.value : false))
const recentAgentEvents = computed(() => agentHistory.value.filter(e => e.tick <= tick.value).slice(-12).reverse())
const robotIds = computed(() => Object.keys(snapshot.value?.robots ?? {}))
const taskIds = computed(() => displayedInstance.value?.tasks.map(task => task.id) ?? [])
const selectionTitle = computed(() => selected.value ? `Agent ${selected.value}` : selectedTask.value ? `Tâche ${selectedTask.value}` : selectedResource.value ? `Station ${selectedResource.value}` : 'Vue globale')
function select(kind: 'robot' | 'task' | 'resource', id: string | null) {
  selected.value = kind === 'robot' ? id : null
  selectedTask.value = kind === 'task' ? id : null
  selectedResource.value = kind === 'resource' ? id : null
}
watch(snapshot, current => {
  if (selected.value && !current?.robots[selected.value]) selected.value = null
  if (selectedTask.value && !current?.tasks[selectedTask.value]) selectedTask.value = null
  if (selectedResource.value && !current?.resources[selectedResource.value]) selectedResource.value = null
})
const metricFields = [
  ['tasks_completed', 'Tâches terminées'], ['completion_rate', 'Taux de complétion'],
  ['makespan', 'Makespan (ticks)'], ['total_tardiness', 'Retard total (ticks)'],
  ['distance', 'Distance (cases)'], ['mean_robot_utilization', 'Utilisation productive'],
  ['strategy_seconds', 'Temps de stratégie (s)'], ['messages', 'Messages'], ['invalid_decisions', 'Décisions invalides'],
]
function formatted(key: string, value: unknown) {
  if (value === null || value === undefined) return key === 'makespan' ? 'Incomplet' : '—'
  if (typeof value !== 'number') return String(value)
  if (key === 'completion_rate' || key === 'mean_robot_utilization') return `${(value * 100).toFixed(2)} %`
  return key === 'strategy_seconds' ? value.toFixed(6) : String(value)
}
function pause() {
  playing.value = false
  clearInterval(playbackTimer)
  playbackTimer = undefined
}
function seek(value: number) {
  pause()
  tick.value = Math.max(0, Math.min(maximum.value, value))
}
function play() {
  if (playing.value) return pause()
  if (!result.value || tick.value >= maximum.value) return
  playing.value = true
  playbackTimer = setInterval(() => {
    tick.value++
    if (tick.value >= maximum.value) pause()
  }, duration.value)
}
watch(speed, () => { if (playing.value) { pause(); play() } })
function invalidate() {
  revision++
  operation++
  clearTimeout(validationTimer)
  pause()
  if (comparison.value) notice.value = 'Instance ou budget modifié : comparaison invalidée.'
  comparison.value = null
  result.value = null
  tick.value = 0
  busy.value = ''
  error.value = ''
  valid.value = false
}
watch([instance, budget], () => {
  invalidate()
  if (!instance.value) return
  jsonDraft.value = JSON.stringify(instance.value, null, 2)
  validationError.value = 'Validation en cours…'
  const version = revision
  validationTimer = setTimeout(async () => {
    try {
      const data = await request<Preview>('instances/validate', { instance: instance.value })
      if (disposed || version !== revision) return
      preview.value = data
      valid.value = Number.isFinite(budget.value) && budget.value > 0
      validationError.value = valid.value ? '' : 'Budget CP-SAT : nombre positif requis.'
    } catch (caught) {
      if (!disposed && version === revision) validationError.value = message(caught)
    }
  }, 200)
}, { flush: 'sync' })
watch(strategy, () => {
  pause()
  result.value = null
  tick.value = 0
  if (busy.value === 'simulation') { operation++; busy.value = '' }
})
function message(caught: unknown) {
  return caught instanceof Error ? caught.message : 'Erreur inconnue.'
}
function install(data: Preview) {
  instance.value = data.instance
  clearTimeout(validationTimer)
  preview.value = data
  select('robot', null)
  valid.value = Number.isFinite(budget.value) && budget.value > 0
  validationError.value = valid.value ? '' : 'Budget CP-SAT : nombre positif requis.'
  jsonDraft.value = JSON.stringify(data.instance, null, 2)
}
async function load() {
  invalidate()
  const id = ++operation
  busy.value = 'exemple'
  try {
    const data = await request<Preview>(`examples/${encodeURIComponent(example.value)}`)
    if (!disposed && id === operation) install(data)
  } catch (caught) { if (!disposed && id === operation) error.value = message(caught) }
  finally { if (id === operation) busy.value = '' }
}
async function importJson(content: string) {
  invalidate()
  jsonDraft.value = content
  const id = ++operation
  busy.value = 'import'
  try {
    const data = await request<Preview>('instances/validate', { instance: content })
    if (!disposed && id === operation) install(data)
  } catch (caught) { if (!disposed && id === operation) validationError.value = message(caught) }
  finally { if (id === operation) busy.value = '' }
}
async function generate(parameters: Record<string, unknown>) {
  const id = ++operation
  busy.value = 'génération'
  error.value = ''
  try {
    const data = await request<Preview>('instances/generate', parameters)
    if (!disposed && id === operation) install(data)
  } catch (caught) { if (!disposed && id === operation) error.value = message(caught) }
  finally { if (id === operation) busy.value = '' }
}
async function calculate(compare: boolean) {
  if (!valid.value || !instance.value) return
  const id = ++operation
  busy.value = compare ? 'comparaison' : 'simulation'
  error.value = ''
  if (compare) { comparison.value = null; notice.value = '' }
  else { pause(); result.value = null; tick.value = 0 }
  const body = { instance: instance.value, solver_budget: budget.value }
  try {
    if (compare) {
      const data = await request<Comparison>('comparisons', body)
      if (!disposed && id === operation) comparison.value = data
    } else {
      const data = await request<Result>('simulations', { ...body, strategy: strategy.value })
      if (!disposed && id === operation) result.value = data
    }
  } catch (caught) { if (!disposed && id === operation) error.value = message(caught) }
  finally { if (id === operation) busy.value = '' }
}
function editDraft(content: string) {
  invalidate()
  jsonDraft.value = content
  validationError.value = 'JSON modifié : appliquer pour valider.'
}
onMounted(async () => {
  try {
    examples.value = await request<string[]>('examples')
    if (!disposed) await load()
  } catch (caught) { if (!disposed) error.value = message(caught) }
})
onUnmounted(() => { disposed = true; revision++; operation++; pause(); clearTimeout(validationTimer) })
</script>

<template>
  <header class="topbar">
    <div class="brand"><span class="brand-icon">TF</span><div><h1>TaskFleet</h1><p>Entrepôt multi-agent</p></div></div>
    <label>Instance<select v-model="example" data-testid="example"><option v-for="name in examples" :key="name">{{ name }}</option></select></label>
    <button data-testid="load-example" @click="load">Charger</button>
    <label>Stratégie<select v-model="strategy" data-testid="strategy"><option value="fifo">FIFO</option><option value="cpsat">CP-SAT</option><option value="cnp">CNP</option></select></label>
    <label>Budget CP-SAT<input v-model.number="budget" type="number" min="0.001" step="0.1" data-testid="budget"></label>
    <button class="primary" data-testid="run" :disabled="!valid || !!busy" @click="calculate(false)">{{ busy === 'simulation' ? 'Calcul…' : 'Lancer' }}</button>
    <button data-testid="compare" :disabled="!valid || !!busy" @click="calculate(true)">{{ busy === 'comparaison' ? 'Comparaison…' : 'Comparer' }}</button>
  </header>
  <main>
    <div class="status-line" aria-live="polite"><span :class="valid ? 'green' : 'orange'">{{ valid ? 'Instance valide' : validationError || 'Charger une instance' }}</span><span v-if="busy" role="status">{{ busy }} en cours…</span><span class="hint">Budget en unités déterministes par phase</span></div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <p v-if="!valid && preview" class="hint">La grille conserve la dernière instance valide. Corriger les données dans l’éditeur.</p>
    <section v-if="snapshot && displayedInstance" class="workspace">
      <div class="panel simulation-panel">
        <div class="panel-heading"><h2>Entrepôt <span class="badge">{{ displayedInstance.width }} × {{ displayedInstance.height }}</span></h2><span class="hint">{{ displayedInstance.robots.length }} robots · {{ displayedInstance.tasks.length }} tâches</span></div>
        <Warehouse :instance="displayedInstance" :snapshot="snapshot" :selected="selected" :selected-task="selectedTask" :selected-resource="selectedResource" :participants="participants" :animate="playing" :duration="duration" @select="select('robot', $event)" @select-task="select('task', $event)" @select-resource="select('resource', $event)" />
        <div class="playback">
          <div class="controls"><button aria-label="Recommencer" :disabled="!result" @click="seek(0)">↤</button><button aria-label="Tick précédent" :disabled="!result || tick === 0" @click="seek(tick - 1)">Précédent</button><button class="primary" data-testid="play" :disabled="!result || maximum === 0 || (!playing && tick === maximum)" @click="play">{{ playing ? 'Pause' : 'Lecture' }}</button><button aria-label="Tick suivant" :disabled="!result || tick === maximum" @click="seek(tick + 1)">Suivant</button><button aria-label="Dernier tick" :disabled="!result" @click="seek(maximum)">↦</button><label>Vitesse<select v-model.number="speed" data-testid="speed"><option v-for="value in [0.5, 1, 2, 4]" :key="value" :value="value">{{ value }}×</option></select></label></div>
          <label class="timeline">Tick <strong data-testid="tick">{{ tick }}</strong> / {{ maximum }}<input :value="tick" type="range" min="0" :max="maximum" :disabled="!result || maximum === 0" aria-label="Tick" @input="seek(Number(($event.target as HTMLInputElement).value))"></label>
        </div>
      </div>
      <aside class="panel inspection">
        <div class="panel-heading"><h2>{{ selectionTitle }}</h2><button v-if="selected || selectedTask || selectedResource" aria-label="Désélectionner" @click="select('robot', null)">×</button></div>
        <label>Inspecter une tâche<select :value="selectedTask ?? ''" data-testid="inspect-task" @change="select('task', ($event.target as HTMLSelectElement).value || null)">
          <option value="">Choisir un ID</option><option v-for="id in taskIds" :key="id" :value="id">{{ id }} · {{ taskLabels[snapshot.tasks[id]!.state] }}</option>
        </select></label>
        <template v-if="robot">
          <dl data-testid="inspection"><dt>Position</dt><dd>[{{ robot.position.join(',') }}]</dd><dt>Capacité</dt><dd>{{ robot.capacity }}</dd>
            <dt>État</dt><dd>{{ robot.unavailable ? 'Suspendu — ' : '' }}{{ robotLabels[robot.state] ?? robot.state }} ({{ robot.state }})</dd>
            <dt>Disponible</dt><dd>{{ robot.available ? 'Oui' : 'Non' }}</dd><dt>Suspendu</dt><dd>{{ robot.unavailable ? 'Oui' : 'Non' }}</dd>
            <dt>Tâche actuelle</dt><dd><button v-if="robot.task_id" class="inline-button" @click="select('task', robot.task_id)">{{ robot.task_id }}</button><span v-else>Aucune</span></dd>
            <dt>Chemin restant</dt><dd>{{ robot.path.length ? `${robot.path.length} cases, vers [${robot.path.at(-1)}]` : 'Aucun chemin restant fourni' }}</dd>
            <dt>Manutention restante</dt><dd>{{ robot.handling_remaining }} ticks</dd>
            <dt>Ressource utilisée</dt><dd><button v-if="robot.resource_id" class="inline-button" @click="select('resource', robot.resource_id)">{{ robot.resource_id }}</button><span v-else>Aucune</span></dd>
            <template v-if="waitingResource"><dt>Ressource attendue</dt><dd><button class="inline-button" @click="select('resource', waitingResource)">{{ waitingResource }}</button></dd></template>
          </dl>
          <p v-if="waitingResource" class="hint">Attente observée pendant ce tick. Une place libérée en fin de tick est réutilisable au tick suivant.</p>
          <details v-if="robot.path.length"><summary>Cases du chemin Python</summary><pre>{{ robot.path }}</pre></details>
        </template>
        <div v-if="currentTask && taskState" data-testid="task-inspection">
          <h3>Tâche {{ currentTask.id }}</h3>
          <dl><dt>État</dt><dd :class="taskState.state">{{ taskLabels[taskState.state] }} ({{ taskState.state }})</dd>
            <dt>Collecte</dt><dd>[{{ currentTask.pickup.join(',') }}]</dd><dt>Destination</dt><dd>[{{ currentTask.destination.join(',') }}]</dd>
            <dt>Robot affecté</dt><dd><button v-if="taskState.robot_id" class="inline-button" @click="select('robot', taskState.robot_id)">{{ taskState.robot_id }}</button><span v-else>Aucun</span></dd>
            <dt>Charge</dt><dd>{{ currentTask.load }}</dd><dt>Arrivée</dt><dd>Tick {{ taskState.arrival_tick }}</dd>
            <dt>Échéance</dt><dd>{{ currentTask.deadline === null ? 'Aucune' : `Tick ${currentTask.deadline}` }}</dd>
            <dt>Livraison</dt><dd>{{ taskState.completed_tick === null ? 'Non livrée' : `Tick ${taskState.completed_tick}` }}</dd>
            <dt>Manutention</dt><dd>{{ currentTask.handling_ticks }} ticks par extrémité</dd><dt>Livrée en retard</dt><dd>{{ taskState.late ? 'Oui' : 'Non' }}</dd>
          </dl>
          <p class="hint">Liaison violette : relation logique collecte / livraison. Elle peut traverser les obstacles ; aucun trajet futur n’est calculé.</p>
          <details v-if="colocatedTasks.length"><summary>Autres tâches sur ces cases ({{ colocatedTasks.length }})</summary>
            <button v-for="task in colocatedTasks" :key="task.id" class="agent-row" :data-inspect-task="task.id" @click="select('task', task.id)">{{ task.id }} · {{ taskLabels[snapshot.tasks[task.id]!.state] }}</button>
          </details>
        </div>
        <template v-if="resource">
          <dl data-testid="resource-inspection"><dt>Identifiant</dt><dd>{{ selectedResource }}</dd><dt>Position</dt><dd>[{{ resource.position.join(',') }}]</dd>
            <dt>Capacité maximale</dt><dd>{{ resource.capacity }}</dd><dt>Occupation actuelle</dt><dd>{{ resource.holders.length }} place(s) occupée(s) sur {{ resource.capacity }}</dd>
            <dt>État</dt><dd>{{ resource.holders.length >= resource.capacity ? 'Saturée' : 'Disponible' }}</dd>
            <dt>Robots titulaires</dt><dd><span v-if="!resource.holders.length">Aucun</span><button v-for="id in resource.holders" :key="id" class="inline-button" @click="select('robot', id)">{{ id }}</button></dd>
            <dt>Robots en attente</dt><dd>{{ waitingRobots.join(', ') || 'Aucun' }}</dd>
          </dl>
          <p class="hint">Ressource utilisée uniquement pour manutention sur cette case. Capacité en robots, indépendante de la charge. Le passage ne réserve aucune place.</p>
        </template>
        <template v-if="selected || selectedTask || selectedResource">
          <button v-if="result?.trace.strategy === 'cnp' && !selectedResource" class="agent-row" data-testid="inspect-messages" @click="tab = 'messages'">Messages CNP associés</button>
          <h3>Derniers événements · tick ≤ {{ tick }}</h3><ul class="agent-events"><li v-for="(event, i) in recentAgentEvents" :key="i"><strong>{{ event.tick }}</strong> {{ event.type ?? event.kind }} · {{ event.task_id ?? '' }}<span v-if="event.reason"> · {{ event.reason }}</span></li></ul>
        </template>
        <template v-else>
          <p class="hint">Cliquer sur un robot, une collecte, une destination ou une station. Le sélecteur permet aussi d’inspecter les tâches masquées.</p>
          <button v-for="(state, id) in snapshot.robots" :key="id" class="agent-row" @click="select('robot', String(id))"><strong>{{ id }}</strong><span>[{{ state.position.join(',') }}]</span><span :class="state.unavailable ? 'red' : 'teal'">{{ state.unavailable ? 'Suspendu' : robotLabels[state.state] ?? state.state }}</span></button>
          <h3>Tâches au tick {{ tick }}</h3><div class="task-list"><button v-for="(task, id) in snapshot.tasks" :key="id" :data-inspect-task="id" @click="select('task', String(id))"><strong>{{ id }}</strong><span :class="task.state">{{ taskLabels[task.state] }}</span><span>{{ task.robot_id ?? '—' }}</span></button></div>
        </template>
      </aside>
    </section>
    <section v-if="comparison" class="panel" data-testid="comparison"><div class="panel-heading"><h2>Comparaison des stratégies</h2><button @click="download('taskfleet-comparison.json', comparison)">Exporter comparaison</button></div><p v-for="run in comparison.runs.filter(run => run.status === 'failed')" :key="run.strategy" class="error">{{ run.strategy === 'cpsat' ? 'CP-SAT' : run.strategy.toUpperCase() }} : {{ run.error }}</p><div class="table-scroll"><table><thead><tr><th>Métrique</th><th v-for="run in comparison.runs" :key="run.strategy">{{ run.strategy === 'cpsat' ? 'CP-SAT' : run.strategy.toUpperCase() }}</th></tr></thead><tbody><tr><th>État</th><td v-for="run in comparison.runs" :key="run.strategy">{{ { complete: 'Terminé', incomplete: 'Incomplet', failed: 'Échec' }[run.status] }}</td></tr><tr v-for="[key, label] in metricFields" :key="key"><th>{{ label }}</th><td v-for="run in comparison.runs" :key="run.strategy">{{ run.status === 'failed' ? '—' : formatted(key!, run[key!]) }}</td></tr></tbody></table></div><p class="hint">Même instance et horizon. Retard des seules tâches terminées ; utilisation hors attente/suspension. Temps réel variable. Aucun classement général.</p></section>
    <section v-if="result" class="panel secondary">
      <div class="tabs" role="tablist" aria-label="Traces et résultats"><button v-for="[key, label] in [['events', 'Événements'], ['messages', 'Messages CNP'], ['metrics', 'Métriques']]" :key="key" role="tab" :aria-selected="tab === key" :class="{ active: tab === key }" @click="tab = key!">{{ label }}<span v-if="key === 'messages'" class="badge">{{ result.metrics.messages }}</span></button></div>
      <div v-show="tab === 'events'"><EventLog :events="events" :tick="tick" :selected="selected" :selected-task="selectedTask" :robots="robotIds" :tasks="taskIds" /></div>
      <div v-show="tab === 'messages'"><EventLog :events="messages" :tick="tick" :selected="selected" :selected-task="selectedTask" :robots="robotIds" :tasks="taskIds" messages /></div>
      <div v-show="tab === 'metrics'"><div class="metrics"><div v-for="[key, label] in metricFields" :key="key"><span>{{ label }}</span><strong>{{ formatted(key!, result.metrics[key!]) }}</strong></div></div><details><summary>Métriques complètes M5</summary><pre>{{ JSON.stringify(result.metrics, null, 2) }}</pre></details><button @click="download('taskfleet-trace.json', result.trace)">Exporter trace JSON</button></div>
    </section>
    <InstanceEditor v-if="instance" :instance="instance" :busy="!!busy" :valid="valid" :json-draft="jsonDraft" @change="instance = $event" @generate="generate" @import="importJson" @draft="editDraft" @export="valid && preview && download('taskfleet-instance.json', preview.instance)" />
    <footer>Simulation déterministe · Décisions au tick n, actions au tick n+1 · Moteur scientifique Python commun</footer>
  </main>
</template>
