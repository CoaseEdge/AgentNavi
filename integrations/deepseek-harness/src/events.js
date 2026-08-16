import z from '@deepseek-ai/schemastery'
import {
  SerialWorkQueue,
  collectPathCandidates,
  cwdForSession,
  isAgentNaviTool,
  messageText,
  parseToolArguments,
  statusForTurnEnd,
  summaryForTurnEnd,
  toolCallIdFromResult,
  truncateText,
} from './protocol.js'

export const name = 'agentnavi-l3-event-bridge'
export const inject = ['agentNavi', 'sessions']

export const Config = z.object({
  enabled: z.boolean().default(true),
  recordAgentNaviTools: z.boolean().default(false),
  maxResultChars: z.number().default(4_000),
})

function currentTurn(session) {
  return session.events.findLast(event => event.type === 'turn/start')?.data.turn
}

function warn(error, key) {
  console.warn(`[${name}] 会话 ${key} 的 L3 写入失败，已按 fail-open 继续：${String(error)}`)
}

export function apply(ctx, config = {}) {
  if (config.enabled === false) return
  const recordAgentNaviTools = config.recordAgentNaviTools === true
  const maxResultChars = Number.isSafeInteger(config.maxResultChars) && config.maxResultChars > 0
    ? config.maxResultChars
    : 4_000
  const queue = new SerialWorkQueue(warn)
  const calls = new Map()
  const assistantSummaries = new Map()

  const callMap = (sessionId) => {
    let value = calls.get(sessionId)
    if (value === undefined) {
      value = new Map()
      calls.set(sessionId, value)
    }
    return value
  }

  ctx.on('session/created', (session) => {
    const sessionId = String(session.id)
    const cwd = cwdForSession(session)
    void queue.enqueue(sessionId, () => ctx.agentNavi.startSession({ sessionId, cwd }))
  })

  ctx.on('session/event', (session, event) => {
    const sessionId = String(session.id)
    const cwd = cwdForSession(session)

    switch (event.type) {
      case 'user/message': {
        if (event.data.source?.kind !== 'user') return
        const prompt = messageText(event.data.content)
        const turn = currentTurn(session)
        if (!prompt || turn === undefined) return
        void queue.enqueue(sessionId, () => ctx.agentNavi.startTurn({
          sessionId,
          turn,
          step: 1,
          cwd,
          prompt,
        }))
        return
      }

      case 'tool/call': {
        if (!recordAgentNaviTools && isAgentNaviTool(event.data.name)) return
        callMap(sessionId).set(String(event.data.callId), {
          name: event.data.name,
          arguments: parseToolArguments(event.data.arguments),
          turn: event.data.turn,
          step: event.data.step,
          seq: event.seq,
        })
        return
      }

      case 'tool/result': {
        const callId = toolCallIdFromResult(event.data.message)
        if (callId === undefined) return
        const pending = callMap(sessionId).get(callId)
        callMap(sessionId).delete(callId)
        if (pending === undefined) return
        const failed = event.data.error !== undefined
        const filePaths = collectPathCandidates({
          message: event.data.message,
          meta: event.data.meta,
        })
        const response = {
          success: !failed,
          status: failed ? 'error' : 'completed',
          text: truncateText(messageText(event.data.message), maxResultChars),
          file_paths: filePaths,
          ...(event.data.error === undefined ? {} : { error: event.data.error }),
        }
        void queue.enqueue(sessionId, () => ctx.agentNavi.ingestHook({
          cwd,
          payload: {
            hook_event_name: failed ? 'PostToolUseFailure' : 'PostToolUse',
            session_id: sessionId,
            cwd,
            tool_name: pending.name,
            tool_input: pending.arguments,
            tool_response: response,
            tool_use_id: callId,
            source: 'deepseek-harness',
            dsh_event_seq: event.seq,
            dsh_call_seq: pending.seq,
            dsh_turn: pending.turn,
            dsh_step: pending.step,
          },
        }))
        return
      }

      case 'assistant/message': {
        const text = truncateText(messageText(event.data.message), 8_000)
        if (text) assistantSummaries.set(sessionId, text)
        return
      }

      case 'turn/end': {
        const reason = event.data.reason
        const status = statusForTurnEnd(reason)
        const summary = assistantSummaries.get(sessionId) || summaryForTurnEnd(reason)
        assistantSummaries.delete(sessionId)
        void queue.enqueue(sessionId, () => ctx.agentNavi.finishTurn({
          sessionId,
          turn: event.data.turn,
          cwd,
          status,
          summary,
          reason,
        }))
        return
      }

      default:
        return
    }
  })

  ctx.on('session/disposed', (session) => {
    const sessionId = String(session.id)
    const cwd = cwdForSession(session)
    void queue.enqueue(sessionId, () => ctx.agentNavi.endSession({ sessionId, cwd }))
      .finally(() => {
        calls.delete(sessionId)
        assistantSummaries.delete(sessionId)
        ctx.agentNavi.releaseSession(sessionId)
      })
  })
}

export default { name, inject, Config, apply }
