<script setup lang="ts">
import { computed } from 'vue'
import { data, display, list } from '../experiments'
import type { ScientificData } from '../experiments'

const props = defineProps<{ training?: ScientificData | ScientificData[] | null; checkpoint?: string }>()
const colors = ['#2162b8', '#845fb7', '#148263', '#ad611b']
const reports = computed<(ScientificData & { color: string | undefined; label: string; curve: ScientificData[]; validation: ScientificData[] })[]>(() => (Array.isArray(props.training) ? props.training : props.training ? [props.training] : []).map((report, index) => ({
  ...report, color: colors[index % colors.length],
  label: report.training_seed === undefined ? 'Graine non renseignée' : `Graine ${report.training_seed}`,
  curve: list(report.curve).map(data), validation: list(report.validation).map(data),
})))
const validPoint = (row: ScientificData, field: string) => typeof row.episode === 'number' && Number.isFinite(row.episode) && typeof row[field] === 'number' && Number.isFinite(row[field])
const series = computed(() => reports.value.flatMap((report, index) => [
  { report, index, kind: 'training', label: 'Entraînement', field: 'return', rows: report.curve },
  { report, index, kind: 'validation', label: 'Validation (moyenne)', field: 'mean_return', rows: report.validation },
].map(row => ({ ...row, rows: row.rows.filter(point => validPoint(point, row.field)).sort((a, b) => Number(a.episode) - Number(b.episode)) }))))
const points = computed(() => series.value.flatMap(row => row.rows.map(point => ({ episode: Number(point.episode), value: Number(point[row.field]) }))))
const lastEpisode = computed(() => Math.max(1, ...points.value.map(point => point.episode)))
const min = computed(() => Math.min(0, ...points.value.map(point => point.value)))
const max = computed(() => Math.max(0, ...points.value.map(point => point.value)))
const width = computed(() => Math.max(660, 120 + lastEpisode.value * 10))
const x = (episode: number) => 78 + episode / lastEpisode.value * (width.value - 110)
const y = (value: number) => 245 - (value - min.value) / (max.value - min.value || 1) * 200
const marks = computed(() => {
  const step = Math.max(1, Math.ceil(lastEpisode.value / 10))
  return Array.from(new Set([...Array.from({ length: Math.floor(lastEpisode.value / step) + 1 }, (_, i) => i * step), lastEpisode.value]))
})
const selected = computed(() => reports.value.flatMap(report => {
  const row = report.validation.find(point => point.episode === report.selected_episode && validPoint(point, 'mean_return'))
  return row ? [{ report, episode: Number(row.episode), value: Number(row.mean_return) }] : []
}))
</script>

<template>
  <div data-testid="training-result">
    <h3>Entraînement et sélection sur validation</h3>
    <p class="hint">Retours d’entraînement par épisode et évaluations de validation enregistrés. Le checkpoint est choisi sur validation ; le test reste une évaluation figée distincte.</p>
    <div v-if="points.length" class="chart-scroll">
      <svg data-testid="marl-training-curves" :viewBox="`0 0 ${width} 300`" :style="{ width: `${width}px` }" role="img" aria-label="Retours d’entraînement et de validation par épisode">
        <title>Entraînement et validation, {{ reports.length }} graine(s) enregistrée(s). Aucune donnée de test dans ces courbes.</title>
        <text x="10" y="20">Retour par épisode</text>
        <g v-for="mark in marks" :key="mark"><line :x1="x(mark)" :x2="x(mark)" y1="38" y2="245" class="grid-line" /><text :x="x(mark)" y="267" text-anchor="middle">{{ mark }}</text></g>
        <line :x1="x(0)" :x2="x(lastEpisode)" y1="245" y2="245" class="axis-line" />
        <text :x="x(0) - 10" y="249" text-anchor="end">{{ min }}</text><text v-if="min !== max" :x="x(0) - 10" y="49" text-anchor="end">{{ max }}</text>
        <g v-for="row in series" :key="`${row.index}-${row.kind}`" :data-training-series="row.kind" :data-seed="row.report.training_seed">
          <polyline :points="row.rows.map(point => `${x(Number(point.episode))},${y(Number(point[row.field]))}`).join(' ')" fill="none" :stroke="row.report.color" :stroke-dasharray="row.kind === 'validation' ? '6 4' : undefined" stroke-width="2" />
          <circle v-for="point in row.rows" :key="Number(point.episode)" :cx="x(Number(point.episode))" :cy="y(Number(point[row.field]))" :r="row.kind === 'validation' ? 4 : 2.5" :fill="row.kind === 'validation' ? 'white' : row.report.color" :stroke="row.report.color" :data-episode="point.episode" :data-value="point[row.field]"><title>{{ row.report.label }} · {{ row.label }} · Épisode {{ point.episode }} : {{ point[row.field] }}</title></circle>
        </g>
        <g v-for="(point, index) in selected" :key="index" data-testid="marl-selected-checkpoint" :data-episode="point.episode" :data-seed="point.report.training_seed">
          <line :x1="x(point.episode)" :x2="x(point.episode)" y1="38" y2="245" :stroke="point.report.color" stroke-dasharray="2 4" />
          <path :d="`M ${x(point.episode)} ${y(point.value) - 8} l 8 8 l -8 8 l -8 -8 Z`" fill="#e8ac42" :stroke="point.report.color" stroke-width="2"><title>{{ point.report.label }} · Checkpoint retenu : épisode {{ point.episode }}, retour moyen de validation {{ point.value }}</title></path>
        </g>
        <text :x="x(lastEpisode)" y="290" text-anchor="end">Épisodes d’entraînement</text>
      </svg>
    </div>
    <div v-if="points.length" class="legend"><span>Trait plein : entraînement</span><span>Pointillé / cercle vide : validation</span><span>◆ Or : checkpoint retenu sur validation</span><span v-for="(report, index) in reports" :key="index" :style="{ color: report.color }">{{ report.label }}</span></div>
    <p v-if="!points.length" class="empty" data-testid="marl-no-training">Aucune courbe d’entraînement ou de validation disponible. Un checkpoint ou un épisode d’évaluation seul ne fournit pas un historique d’apprentissage.</p>
    <p v-if="reports.length === 1" class="hint" data-testid="marl-single-seed">Une seule graine disponible : comparaison entre graines indisponible.</p>
    <p v-if="reports.length && !series.some(row => row.kind === 'validation' && row.rows.length)" class="hint">Aucun résultat de validation enregistré.</p>
    <p v-for="(report, index) in reports" :key="index" class="hint">{{ report.label }} · Épisode retenu : {{ display(report.selected_episode) }} · Retour de validation : {{ display(report.validation_return) }}<template v-if="!selected.some(point => point.report === report)"> · Point de validation retenu indisponible sur la courbe.</template></p>
    <p v-if="checkpoint" class="hint">Checkpoint évalué : {{ checkpoint }}</p>
    <details v-if="reports.length"><summary>Détails de l’entraînement et de la validation</summary>
      <div v-for="(report, index) in reports" :key="index"><h3>{{ report.label }}</h3><dl><dt>Épisode retenu</dt><dd>{{ display(report.selected_episode) }}</dd><dt>Retour de validation</dt><dd>{{ display(report.validation_return) }}</dd><dt>Entraînement (s)</dt><dd>{{ display(report.training_seconds) }}</dd><dt>Instances d’entraînement</dt><dd>{{ display(report.training_instances) }}</dd><dt>Instances de validation</dt><dd>{{ display(report.validation_instances) }}</dd></dl><pre>{{ JSON.stringify(report, null, 2) }}</pre></div>
    </details>
  </div>
</template>

<style scoped>
.chart-scroll { max-width: 100%; overflow-x: auto; margin-top: 16px; }
svg { display: block; max-width: none; font-size: 11px; fill: #344c63; }
.grid-line { stroke: #e3eaf1; }
.axis-line { stroke: #a8bacb; }
</style>
