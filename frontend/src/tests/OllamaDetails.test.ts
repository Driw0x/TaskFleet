import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import OllamaDetails from '../components/OllamaDetails.vue'
import type { ScientificResult } from '../experiments'
import type { Result } from '../types'

describe('Ollama decision provenance', () => {
  it('counts only generated assignments confirmed and executed, excluding intermediate choices and losing claims', () => {
    const result: ScientificResult = { milestone: 'm8', method: 'llm', execution_status: 'valid', metrics: {},
      coordination: { assignments: [['r1', 't1']], llm_metrics: { calls: 3, successes: 3, legal_actions: 3 }, llm_trace: [
        { observation: { robot: { id: 'r1' } }, action: 't2', fallback: false },
        { observation: { robot: { id: 'r1' } }, action: 't1', fallback: false },
        { observation: { robot: { id: 'r2' } }, action: 't1', fallback: false },
      ] }, trace: { events: [{ tick: 0, kind: 'assigned', robot_id: 'r1', task_id: 't1' }] } as Result['trace'] }
    const wrapper = mount(OllamaDetails, { props: { result } })
    expect(wrapper.get('[data-testid="llm-applied"]').text()).toBe('1')
    const decisions = wrapper.findAll('[data-testid="llm-execution"]')
    expect(decisions[0]!.text()).toContain('Choix intermédiaire')
    expect(decisions[1]!.text()).toContain('confirmée et exécutée : t1')
    expect(decisions[2]!.text()).toContain('abandonnée à la confirmation')
    wrapper.unmount()
  })

  it('shows invalid proposed action, actual fallback and zero applied LLM assignments separately', () => {
    const result: ScientificResult = { milestone: 'm10', method: 'llm', execution_status: 'ollama_error', metrics: {},
      episode: { llm_actions_accepted: 0, decisions: [{ tick: 0, proposals: { r1: null }, accepted: [], rejected: [], conflicts: [] }],
        llm: { calls: 1, successes: 0, fallbacks: 1, errors: { invalid_action: 1 } },
        llm_trace: [{ observation: { tick: 0, robot: { id: 'r1' } }, action: null, fallback: true, error: 'invalid_action',
          raw_content: '{"action":"forbidden"}', json_valid: true, structured_valid: true, action_legal: false }] } }
    const wrapper = mount(OllamaDetails, { props: { result } })
    expect(wrapper.get('[data-testid="llm-applied"]').text()).toBe('0')
    expect(wrapper.text()).toContain('forbidden')
    expect(wrapper.text()).toContain('Secours')
    expect(wrapper.get('[data-testid="llm-execution"]').text()).toContain('Attente appliquée')
    wrapper.unmount()
  })

  it('keeps unavailable token counts hidden and shows real preload measurements', () => {
    const result: ScientificResult = { milestone: 'm8', method: 'llm', execution_status: 'valid', metrics: {},
      ollama: { model: 'installed:model', metrics: { calls: 1, prompt_tokens: 8, generated_tokens: 0 },
        trace: [{ prompt_tokens: 8, generated_tokens: null }],
        preload: { status: 'ok', latency_seconds: 12.5, timeout_seconds: 120 } } }
    const wrapper = mount(OllamaDetails, { props: { result } })
    expect(wrapper.get('.metrics').text()).toContain('Tokens en entrée8')
    expect(wrapper.get('.metrics').text()).not.toContain('Tokens générés')
    expect(wrapper.get('[data-testid="ollama-preload"]').text()).toContain('12.5000 s')
    wrapper.unmount()
  })
})
