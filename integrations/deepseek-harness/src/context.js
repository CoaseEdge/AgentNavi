import z from '@deepseek-ai/schemastery'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import {
  cwdForAgent,
  extractHumanPrompt,
  truncateText,
} from './protocol.js'

export const name = 'agentnavi-context-router'
export const inject = ['agentNavi', 'agents']

export const Config = z.object({
  enabled: z.boolean().default(true),
  firstStepOnly: z.boolean().default(true),
  maxContextChars: z.number().default(12_000),
})

function warn(error) {
  console.warn(`[${name}] 自动上下文注入失败，已按 fail-open 继续：${String(error)}`)
}

export function apply(ctx, config = {}) {
  const enabled = config.enabled !== false
  const firstStepOnly = config.firstStepOnly !== false
  const maxContextChars = Number.isSafeInteger(config.maxContextChars) && config.maxContextChars > 0
    ? config.maxContextChars
    : 12_000
  const injected = new Set()

  ctx.on('agent/pre-step', async ({ agent, turn, step, signal }, next) => {
    const decision = await next()
    if (!enabled || decision.kind === 'reject' || signal.aborted) return decision
    if (firstStepOnly && step !== 1) return decision

    const prompt = extractHumanPrompt(decision.messages)
    if (!prompt) return decision
    const key = `${String(agent.session.id)}:${String(turn)}:${String(step)}`
    if (injected.has(key)) return decision
    injected.add(key)

    try {
      const raw = await ctx.agentNavi.startTurn({
        sessionId: String(agent.session.id),
        turn,
        step,
        cwd: cwdForAgent(agent),
        prompt,
        signal,
      })
      if (signal.aborted) return decision
      const context = truncateText(String(raw ?? '').trim(), maxContextChars)
      if (!context || context === '{}') return decision
      const text = `${context}\n\n说明：以上内容用于缩小候选范围，不是修改结论；请在执行前验证。`
      return {
        kind: 'enter',
        messages: [
          ...decision.messages,
          createUserMessage({
            content: [{ type: 'text', text }],
            source: {
              kind: 'plugin',
              plugin: name,
              form: 'snapshot',
              sections: [{ name: 'agentnavi-project-context', text }],
            },
          }),
        ],
      }
    } catch (error) {
      warn(error)
      return decision
    }
  }, { prepend: true })
}

export default { name, inject, Config, apply }
