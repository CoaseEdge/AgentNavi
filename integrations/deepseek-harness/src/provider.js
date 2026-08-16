import z from '@deepseek-ai/schemastery'
import { AgentNaviService } from './service.js'
import {
  contextArgs,
  historyArgs,
  hookArgs,
  impactArgs,
  scanArgs,
} from './protocol.js'

export const name = 'agentnavi-local-provider'
export const inject = ['subprocess']

export const Config = z.object({
  command: z.string().default('agentnavi'),
  home: z.string(),
  timeoutMs: z.number().default(15_000),
  maxOutputBytes: z.number().default(1_048_576),
  maxErrorBytes: z.number().default(65_536),
  graceMs: z.number().default(3_000),
})

function positiveInteger(name, value) {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new TypeError(`agentnavi-local-provider: ${name} 必须是正整数`)
  }
  return value
}

function boundedOutput(handle, stream) {
  const reader = handle.collected[stream]
  if (reader === undefined) throw new Error(`AgentNavi 子进程缺少 ${stream} 收集器`)
  const output = reader.readFrom(0)
  if (output.lossy) {
    throw new Error(`AgentNavi ${stream} 超过插件输出预算，结果不完整`)
  }
  return output.text
}

function deadlineSignal(parent, timeoutMs) {
  const controller = new AbortController()
  const forward = () => controller.abort(parent?.reason)
  if (parent?.aborted) forward()
  else parent?.addEventListener('abort', forward, { once: true })
  const timer = setTimeout(
    () => controller.abort(new Error(`AgentNavi 命令超过 ${timeoutMs}ms`)),
    timeoutMs,
  )
  return {
    signal: controller.signal,
    dispose() {
      clearTimeout(timer)
      parent?.removeEventListener('abort', forward)
    },
  }
}

export default class LocalAgentNaviProvider extends AgentNaviService {
  static inject = inject
  static Config = Config

  constructor(ctx, config = {}) {
    super(ctx)
    this.config = {
      command: String(config.command ?? 'agentnavi'),
      home: config.home ? String(config.home) : undefined,
      timeoutMs: positiveInteger('timeoutMs', config.timeoutMs ?? 15_000),
      maxOutputBytes: positiveInteger('maxOutputBytes', config.maxOutputBytes ?? 1_048_576),
      maxErrorBytes: positiveInteger('maxErrorBytes', config.maxErrorBytes ?? 65_536),
      graceMs: positiveInteger('graceMs', config.graceMs ?? 3_000),
    }
    if (!this.config.command.trim()) throw new TypeError('agentnavi-local-provider: command 不能为空')
    this.sessionStarts = new Map()
    this.turnStarts = new Map()
    this.turnEnds = new Map()
    this.sessionEnds = new Map()
  }

  async run(cwd, args, { signal, stdin, json = true } = {}) {
    const workdir = String(cwd || process.cwd())
    const deadline = deadlineSignal(signal, this.config.timeoutMs)
    try {
      deadline.signal.throwIfAborted()
      const handle = this.ctx.subprocess.spawn({
        argv: [this.config.command, ...args],
        cwd: workdir,
        stdio: {
          stdin: stdin === undefined ? 'ignore' : { data: stdin },
          stdout: { maxBytes: this.config.maxOutputBytes },
          stderr: { maxBytes: this.config.maxErrorBytes },
        },
        graceMs: this.config.graceMs,
        signal: deadline.signal,
        env: {
          NO_COLOR: '1',
          TERM: 'dumb',
          PAGER: 'cat',
          GIT_PAGER: 'cat',
          ...(this.config.home ? { AGENTNAVI_HOME: this.config.home } : {}),
        },
      })
      const outcome = await handle.done
      const stdout = boundedOutput(handle, 'stdout')
      const stderr = boundedOutput(handle, 'stderr')
      if (outcome.exitCode !== 0) {
        const detail = stderr.trim() || stdout.trim() || `exit=${String(outcome.exitCode)}`
        throw new Error(`AgentNavi 命令失败：${detail}`)
      }
      if (!json) return stdout.trim()
      try {
        return JSON.parse(stdout)
      } catch (error) {
        throw new Error(`AgentNavi 返回了无效 JSON：${stdout.slice(0, 500)}`, { cause: error })
      }
    } finally {
      deadline.dispose()
    }
  }

  context({ cwd, query, signal }) {
    return this.run(cwd, contextArgs(query), { signal })
  }

  impact({ cwd, selector, signal }) {
    return this.run(cwd, impactArgs(selector), { signal })
  }

  history({ cwd, query = '', limit = 10, signal }) {
    return this.run(cwd, historyArgs(query, limit), { signal })
  }

  scan({ cwd, full = false, signal }) {
    return this.run(cwd, scanArgs(full), { signal })
  }

  ingestHook({ cwd, payload, signal }) {
    return this.run(cwd, hookArgs(), {
      signal,
      stdin: `${JSON.stringify(payload)}\n`,
      json: false,
    })
  }

  startSession({ sessionId, cwd, signal }) {
    const key = String(sessionId)
    const existing = this.sessionStarts.get(key)
    if (existing !== undefined) return existing
    const running = this.ingestHook({
      cwd,
      signal,
      payload: {
        hook_event_name: 'SessionStart',
        session_id: key,
        cwd,
        source: 'deepseek-harness',
      },
    }).catch(error => {
      this.sessionStarts.delete(key)
      throw error
    })
    this.sessionStarts.set(key, running)
    return running
  }

  async startTurn({ sessionId, turn, step = 1, cwd, prompt, signal }) {
    const sessionKey = String(sessionId)
    const key = `${sessionKey}:${String(turn)}`
    const existing = this.turnStarts.get(key)
    if (existing !== undefined) return existing
    const running = (async () => {
      await this.startSession({ sessionId: sessionKey, cwd, signal })
      return this.ingestHook({
        cwd,
        signal,
        payload: {
          hook_event_name: 'UserPromptSubmit',
          session_id: sessionKey,
          cwd,
          prompt,
          source: 'deepseek-harness',
          dsh_turn: turn,
          dsh_step: step,
        },
      })
    })().catch(error => {
      this.turnStarts.delete(key)
      throw error
    })
    this.turnStarts.set(key, running)
    return running
  }

  finishTurn({ sessionId, turn, cwd, status, summary, reason, signal }) {
    const sessionKey = String(sessionId)
    const key = `${sessionKey}:${String(turn)}`
    const existing = this.turnEnds.get(key)
    if (existing !== undefined) return existing
    const running = this.ingestHook({
      cwd,
      signal,
      payload: {
        hook_event_name: 'Stop',
        session_id: sessionKey,
        cwd,
        last_assistant_message: summary,
        task_status: status,
        turn_end_reason: reason,
        source: 'deepseek-harness',
        dsh_turn: turn,
      },
    }).catch(error => {
      this.turnEnds.delete(key)
      throw error
    })
    this.turnEnds.set(key, running)
    return running
  }

  endSession({ sessionId, cwd, signal }) {
    const key = String(sessionId)
    const existing = this.sessionEnds.get(key)
    if (existing !== undefined) return existing
    const running = this.ingestHook({
      cwd,
      signal,
      payload: {
        hook_event_name: 'SessionEnd',
        session_id: key,
        cwd,
        source: 'deepseek-harness',
      },
    }).catch(error => {
      this.sessionEnds.delete(key)
      throw error
    })
    this.sessionEnds.set(key, running)
    return running
  }

  releaseSession(sessionId) {
    const prefix = `${String(sessionId)}:`
    this.sessionStarts.delete(String(sessionId))
    this.sessionEnds.delete(String(sessionId))
    for (const key of this.turnStarts.keys()) {
      if (key.startsWith(prefix)) this.turnStarts.delete(key)
    }
    for (const key of this.turnEnds.keys()) {
      if (key.startsWith(prefix)) this.turnEnds.delete(key)
    }
  }
}
