import assert from 'node:assert/strict'
import test from 'node:test'
import {
  SerialWorkQueue,
  collectPathCandidates,
  contextArgs,
  extractHumanPrompt,
  historyArgs,
  impactArgs,
  messageText,
  parseToolArguments,
  scanArgs,
  statusForTurnEnd,
  summaryForTurnEnd,
  toolCallIdFromResult,
  truncateText,
} from '../src/protocol.js'

test('只从真实用户消息提取当前任务', () => {
  const prompt = extractHumanPrompt([
    {
      source: { kind: 'user' },
      content: [{ type: 'text', text: '修改会员升级逻辑' }],
    },
    {
      source: { kind: 'plugin', plugin: 'agentnavi-context-router' },
      content: [{ type: 'text', text: '这段不能再次成为任务' }],
    },
  ])
  assert.equal(prompt, '修改会员升级逻辑')
})

test('构造四类 AgentNavi CLI 参数时不经过 shell', () => {
  assert.deepEqual(contextArgs('会员升级'), ['context', '会员升级', '--json'])
  assert.deepEqual(impactArgs('src/membership/upgrade.py'), [
    'impact',
    'src/membership/upgrade.py',
    '--json',
  ])
  assert.deepEqual(historyArgs('支付', 5), ['history', '支付', '--limit', '5', '--json'])
  assert.deepEqual(scanArgs(true), ['scan', '--full', '--json'])
})

test('DeepSeek Harness 结束原因映射为 AgentNavi 终态', () => {
  assert.equal(statusForTurnEnd({ kind: 'completed' }), 'completed')
  assert.equal(statusForTurnEnd({ kind: 'aborted', reason: { kind: 'user' } }), 'cancelled')
  assert.equal(statusForTurnEnd({ kind: 'aborted', reason: { kind: 'parent' } }), 'interrupted')
  assert.equal(statusForTurnEnd({ kind: 'error', error: { message: 'boom' } }), 'failed')
  assert.match(summaryForTurnEnd({ kind: 'max-tokens' }), /输出上限/)
})

test('工具参数、结果文本、调用 ID 与路径可以保守提取', () => {
  assert.deepEqual(parseToolArguments('{"path":"src/app.py"}'), { path: 'src/app.py' })
  assert.deepEqual(parseToolArguments('not-json'), { raw: 'not-json' })
  const message = {
    source: { kind: 'tool', callId: 'call-1' },
    content: [{
      type: 'tool-result',
      content: [{ type: 'text', text: '读取完成' }],
    }],
  }
  assert.equal(toolCallIdFromResult(message), 'call-1')
  assert.equal(messageText(message), '读取完成')
  assert.deepEqual(collectPathCandidates({
    input: { path: 'src/app.py' },
    result: { file_paths: ['tests/test_app.py', 'src/app.py'] },
  }), ['src/app.py', 'tests/test_app.py'])
})

test('文本输出受明确字符预算约束', () => {
  assert.equal(truncateText('abc', 3), 'abc')
  assert.match(truncateText('abcdef', 3), /^abc\n…/)
})

test('同一会话的异步事件严格串行，不同会话互不共享链', async () => {
  const order = []
  const queue = new SerialWorkQueue(error => { throw error })
  const first = queue.enqueue('session-a', async () => {
    await new Promise(resolve => setTimeout(resolve, 10))
    order.push('a1')
  })
  const second = queue.enqueue('session-a', async () => {
    order.push('a2')
  })
  const other = queue.enqueue('session-b', async () => {
    order.push('b1')
  })
  await Promise.all([first, second, other])
  assert.ok(order.indexOf('a1') < order.indexOf('a2'))
  assert.ok(order.includes('b1'))
})
