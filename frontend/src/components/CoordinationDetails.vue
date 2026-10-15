<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { data, display, list } from '../experiments'
import type { ScientificData } from '../experiments'

const props = defineProps<{ coordination: ScientificData; tasks: string[] }>()
const robot = ref('')
const task = ref('')
const kind = ref('')
const round = ref('')
const page = ref(0)
const domains = computed(() => data(props.coordination.domains))
const messages = computed(() => list(props.coordination.messages).map(data))
const history = computed(() => list(props.coordination.history).map(data))
const steps = computed(() => history.value.filter(row => typeof row.round === 'number').slice()
  .sort((a, b) => Number(a.round) - Number(b.round)))
const step = ref(steps.value.length)
const showCompatibility = ref(false)
const selectedRobot = ref('')
const selectedTask = ref('')
const final = computed(() => step.value >= steps.value.length)
const currentRound = computed(() => final.value ? undefined : Number(steps.value[step.value]?.round))
const robots = computed(() => [...new Set([
  ...Object.keys(domains.value), ...Object.keys(data(steps.value.at(-1)?.values)),
  ...list(props.coordination.assignments).map(pair => String(list(pair)[0])),
  ...list(props.coordination.raw_assignments).map(pair => String(list(pair)[0])),
])].sort())
function pairs(value: unknown): Record<string, unknown> {
  return Object.fromEntries(list(value).map(pair => list(pair)).filter(pair => typeof pair[0] === 'string'))
}
const confirmed = computed(() => pairs(props.coordination.assignments))
const raw = computed(() => pairs(props.coordination.raw_assignments))
const choices = computed(() => final.value ? confirmed.value : data(steps.value[step.value]?.values))
const previous = computed(() => {
  if (final.value) return raw.value
  if (step.value > 0) return data(steps.value[step.value - 1]?.values)
  return Object.fromEntries(messages.value.filter(message => message.type === 'value' && message.round === currentRound.value)
    .map(message => [String(message.sender), message.action]))
})
const graphTasks = computed(() => [...new Set([
  ...props.tasks, ...Object.values(domains.value).flatMap(value => list(value)),
  ...Object.values(choices.value), ...Object.values(previous.value),
].filter((id): id is string => typeof id === 'string'))].sort())
const graphHeight = computed(() => Math.max(260, Math.max(robots.value.length, graphTasks.value.length) * 62 + 90))
function y(index: number, count: number) {
  return count < 2 ? graphHeight.value / 2 : 80 + index * (graphHeight.value - 130) / (count - 1)
}
function conflict(values: Record<string, unknown>, id: unknown) {
  return typeof id === 'string' && Object.values(values).filter(value => value === id).length > 1
}
const conflictPairs = computed(() => {
  const counts = new Map<string, number>()
  Object.values(choices.value).forEach(value => { if (typeof value === 'string') counts.set(value, (counts.get(value) ?? 0) + 1) })
  return [...counts.values()].reduce((total, count) => total + count * (count - 1) / 2, 0)
})
type Link = { robot: string; task: string; state: string; conflicted?: boolean }
const links = computed(() => {
  const result: Link[] = []
  if (showCompatibility.value) for (const id of robots.value) for (const task of list(domains.value[id])) {
    if (typeof task === 'string') result.push({ robot: id, task, state: 'compatible' })
  }
  for (const id of robots.value) {
    const old = previous.value[id]
    const current = choices.value[id]
    if (typeof old === 'string' && old !== current) result.push({ robot: id, task: old, state: 'abandoned', conflicted: conflict(previous.value, old) })
    if (typeof current === 'string') result.push({ robot: id, task: current,
      state: final.value ? 'confirmed' : conflict(choices.value, current) ? 'conflict' : 'proposed' })
  }
  return result
})
const stateLabels: Record<string, string> = {
  compatible: 'Compatible', abandoned: 'Proposition abandonnée', conflict: 'Proposition en conflit',
  proposed: 'Proposition courante', confirmed: 'Affectation confirmée',
}
function curve(link: Link) {
  const from = y(robots.value.indexOf(link.robot), robots.value.length)
  const to = y(graphTasks.value.indexOf(link.task), graphTasks.value.length)
  return `M 232 ${from} C 375 ${from}, 505 ${to}, 648 ${to}`
}
function inspect(id: string, target = '') { selectedRobot.value = id; selectedTask.value = target }
function choice(id: string) {
  const value = choices.value[id]
  if (typeof value === 'string') return value
  if (value === null || (final.value && Array.isArray(props.coordination.assignments))) return final.value ? 'Sans affectation' : 'Attente'
  return 'Choix non renseigné'
}
const stageMessages = computed(() => final.value
  ? messages.value.filter(message => message.type === 'claim' || message.type === 'confirmed')
  : messages.value.filter(message => Number(message.round) === currentRound.value && message.type === 'value'))
const exchanges = computed(() => {
  const grouped = new Map<string, { message: ScientificData; recipients: string[] }>()
  for (const message of stageMessages.value) {
    const key = JSON.stringify([message.round, message.type, message.sender, message.action])
    const group = grouped.get(key) ?? { message, recipients: [] }
    group.recipients.push(String(message.recipient))
    grouped.set(key, group)
  }
  return [...grouped.values()]
})
const cumulativeMessages = computed(() => final.value ? messages.value.length
  : messages.value.filter(message => Number(message.round) <= Number(currentRound.value)
    && message.type !== 'claim' && message.type !== 'confirmed').length)
const llmTrace = computed(() => list(props.coordination.llm_trace).map(data))
const llmSources = computed(() => {
  const result: Record<string, ScientificData> = {}
  for (const id of robots.value) {
    const rows = llmTrace.value.filter(row => data(data(row.observation).robot).id === id)
    // M8 records one call per robot per history row, in the same round order.
    const source = final.value ? rows.at(-1) : rows[step.value]
    if (source) result[id] = source
  }
  return result
})
function sourceLabel(id: string) {
  const source = llmSources.value[id]
  return !source ? '' : source.fallback ? `Secours : ${display(source.error)}`
    : source.structured_valid && source.action_legal ? 'Décision LLM validée' : 'Validation LLM non renseignée'
}
function rejectedModelAction(row: ScientificData): string {
  const content = row.raw_content ?? data(data(row.raw_response).message).content
  if (!row.fallback || typeof content !== 'string') return ''
  try {
    const action = data(JSON.parse(content)).action
    return action === undefined ? '' : `Proposition brute rejetée : ${action === null ? 'Attente' : display(action)}.`
  } catch { return '' }
}
function short(value: string, limit = 29) { return value.length > limit ? `${value.slice(0, limit - 1)}…` : value }
watch(() => props.coordination, () => {
  step.value = steps.value.length
  selectedRobot.value = ''; selectedTask.value = ''; showCompatibility.value = false
  robot.value = ''; task.value = ''; kind.value = ''; round.value = ''
})
const types = computed(() => [...new Set(messages.value.map(message => String(message.type)))])
const rounds = computed(() => [...new Set(messages.value.map(message => Number(message.round)))].sort((a, b) => a - b))
const filtered = computed(() => messages.value.filter(message =>
  (!robot.value || message.sender === robot.value || message.recipient === robot.value || message.recipient === 'all')
  && (!task.value || message.action === task.value || list(message.tasks).includes(task.value))
  && (!kind.value || message.type === kind.value)
  && (round.value === '' || Number(message.round) === Number(round.value))))
const visible = computed(() => filtered.value.slice(page.value * 100, (page.value + 1) * 100))
watch([robot, task, kind, round, () => props.coordination], () => { page.value = 0 })
const labels: Record<string, string> = {
  CONFIRMED: 'Affectations confirmées', LOCAL_OPTIMUM: 'Choix locaux stables',
  COMMUNICATION_BUDGET: 'Budget de transmissions épuisé', ROUND_BUDGET: 'Nombre maximal de tours atteint',
  UNCHANGED_LLM: 'Choix LLM inchangés', domain: 'Domaine', value: 'Choix local', claim: 'Revendication', confirmed: 'Confirmation',
}
function label(value: unknown) { return labels[String(value)] ?? display(value) }
function assignment(value: unknown, id: string): string {
  if (!Array.isArray(value)) return '—'
  const pair = value.find(pair => list(pair)[0] === id)
  return pair ? display(list(pair)[1]) : 'Attente'
}
</script>

<template>
  <div data-testid="coordination-details">
    <p data-testid="coordination-status">{{ label(coordination.status) }} · Arrêt : {{ label(coordination.stop_reason) }} · Conflits avant confirmation : {{ display(data(coordination.raw_quality).conflict_pairs) }} · Abandons : {{ coordination.confirmation_abandonments }}</p>
    <p class="hint">Coordination locale sur une observation figée. Les tours sont des échanges synchrones, indépendants des ticks du rejeu spatial.</p>
    <p v-if="Number(coordination.message_budget) < Number(coordination.minimum_confirmation_budget)" class="notice">Budget insuffisant pour découvrir les domaines et confirmer : aucune affectation transmise.</p>
    <h3>Graphe des propositions et affectations</h3>
    <div class="controls graph-controls">
      <template v-if="steps.length">
        <button aria-label="Tour précédent" :disabled="step === 0" @click="step--">Précédent</button>
        <label>Étape de coordination<select v-model.number="step" data-testid="coordination-step">
          <option v-for="(row, index) in steps" :key="Number(row.round)" :value="index">Tour {{ row.round }} · choix locaux</option>
          <option :value="steps.length">Confirmation finale</option>
        </select></label>
        <button aria-label="Tour suivant" :disabled="final" @click="step++">Suivant</button>
      </template>
      <label class="checkbox-label"><input v-model="showCompatibility" type="checkbox" data-testid="coordination-compatibility">Afficher les compatibilités détaillées</label>
    </div>
    <p class="hint" data-testid="coordination-stage">{{ final ? 'Confirmation finale' : `Tour ${currentRound} · choix après mise à jour locale` }} · {{ Object.values(choices).filter(value => typeof value === 'string').length }} {{ final ? 'affectations' : 'propositions' }} · {{ conflictPairs }} paires en conflit · Messages logiques : {{ cumulativeMessages }} / {{ messages.length }}<template v-if="coordination.transmissions !== undefined"> · Transmissions totales : {{ coordination.transmissions }}</template></p>
    <p v-if="!steps.length" class="hint">Aucun tour de décision exécuté n’est renseigné. Seuls les choix finaux enregistrés sont affichés.</p>
    <p v-if="!Array.isArray(coordination.assignments) && final" class="notice">Affectations finales non renseignées.</p>
    <div v-if="robots.length" class="coordination-scroll">
      <svg class="coordination-graph" :viewBox="`0 0 880 ${graphHeight}`" role="img" aria-label="Graphe de coordination robots et tâches" data-testid="coordination-graph">
        <title>{{ final ? 'Affectations confirmées' : `Propositions au tour ${currentRound}` }}. Robots à gauche, tâches à droite.</title>
        <text x="24" y="26" class="column-label">ROBOTS · {{ robots.length }}</text><text x="648" y="26" class="column-label">TÂCHES · {{ graphTasks.length }}</text>
        <g data-layer="coordination-links">
          <path v-for="link in links" :key="`${link.state}-${link.robot}-${link.task}`" :d="curve(link)" :class="['coordination-link', link.state, { dimmed: (selectedRobot && selectedRobot !== link.robot) || (selectedTask && selectedTask !== link.task) }]"
            :data-robot="link.robot" :data-task="link.task" :data-state="link.state" :data-conflict="link.state === 'conflict' || !!link.conflicted"
            role="button" tabindex="0" :aria-label="`${link.robot}, ${link.task} : ${stateLabels[link.state]}`"
            @click="inspect(link.robot, link.task)" @keydown.enter.prevent="inspect(link.robot, link.task)" @keydown.space.prevent="inspect(link.robot, link.task)">
            <title>{{ link.robot }} · {{ link.task }} : {{ stateLabels[link.state] }}{{ link.conflicted ? ' après conflit' : '' }}{{ sourceLabel(link.robot) ? ` · ${sourceLabel(link.robot)}` : '' }}</title>
          </path>
        </g>
        <g v-for="(id, index) in robots" :key="id" :transform="`translate(24 ${y(index, robots.length) - 24})`" class="coordination-node" role="button" tabindex="0" :aria-label="`Inspecter robot ${id} : ${choice(id)}`" :aria-pressed="selectedRobot === id"
          :data-coordination-robot="id" :data-unassigned="final && !confirmed[id]" @click="inspect(id)" @keydown.enter.prevent="inspect(id)" @keydown.space.prevent="inspect(id)">
          <rect width="208" height="48" rx="8" :class="{ selected: selectedRobot === id, idle: !choices[id], fallback: llmSources[id]?.fallback }" />
          <text x="12" y="18" class="node-id">{{ short(id) }}</text>
          <text x="12" y="35" class="node-status">{{ short(choice(id), 25) }}{{ llmSources[id] ? llmSources[id]?.fallback ? ' · secours' : ' · LLM' : '' }}</text>
          <title>{{ id }} · {{ choice(id) }}{{ sourceLabel(id) ? ` · ${sourceLabel(id)}` : '' }}</title>
        </g>
        <g v-for="(id, index) in graphTasks" :key="id" :transform="`translate(648 ${y(index, graphTasks.length) - 24})`" class="coordination-node task-node" role="button" tabindex="0" :aria-label="`Inspecter tâche ${id}`" :aria-pressed="selectedTask === id"
          :data-coordination-task="id" @click="inspect('', id)" @keydown.enter.prevent="inspect('', id)" @keydown.space.prevent="inspect('', id)">
          <rect width="208" height="48" rx="8" :class="{ selected: selectedTask === id, conflicted: conflict(choices, id) }" />
          <text x="12" y="18" class="node-id">{{ short(id) }}</text>
          <text x="12" y="35" class="node-status">{{ Object.values(choices).filter(value => value === id).length }} {{ final ? 'affectation(s)' : 'proposition(s)' }}</text>
          <title>{{ id }}{{ conflict(choices, id) ? ' · Propositions en conflit' : '' }}</title>
        </g>
      </svg>
    </div>
    <p v-else class="empty">Aucun robot renseigné dans le résultat de coordination.</p>
    <div class="legend graph-legend"><span v-if="showCompatibility"><i class="compatible" />Compatible</span><span><i class="proposed" />Proposition</span><span><i class="conflict" />Conflit</span><span><i class="confirmed" />Confirmée</span><span><i class="abandoned" />Abandonnée</span></div>
    <p class="hint">Les liens abandonnés montrent les choix remplacés depuis l’étape précédente, ou perdus à la confirmation. Une confirmation ne décrit pas encore une livraison.</p>
    <div v-if="selectedRobot || selectedTask" class="graph-inspection" data-testid="coordination-inspection" aria-live="polite">
      <p v-if="selectedRobot"><strong>Robot {{ selectedRobot }}</strong> · {{ final ? 'Affectation confirmée' : 'Choix local' }} : {{ choice(selectedRobot) }}. Domaine : {{ domains[selectedRobot] === undefined ? 'non renseigné' : display(domains[selectedRobot]) }}. {{ sourceLabel(selectedRobot) }}</p>
      <p v-if="selectedTask"><strong>Tâche {{ selectedTask }}</strong> · Robots {{ final ? 'affectés' : 'candidats' }} : {{ Object.entries(choices).filter(([, value]) => value === selectedTask).map(([id]) => id).join(', ') || 'Aucun' }}.</p>
      <button class="inline-button" @click="inspect('')">Effacer la sélection</button>
    </div>
    <ul v-if="Object.keys(llmSources).length" class="source-list" data-testid="coordination-llm-sources">
      <li v-for="(source, id) in llmSources" :key="id"><strong>{{ id }}</strong> · {{ sourceLabel(String(id)) }} · Choix retenu : {{ source.action === null ? 'Attente' : display(source.action) }}. {{ rejectedModelAction(source) }}<template v-if="final && typeof source.action === 'string'"> {{ confirmed[id] === source.action ? 'Proposition confirmée.' : 'Proposition non confirmée, aucune affectation exécutée issue de ce choix.' }}</template></li>
    </ul>
    <p v-if="llmTrace.length" class="hint">Sources par ordre réel des appels de chaque robot dans les tours enregistrés. Les propositions brutes rejetées ne deviennent pas des affectations.</p>
    <p v-else-if="coordination.method === 'llm' || coordination.llm_metrics" class="hint">Sources des décisions LLM non renseignées.</p>
    <h3>Échanges significatifs {{ final ? 'de confirmation' : `du tour ${currentRound}` }}</h3>
    <ul v-if="exchanges.length" class="exchange-list" data-testid="coordination-exchanges"><li v-for="({ message, recipients }, index) in exchanges.slice(0, 8)" :key="index">
      <strong>{{ message.sender }}</strong> · {{ label(message.type) }} : {{ message.action === null ? 'Attente' : display(message.action) }} · {{ recipients.includes('all') ? 'Tous les autres agents' : recipients.join(', ') }}<template v-if="message.type === 'value'"> (avant mise à jour locale)</template>
    </li></ul>
    <p v-else class="hint">Aucun message enregistré pour cette étape.</p>
    <p v-if="exchanges.length > 8" class="hint">{{ exchanges.length }} échanges regroupés ; liste complète dans les détails.</p>
    <details class="coordination-technical" data-testid="coordination-technical"><summary>Détails de la coordination</summary>
    <div class="table-scroll"><table data-testid="domains"><thead><tr><th>Agent</th><th>Domaine (null = attente)</th><th>Voisins</th><th>Proposition avant confirmation</th><th>Affectation confirmée</th></tr></thead><tbody>
      <tr v-for="(domain, id) in domains" :key="id"><th>{{ id }}</th><td>{{ display(domain) }}</td><td>{{ display(data(coordination.neighbors)[id]) }}</td><td>{{ assignment(coordination.raw_assignments, String(id)) }}</td><td>{{ assignment(coordination.assignments, String(id)) }}</td></tr>
    </tbody></table></div>
    <p>Coût estimé avant / après confirmation : {{ display(data(coordination.raw_quality).estimated_cost) }} / {{ display(data(coordination.final_quality).estimated_cost) }}. Faisabilité finale : {{ data(coordination.final_quality).feasible === undefined ? '—' : data(coordination.final_quality).feasible ? 'Oui' : 'Non' }}.</p>
    <p class="hint">Cardinalité prioritaire, puis coût à cardinalité égale. Les coûts privés individuels DSA ne sont pas exportés. Les messages échangent domaines et choix, sans coûts privés.</p>
    <details v-if="coordination.penalties"><summary>Pénalités de décision</summary><pre>{{ JSON.stringify(coordination.penalties, null, 2) }}</pre></details>
    <h3>Historique des décisions locales</h3>
    <div v-if="history.length" class="table-scroll"><table data-testid="coordination-rounds"><thead><tr><th>Tour</th><th v-for="(_, id) in domains" :key="id">{{ id }}</th><th>Changements</th><th>Améliorations strictes DSA</th></tr></thead><tbody>
      <tr v-for="row in history" :key="String(row.round)"><td>{{ row.round }}</td><td v-for="(_, id) in domains" :key="id">{{ data(row.values)[id] === null ? 'Attente' : display(data(row.values)[id]) }}</td><td>{{ display(row.changed) }}</td><td>{{ display(row.strict_improvements) }}</td></tr>
    </tbody></table></div>
    <p v-else class="empty">Historique des tours non renseigné.</p>
    <p class="hint">Valeurs après mise à jour locale de chaque tour. Les messages « value » du même tour contiennent les valeurs avant mise à jour ; « claim » revendique, « confirmed » confirme après départage par ID.</p>
    <h3>Messages et confirmations finales</h3>
    <div class="filters">
      <label>Robot<select v-model="robot" data-testid="log-robot"><option value="">Tous</option><option v-for="(_, id) in domains" :key="id">{{ id }}</option></select></label>
      <label>Tâche<select v-model="task" data-testid="log-task"><option value="">Toutes</option><option v-for="id in tasks" :key="id">{{ id }}</option></select></label>
      <label>Type<select v-model="kind" data-testid="log-type"><option value="">Tous</option><option v-for="type in types" :key="type" :value="type">{{ label(type) }}</option></select></label>
      <label>Tour<select v-model="round" data-testid="coordination-round"><option value="">Tous</option><option v-for="number in rounds" :key="number">{{ number }}</option></select></label>
    </div>
    <p class="hint" data-testid="log-count">{{ filtered.length }} / {{ messages.length }} messages logiques</p>
    <div class="table-scroll log-table"><table data-testid="coordination-messages"><thead><tr><th>Tour</th><th>Type</th><th>Émetteur</th><th>Destinataire</th><th>Choix / domaine</th><th>Détails</th></tr></thead><tbody>
      <tr v-for="(message, index) in visible" :key="index"><td>{{ message.round }}</td><td>{{ label(message.type) }}</td><td>{{ message.sender }}</td><td>{{ message.recipient === 'all' ? 'Tous les autres agents' : message.recipient }}</td><td>{{ message.type === 'domain' ? display(message.tasks) : message.action === null ? 'Attente' : display(message.action) }}</td><td><details><summary>Message original</summary><pre>{{ JSON.stringify(message, null, 2) }}</pre></details></td></tr>
    </tbody></table><p v-if="!filtered.length" class="empty">Aucun message pour ces filtres.</p></div>
    <div v-if="filtered.length > 100" class="pagination"><button :disabled="page === 0" @click="page--">Page précédente</button><span>{{ page + 1 }} / {{ Math.ceil(filtered.length / 100) }}</span><button :disabled="(page + 1) * 100 >= filtered.length" @click="page++">Page suivante</button></div>
    </details>
  </div>
</template>

<style scoped>
.graph-controls { margin: 12px 0; }
.graph-controls label { margin-left: 0; }
.graph-controls select { max-width: 100%; }
.coordination-scroll { max-width: 100%; max-height: 560px; overflow: auto; margin-top: 14px; border: 1px solid #dbe4ec; border-radius: 9px; background: #fbfdff; }
.coordination-graph { display: block; width: 100%; min-width: 650px; }
.column-label { font-size: 12px; fill: #647b90; font-weight: 700; }
.coordination-link { fill: none; cursor: pointer; stroke-width: 3; }
.compatible { stroke: #bac9d7; stroke-width: 1; stroke-dasharray: 2 5; }
.proposed { stroke: #2162b8; stroke-dasharray: 8 4; }
.conflict { stroke: #c43b50; stroke-width: 4; stroke-dasharray: 6 3; }
.confirmed { stroke: #148263; stroke-width: 4; }
.abandoned { stroke: #ad611b; stroke-dasharray: 2 6; }
.dimmed { opacity: .2; }
.coordination-link:hover, .coordination-link:focus-visible { stroke-width: 6; opacity: 1; }
.coordination-node { cursor: pointer; }
.coordination-node rect { fill: #e9f3ff; stroke: #8fb2d5; }
.coordination-node .idle { fill: #f1f4f7; stroke: #bdcad5; }
.coordination-node .fallback { stroke: #ad611b; stroke-dasharray: 4 3; }
.task-node rect { fill: #edf7f2; stroke: #9ac9b6; }
.task-node .conflicted { fill: #fff0f1; stroke: #c43b50; }
.coordination-node .selected { stroke: #2162b8; stroke-width: 3; }
.node-id { font-weight: 700; font-size: 13px; fill: #1d3348; }
.node-status { font-size: 11px; fill: #576e85; }
.graph-legend i { display: inline-block; width: 27px; height: 0; border-top: 3px solid; }
.graph-legend .compatible { border-color: #bac9d7; border-top-style: dotted; }
.graph-legend .proposed { border-color: #2162b8; border-top-style: dashed; }
.graph-legend .conflict { border-color: #c43b50; border-top-style: dashed; }
.graph-legend .confirmed { border-color: #148263; }
.graph-legend .abandoned { border-color: #ad611b; border-top-style: dotted; }
.graph-inspection { display: flex; align-items: start; flex-wrap: wrap; gap: 10px; padding: 12px; margin-top: 12px; border-radius: 8px; background: #e9f3ff; font-size: 13px; overflow-wrap: anywhere; }
.exchange-list, .source-list { padding-left: 20px; font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }
.source-list { max-height: 220px; overflow: auto; }
.coordination-technical { margin-top: 18px; }
@media (max-width: 600px) { .graph-controls { align-items: stretch; } .graph-controls label { min-width: 0; } .graph-controls .checkbox-label { flex-basis: 100%; } }
</style>
