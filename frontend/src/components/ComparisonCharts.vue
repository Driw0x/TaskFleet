<script setup lang="ts">
import { computed } from 'vue'
import type { ScientificData } from '../experiments'

const props = defineProps<{
  rows: { label: string; metrics: ScientificData }[]
  metrics: { key: string; label: string }[]
  costByCardinality?: boolean
}>()
const colors = ['#126e7d', '#845fb7', '#ad611b', '#2162b8', '#a82a3f', '#148263']
function number(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value) }
function formatted(value: unknown) {
  return !number(value) ? 'Non disponible' : Number.isInteger(value) ? String(value) : String(Number(value.toPrecision(5)))
}
const charts = computed(() => props.metrics.flatMap(metric => {
  const groups = props.costByCardinality && metric.key === 'estimated_cost'
    ? [...new Set(props.rows.map(row => row.metrics.cardinality))] : [undefined]
  return groups.map(cardinality => {
    const rows = props.rows.map((row, index) => ({ ...row, color: colors[index % colors.length] }))
      .filter(row => !props.costByCardinality || metric.key !== 'estimated_cost' || row.metrics.cardinality === cardinality)
    const values = rows.map(row => row.metrics[metric.key]).filter(number)
    const min = Math.min(0, ...values)
    const max = Math.max(0, ...values)
    const x = (value: number) => 190 + (value - min) / (max - min || 1) * 310
    return { ...metric, label: metric.label + (props.costByCardinality && metric.key === 'estimated_cost'
      ? ` · cardinalité ${number(cardinality) ? cardinality : 'inconnue'}` : ''), rows, min, max, x }
  })
}))
</script>

<template>
  <div class="comparison-charts" data-testid="comparison-charts">
    <figure v-for="chart in charts" :key="chart.label" :data-metric="chart.key">
      <figcaption>{{ chart.label }}</figcaption>
      <div class="chart-scroll" tabindex="0" :aria-label="`Défilement : ${chart.label}`">
        <svg :viewBox="`0 0 600 ${chart.rows.length * 42 + 50}`" role="img" :aria-label="chart.label">
          <title>{{ chart.label }} · mesures indépendantes, valeurs manquantes explicites</title>
          <line :x1="chart.x(0)" :x2="chart.x(0)" y1="8" :y2="chart.rows.length * 42 + 4" stroke="#8193a5" />
          <g v-for="(row, index) in chart.rows" :key="index" :data-method="row.label" :data-value="number(row.metrics[chart.key]) ? row.metrics[chart.key] : undefined">
            <text x="2" :y="index * 42 + 27">{{ row.label }}</text>
            <template v-if="number(row.metrics[chart.key])">
              <rect :x="Math.min(chart.x(0), chart.x(row.metrics[chart.key] as number))" :y="index * 42 + 10"
                :width="Math.abs(chart.x(row.metrics[chart.key] as number) - chart.x(0))" height="24" rx="3" :fill="row.color">
                <title>{{ row.label }} · {{ chart.label }} : {{ row.metrics[chart.key] }}</title>
              </rect>
              <text x="510" :y="index * 42 + 27">{{ formatted(row.metrics[chart.key]) }}</text>
            </template>
            <text v-else x="365" :y="index * 42 + 27" class="missing">Non disponible</text>
          </g>
          <line x1="190" x2="500" :y1="chart.rows.length * 42 + 5" :y2="chart.rows.length * 42 + 5" stroke="#8193a5" />
          <text x="190" :y="chart.rows.length * 42 + 26">{{ formatted(chart.min) }}</text>
          <text x="500" :y="chart.rows.length * 42 + 26" text-anchor="end">{{ formatted(chart.max) }}</text>
        </svg>
      </div>
    </figure>
  </div>
</template>

<style scoped>
.comparison-charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(430px, 100%), 1fr)); gap: 16px; }
figure { margin: 0; min-width: 0; padding: 12px; background: #f5f8fb; border-radius: 8px; }
figcaption { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.chart-scroll { max-width: 100%; overflow-x: auto; }
svg { display: block; width: 100%; min-width: 560px; }
text { font-size: 12px; fill: #1d3348; }
.missing { fill: #6e8194; }
</style>
