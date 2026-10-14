<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { TraceEvent } from '../types'

const props = defineProps<{ events: TraceEvent[]; tick: number; selected: string | null; selectedTask?: string | null; robots: string[]; tasks: string[]; messages?: boolean }>()
const scope = ref('tick')
const robot = ref('')
const task = ref('')
const kind = ref('')
const page = ref(0)
const pageSize = 100
watch([() => props.selected, () => props.selectedTask], ([robotId, taskId]) => {
  robot.value = robotId ?? ''
  task.value = taskId ?? ''
}, { immediate: true })
const types = computed(() => [...new Set(props.events.map(e => props.messages ? e.type! : e.kind))].sort())
const byTick = computed(() => {
  const result = new Map<number, TraceEvent[]>()
  for (const event of props.events) {
    if (!result.has(event.tick)) result.set(event.tick, [])
    result.get(event.tick)!.push(event)
  }
  return result
})
// Full-history filters do not depend on the replay tick.
const filteredHistory = computed(() => props.events.filter(matches))
function matches(event: TraceEvent) {
  return (!robot.value || event.robot_id === robot.value || event.sender === robot.value || event.recipient === robot.value || event.recipient === 'all'
      || (Array.isArray(event.assignments) && event.assignments.some(pair => pair[0] === robot.value && (!task.value || pair[1] === task.value))))
    && (!task.value || event.task_id === task.value || (Array.isArray(event.assignments) && event.assignments.some(pair => pair[1] === task.value && (!robot.value || pair[0] === robot.value))))
    && (!kind.value || (props.messages ? event.type : event.kind) === kind.value)
}
const filtered = computed(() => scope.value === 'history' ? filteredHistory.value : scope.value === 'past'
  ? filteredHistory.value.filter(event => event.tick <= props.tick) : (byTick.value.get(props.tick) ?? []).filter(matches))
watch([scope, robot, task, kind, () => props.events, () => scope.value !== 'history' ? props.tick : null], () => { page.value = 0 })
const visible = computed(() => filtered.value.slice(page.value * pageSize, (page.value + 1) * pageSize))
</script>

<template>
  <div class="filters">
    <label>Portée<select v-model="scope" data-testid="log-scope"><option value="tick">Tick courant</option><option value="past">Jusqu’au tick courant</option><option value="history">Historique complet (tous les ticks)</option></select></label>
    <label>Robot<select v-model="robot" data-testid="log-robot"><option value="">Tous</option><option v-for="id in robots" :key="id">{{ id }}</option></select></label>
    <label>Tâche<select v-model="task" data-testid="log-task"><option value="">Toutes</option><option v-for="id in tasks" :key="id">{{ id }}</option></select></label>
    <label>Type<select v-model="kind" data-testid="log-type"><option value="">Tous</option><option v-for="type in types" :key="type">{{ type }}</option></select></label>
  </div>
  <p class="hint" data-testid="log-count">{{ filtered.length }} / {{ events.length }} {{ messages ? 'messages logiques' : 'événements' }}</p>
  <div class="table-scroll log-table">
    <table><thead><tr><th>Tick</th><th>Type</th><th v-if="messages">Émetteur</th><th v-if="messages">Destinataire</th><th v-else>Robot</th><th>Tâche</th><th v-if="messages">Coût</th><th>Détails / décision / motif</th></tr></thead>
      <tbody><tr v-for="(event, i) in visible" :key="`${page}-${i}`" :class="event.type">
        <td>{{ event.tick }}</td><td>{{ messages ? event.type : event.kind }}</td>
        <template v-if="messages"><td>{{ event.sender }}</td><td>{{ event.recipient }}</td></template><td v-else>{{ event.robot_id ?? '—' }}</td>
        <td>{{ event.task_id ?? '—' }}</td><td v-if="messages">{{ event.cost ?? '—' }}</td>
        <td><details><summary>{{ event.reason ?? event.state ?? (event.type || event.kind) }}</summary><pre>{{ JSON.stringify(event, null, 2) }}</pre></details></td>
      </tr></tbody>
    </table>
    <p v-if="!filtered.length" class="empty">Aucun élément pour ces filtres.</p>
  </div>
  <div v-if="filtered.length > pageSize" class="pagination"><button :disabled="page === 0" @click="page--">Page précédente</button><span>{{ page + 1 }} / {{ Math.ceil(filtered.length / pageSize) }}</span><button :disabled="(page + 1) * pageSize >= filtered.length" @click="page++">Page suivante</button></div>
</template>
