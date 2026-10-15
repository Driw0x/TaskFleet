<script setup lang="ts">
import { computed } from 'vue'
import { data, display, list } from '../experiments'
import type { ScientificData, ScientificResult } from '../experiments'

const props = defineProps<{ result: ScientificResult; selectedModel?: string }>()
const ollama = computed(() => data(props.result.ollama))
const coordination = computed(() => data(props.result.coordination))
const episode = computed(() => data(props.result.episode))
const metrics = computed(() => data(ollama.value.metrics ?? coordination.value.llm_metrics ?? episode.value.llm))
const trace = computed(() => list(ollama.value.trace ?? coordination.value.llm_trace ?? episode.value.llm_trace).map(data))
const preload = computed(() => data(ollama.value.preload))
const decisions = computed(() => list(episode.value.decisions).map(data))
const lastByRobot = computed(() => new Map(trace.value.map(row => [robot(row), row])))
const labels: Record<string, string> = {
  calls: 'Appels HTTP', decisions: 'Décisions demandées', successes: 'Générations valides',
  json_responses: 'Réponses JSON valides', structured_responses: 'Réponses structurées valides',
  legal_actions: 'Actions légales validées', rejections: 'Rejets', fallbacks: 'Actions de secours',
  latency_seconds: 'Latence cumulée (s)', mean_latency_seconds: 'Latence moyenne (s)',
  prompt_tokens: 'Tokens en entrée', generated_tokens: 'Tokens générés',
  generation_tokens_per_second: 'Tokens générés par seconde', scenario_elapsed_seconds: 'Durée des décisions (s)',
  scenario_budget_overrun_seconds: 'Dépassement du budget (s)',
}
const shownMetrics = computed(() => Object.keys(labels).filter(key => metrics.value[key] !== undefined && metrics.value[key] !== null
  && (!['prompt_tokens', 'generated_tokens'].includes(key) || tokenAvailable(key))))
const durations = computed(() => Object.fromEntries(Object.entries(data(metrics.value.timings_ns))
  .filter(([key]) => Number(data(metrics.value.duration_measurements)[key]) > 0)
  .map(([key, value]) => [key, Number(value) / 1_000_000_000])))
const appliedCount = computed(() => {
  if (episode.value.llm_actions_accepted !== undefined) return episode.value.llm_actions_accepted
  if (props.result.milestone !== 'm8' || !props.result.trace || !Array.isArray(coordination.value.assignments)
    || trace.value.some(row => !robot(row))) return undefined
  return list(coordination.value.assignments).filter(pair => {
    const [id, task] = list(pair)
    const source = lastByRobot.value.get(String(id))
    return source && !source.fallback && source.action === task
      && props.result.trace!.events.some(event => event.kind === 'assigned' && event.robot_id === id && event.task_id === task)
  }).length
})
function robot(row: ScientificData) { return String(data(data(row.observation).robot).id ?? '') }
function action(value: unknown) { return value === null ? 'Attente (null)' : display(value) }
function yesNo(value: unknown) { return typeof value === 'boolean' ? value ? 'Oui' : 'Non' : '—' }
function formatted(value: unknown) { return typeof value === 'number' && !Number.isInteger(value) ? value.toFixed(4) : display(value) }
function tokenAvailable(key: string) {
  return trace.value.length ? trace.value.some(row => typeof row[key] === 'number') : Number(metrics.value.token_measurements) > 0
}
function rawContent(row: ScientificData) { return row.raw_content ?? data(data(row.raw_response).message).content }
function proposed(row: ScientificData) {
  const content = rawContent(row)
  if (typeof content !== 'string') return undefined
  try { return data(JSON.parse(content)).action } catch { return undefined }
}
function execution(row: ScientificData) {
  const id = robot(row)
  if (!id) return 'Exécution non renseignée'
  if (props.result.milestone === 'm8') {
    if (lastByRobot.value.get(id) !== row) return 'Choix intermédiaire de coordination, sans exécution directe'
    if (!Array.isArray(coordination.value.assignments)) return 'Confirmation non renseignée'
    const confirmed = list(coordination.value.assignments).find(pair => list(pair)[0] === id)
    if (!confirmed) return row.action === null ? 'Attente retenue après coordination' : 'Proposition abandonnée à la confirmation'
    const task = list(confirmed)[1]
    const executed = props.result.trace?.events.some(event => event.kind === 'assigned' && event.robot_id === id && event.task_id === task)
    return executed ? `Affectation confirmée et exécutée : ${display(task)}` : `Affectation confirmée : ${display(task)} ; exécution non renseignée`
  }
  const decision = decisions.value.find(decision => decision.tick === data(row.observation).tick)
  if (!decision) return 'Transition non renseignée'
  if (list(decision.rejected).includes(id)) return 'Action rejetée par le masque'
  if (list(decision.conflicts).includes(id)) return 'Non appliquée : conflit d’affectation'
  const accepted = list(decision.accepted).find(pair => list(pair)[0] === id)
  if (accepted) return `Affectation appliquée : ${display(list(accepted)[1])}`
  return data(decision.proposals)[id] === null ? 'Attente appliquée, aucune nouvelle affectation' : 'Aucune affectation appliquée'
}
</script>

<template>
  <section v-if="Object.keys(metrics).length || trace.length" class="panel" data-testid="llm-result">
    <h2>Actions et validations Ollama</h2>
    <p class="hint">Modèle utilisé : {{ display(ollama.model ?? data(coordination.llm_config).model ?? selectedModel) }}</p>
    <div class="metrics"><div v-for="key in shownMetrics" :key="key"><span>{{ labels[key] }}</span><strong>{{ formatted(metrics[key]) }}</strong></div><div v-if="appliedCount !== undefined"><span>Affectations LLM appliquées</span><strong data-testid="llm-applied">{{ appliedCount }}</strong></div></div>
    <p class="hint">Une génération valide peut choisir l’attente ou perdre une revendication concurrente. Seules les affectations confirmées et exécutées comptent comme appliquées. Un secours ne compte jamais comme une génération réussie.</p>
    <p v-if="!tokenAvailable('prompt_tokens') && !tokenAvailable('generated_tokens')" class="hint">Comptage des tokens indisponible.</p>
    <p>Erreurs et timeouts : {{ display(metrics.errors) }}</p>
    <div v-if="Object.keys(preload).length" data-testid="ollama-preload"><h3>Préchargement du modèle</h3><p>Statut : {{ preload.status }} · Durée murale : {{ formatted(preload.latency_seconds) }} s · Budget : {{ display(preload.timeout_seconds) }} s.</p><p class="hint">Mesure distincte des appels de décision et de leur budget.</p><details><summary>Mesures du chargement</summary><pre>{{ JSON.stringify(preload, null, 2) }}</pre></details></div>
    <details v-if="Object.keys(durations).length"><summary>Durées serveur des générations (secondes)</summary><pre>{{ JSON.stringify(durations, null, 2) }}</pre></details>
    <p v-if="!trace.length" class="empty">Aucune décision LLM tracée.</p>
    <details v-for="(row, index) in trace" :key="index" data-testid="llm-decision">
      <summary>Décision {{ index + 1 }} · {{ robot(row) || 'Agent non renseigné' }} · Action {{ display(row.action) }} · {{ row.fallback ? 'Secours' : row.error ? display(row.error) : 'Validation' }}</summary>
      <dl><dt>Tick de l’observation</dt><dd>{{ display(data(row.observation).tick) }}</dd><dt>Actions autorisées</dt><dd>{{ display(row.allowed_actions) }}</dd><dt>Action proposée dans la réponse</dt><dd>{{ action(proposed(row)) }}</dd><dt>JSON / structure / action légale</dt><dd>{{ yesNo(row.json_valid) }} / {{ yesNo(row.structured_valid) }} / {{ yesNo(row.action_legal) }}</dd><dt>Action retenue par le client</dt><dd>{{ action(row.action) }} · {{ row.fallback ? 'Secours' : 'Réponse LLM' }}</dd><dt>Exécution réelle</dt><dd data-testid="llm-execution">{{ execution(row) }}</dd><dt>Erreur / timeout</dt><dd>{{ display(row.error) }} {{ display(row.error_detail) }}</dd><dt>Latence (s)</dt><dd>{{ formatted(row.latency_seconds) }}</dd></dl>
      <details><summary>Réponse brute du modèle</summary><pre>{{ display(rawContent(row)) }}</pre></details>
      <details><summary>Observation autorisée et trace complète</summary><pre>{{ JSON.stringify(row, null, 2) }}</pre></details>
    </details>
  </section>
</template>
