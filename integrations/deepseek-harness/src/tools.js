import z from '@deepseek-ai/schemastery'
import { defineTool } from '@deepseek-ai/dsh-tools'
import {
  cwdForAgent,
  requireNonEmpty,
  truncateText,
} from './protocol.js'

export const name = 'agentnavi-tools'
export const inject = ['agentNavi', 'tools']

export const Config = z.object({
  maxRenderChars: z.number().default(12_000),
})

const OPEN_OBJECT = { type: 'object', additionalProperties: true }

function renderValue(label, value, maxChars) {
  return [{
    type: 'text',
    text: truncateText(`${label}\n${JSON.stringify(value, null, 2)}`, maxChars),
  }]
}

export function apply(ctx, config = {}) {
  const maxRenderChars = Number.isSafeInteger(config.maxRenderChars) && config.maxRenderChars > 0
    ? config.maxRenderChars
    : 12_000

  ctx.tools.register(defineTool({
    name: 'agentnavi_context',
    description: '根据当前任务，从项目概念、文件依赖和历史任务中返回受上限约束的候选上下文。',
    parameters: {
      query: { type: 'string', required: true, description: '当前要完成的任务或问题。' },
    },
    output: {
      schema: OPEN_OBJECT,
      render: (_args, value) => renderValue('AgentNavi 任务上下文', value, maxRenderChars),
    },
    async execute(args, exec) {
      return ctx.agentNavi.context({
        cwd: cwdForAgent(exec.agent),
        query: requireNonEmpty(args.query, 'query'),
        signal: exec.signal,
      })
    },
  }))

  ctx.tools.register(defineTool({
    name: 'agentnavi_impact',
    description: '分析修改一个文件、概念或节点可能影响的文件、概念和依赖方向。',
    parameters: {
      selector: { type: 'string', required: true, description: '文件路径、概念名或图谱节点选择器。' },
    },
    output: {
      schema: OPEN_OBJECT,
      render: (_args, value) => renderValue('AgentNavi 影响分析', value, maxRenderChars),
    },
    async execute(args, exec) {
      return ctx.agentNavi.impact({
        cwd: cwdForAgent(exec.agent),
        selector: requireNonEmpty(args.selector, 'selector'),
        signal: exec.signal,
      })
    },
  }))

  ctx.tools.register(defineTool({
    name: 'agentnavi_history',
    description: '查询与关键词相关的历史任务、结果与状态；关键词为空时返回最近任务。',
    parameters: {
      query: { type: 'string', description: '历史任务关键词，可留空。' },
      limit: { type: 'number', description: '最多返回多少条，范围 1 到 100。' },
    },
    output: {
      schema: { type: 'array', items: OPEN_OBJECT },
      render: (_args, value) => renderValue('AgentNavi 历史任务', value, maxRenderChars),
    },
    async execute(args, exec) {
      const limit = args.limit ?? 10
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
        throw new TypeError('limit 必须是 1 到 100 之间的整数')
      }
      return ctx.agentNavi.history({
        cwd: cwdForAgent(exec.agent),
        query: String(args.query ?? '').trim(),
        limit,
        signal: exec.signal,
      })
    },
  }))

  ctx.tools.register(defineTool({
    name: 'agentnavi_scan',
    description: '更新当前项目的 AgentNavi 索引；默认增量扫描，必要时可请求全量重建。',
    parameters: {
      full: { type: 'boolean', description: '是否执行全量扫描。' },
    },
    output: {
      schema: OPEN_OBJECT,
      render: (_args, value) => renderValue('AgentNavi 扫描结果', value, maxRenderChars),
    },
    async execute(args, exec) {
      return ctx.agentNavi.scan({
        cwd: cwdForAgent(exec.agent),
        full: args.full === true,
        signal: exec.signal,
      })
    },
  }))
}

export default { name, inject, Config, apply }
