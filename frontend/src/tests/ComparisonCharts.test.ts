import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ComparisonCharts from '../components/ComparisonCharts.vue'

describe('scientific comparisons', () => {
  it('compares recorded policies with signed returns and explicit missing measurements', () => {
    const wrapper = mount(ComparisonCharts, { props: {
      rows: [{ label: 'Aléatoire légale', metrics: { return: -10, makespan: null } },
        { label: 'Q-learning figée', metrics: { return: -5, makespan: 4 } }],
      metrics: [{ key: 'return', label: 'Retour' }, { key: 'makespan', label: 'Makespan (ticks)' }],
    } })
    const returns = wrapper.get('[data-metric="return"]')
    expect(returns.findAll('[data-method]')).toHaveLength(2)
    expect(returns.findAll('rect').map(rect => Number(rect.attributes('width')))).toEqual([310, 155])
    expect(returns.findAll('rect').map(rect => Number(rect.attributes('x')))).toEqual([190, 345])
    expect(wrapper.get('[data-metric="makespan"] [data-method="Aléatoire légale"]').text()).toContain('Non disponible')
    expect(wrapper.find('[data-method="LLM Ollama"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('separates allocation costs by cardinality without inventing missing rounds', () => {
    const wrapper = mount(ComparisonCharts, { props: {
      rows: [{ label: 'DSA', metrics: { cardinality: 2, estimated_cost: 10, rounds: 3 } },
        { label: 'CP-SAT', metrics: { cardinality: 2, estimated_cost: 8 } },
        { label: 'LLM Ollama', metrics: { cardinality: 1, estimated_cost: 2 } }],
      metrics: [{ key: 'estimated_cost', label: 'Coût estimé' }, { key: 'rounds', label: 'Tours' }], costByCardinality: true,
    } })
    const costs = wrapper.findAll('[data-metric="estimated_cost"]')
    expect(costs.map(chart => chart.get('figcaption').text())).toEqual(['Coût estimé · cardinalité 2', 'Coût estimé · cardinalité 1'])
    expect(costs[0]!.findAll('[data-method]').map(row => row.attributes('data-method'))).toEqual(['DSA', 'CP-SAT'])
    expect(costs[1]!.findAll('[data-method]')).toHaveLength(1)
    expect(wrapper.get('[data-metric="rounds"] [data-method="CP-SAT"]').find('rect').exists()).toBe(false)
    wrapper.unmount()
  })
})
