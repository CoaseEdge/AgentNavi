const PATH_KEYS = new Set([
  'path',
  'file_path',
  'filepath',
  'notebook_path',
  'target_path',
  'source_path',
  'destination_path',
])
const PATH_LIST_KEYS = new Set(['paths', 'files', 'file_paths'])

export function truncateText(value, maxChars) {
  const text = String(value ?? '')
  if (!Number.isSafeInteger(maxChars) || maxChars <= 0 || text.length <= maxChars) return text
  const omitted = text.length - maxChars
  return `${text.slice(0, maxChars)}\n…（已省略 ${omitted} 个字符）`
}

export function requireNonEmpty(value, name) {
  const text = String(value ?? '').trim()
  if (!text) throw new TypeError(`${name} 不能为空`)
  return text
}

export function messageText(value) {
  const parts = []
  const visit = (item, depth) => {
    if (depth > 12 || item === null || item === undefined) return
    if (typeof item === 'string') {
      if (item.trim()) parts.push(item)
      return
    }
    if (Array.isArray(item)) {
      for (const child of item) visit(child, depth + 1)
      return
    }
    if (typeof item !== 'object') return
    if (typeof item.text === 'string') {
      if (item.text.trim()) parts.push(item.text)
      return
    }
    if ('content' in item) visit(item.content, depth + 1)
    if ('message' in item) visit(item.message, depth + 1)
  }
  visit(value, 0)
  return parts.join('\n').trim()
}

export function extractHumanPrompt(messages) {
  return messages
    .filter(message => message?.source?.kind === 'user')
    .map(message => messageText(message.content))
    .filter(Boolean)
    .join('\n\n')
    .trim()
}

export function cwdForAgent(agent) {
  return agent?.session?.header?.cwd ?? process.cwd()
}

export function cwdForSession(session) {
  return session?.header?.cwd ?? process.cwd()
}

export function parseToolArguments(value) {
  if (value && typeof value === 'object' && !Array.isArray(value)) return value
  if (typeof value !== 'string' || !value.trim()) return {}
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed
      : { value: parsed }
  } catch {
    return { raw: truncateText(value, 4_000) }
  }
}

export function toolCallIdFromResult(message) {
  const source = message?.source
  return source?.kind === 'tool' && typeof source.callId === 'string'
    ? source.callId
    : undefined
}

export function collectPathCandidates(value, limit = 50) {
  const output = []
  const visit = (item, key, depth) => {
    if (output.length >= limit || depth > 12 || item === null || item === undefined) return
    if (Array.isArray(item)) {
      if (PATH_LIST_KEYS.has(key)) {
        for (const child of item) {
          if (typeof child === 'string' && child.trim()) output.push(child)
          if (output.length >= limit) return
        }
        return
      }
      for (const child of item) visit(child, key, depth + 1)
      return
    }
    if (typeof item !== 'object') {
      if (typeof item === 'string' && PATH_KEYS.has(key) && item.trim()) output.push(item)
      return
    }
    for (const [childKey, child] of Object.entries(item)) {
      const lowered = childKey.toLowerCase()
      if (typeof child === 'string' && PATH_KEYS.has(lowered) && child.trim()) {
        output.push(child)
      } else {
        visit(child, lowered, depth + 1)
      }
      if (output.length >= limit) return
    }
  }
  visit(value, '', 0)
  return [...new Set(output)].slice(0, limit)
}

export function statusForTurnEnd(reason) {
  const kind = reason?.kind
  if (kind === 'completed') return 'completed'
  if (kind === 'aborted') {
    const cause = reason?.reason?.kind
    return cause === 'user' ? 'cancelled' : 'interrupted'
  }
  if (kind === 'interrupted') return 'interrupted'
  return 'failed'
}

export function summaryForTurnEnd(reason) {
  switch (reason?.kind) {
    case 'completed':
      return 'DeepSeek Harness 本轮任务已完成。'
    case 'aborted':
      return reason?.reason?.kind === 'user'
        ? '用户取消了本轮任务。'
        : '本轮任务被中断。'
    case 'blocked':
      return '本轮任务在进入模型步骤前被拦截。'
    case 'max-tokens':
      return '本轮任务达到模型输出上限，未确认完整完成。'
    case 'error':
      return `本轮任务失败：${reason?.error?.message ?? '未知错误'}`
    case 'interrupted':
      return '本轮任务因会话恢复或进程中断而结束。'
    default:
      return `本轮任务结束：${JSON.stringify(reason ?? {})}`
  }
}

export function isAgentNaviTool(name) {
  return typeof name === 'string' && name.startsWith('agentnavi_')
}

export function contextArgs(query) {
  return ['context', requireNonEmpty(query, 'query'), '--json']
}

export function impactArgs(selector) {
  return ['impact', requireNonEmpty(selector, 'selector'), '--json']
}

export function historyArgs(query, limit = 10) {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
    throw new TypeError('limit 必须是 1 到 100 之间的整数')
  }
  const args = ['history']
  const text = String(query ?? '').trim()
  if (text) args.push(text)
  args.push('--limit', String(limit), '--json')
  return args
}

export function scanArgs(full = false) {
  return ['scan', ...(full ? ['--full'] : []), '--json']
}

export function hookArgs() {
  return ['hook', 'ingest', '--agent', 'generic']
}

export class SerialWorkQueue {
  constructor(onError = () => {}) {
    this.onError = onError
    this.chains = new Map()
  }

  enqueue(key, operation) {
    const previous = this.chains.get(key) ?? Promise.resolve()
    const running = previous
      .catch(() => undefined)
      .then(operation)
    const observed = running.catch(error => {
      this.onError(error, key)
      return undefined
    })
    const tail = observed.finally(() => {
      if (this.chains.get(key) === tail) this.chains.delete(key)
    })
    this.chains.set(key, tail)
    return observed
  }

  pending(key) {
    return this.chains.get(key) ?? Promise.resolve()
  }
}
