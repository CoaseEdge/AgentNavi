# DeepSeek Harness 集成

## 一、定位

DeepSeek Harness 负责模型调用、Agent Loop、工具调度、会话日志和运行时；AgentNavi 负责项目的物理图、语义图、历史任务和上下文路由。

```text
DeepSeek Harness
  模型、工具、会话、权限、运行循环
            │
            ▼
AgentNavi 插件组合
  Local Provider、四个工具、pre-step 路由、L3 事件桥
            │
            ▼
AgentNavi Python 内核
  L1 / L2 / L3、SQLite、events.jsonl、semantic-overlays.jsonl
```

两边的数据职责不同：

- Harness 会话日志是完整运行轨迹；
- AgentNavi L3 是面向未来检索的项目任务索引；
- 不复制模型思维流和完整对话正文。

## 二、目录

```text
integrations/deepseek-harness/
├── package.json
├── cordis.patch.yml
├── README.md
├── src/
│   ├── service.js
│   ├── provider.js
│   ├── tools.js
│   ├── context.js
│   ├── events.js
│   ├── protocol.js
│   └── index.js
└── test/
    └── protocol.test.js
```

## 三、服务边界

`LocalAgentNaviProvider` 把能力发布为 `ctx.agentNavi`：

```text
context
impact
history
scan
ingestHook
startSession
startTurn
finishTurn
endSession
```

工具和事件插件不直接启动 Python，也不读取 AgentNavi 数据库。未来替换 Provider 时，消费层不需要重写。

## 四、自动路由

`agent/pre-step` 在模型请求生成前运行。插件只读取真实用户消息，忽略其他插件注入内容，避免把 AgentNavi 自己的上下文再次当作新任务。

同一 `session + turn` 的任务创建由 Local Provider 幂等缓存保护；即使 `agent/pre-step` 与 `session/event` 都观察到用户输入，也只会创建一次 AgentNavi 任务。

## 五、事件顺序

Harness 的 `session/event` 是 fire-and-forget 观察流。AgentNavi 写入可能涉及磁盘、扫描和 SQLite，因此事件桥不会在监听器中无序启动多个子进程，而是按 session 建立 Promise 串行链：

```text
任务建立
  → 工具结果 1
  → 工具结果 2
  → turn/end 关闭任务
  → session/disposed 结束会话
```

不同会话使用不同链，可以并行运行。

## 六、任务状态

Harness 的 turn 结束原因被收敛为 AgentNavi 终态：

| Harness 原因 | AgentNavi 状态 |
|---|---|
| `completed` | `completed` |
| 用户触发的 `aborted` | `cancelled` |
| 父任务、Hook 或运行时触发的 `aborted` | `interrupted` |
| `interrupted` | `interrupted` |
| `blocked`、`error`、`max-tokens` | `failed` |

Python Hook 适配层同时接受显式 `task_status` 和结构化 `turn_end_reason`。其他 Agent 不传这些字段时保持原来的 `completed` 默认行为。

## 七、兼容策略

当前适配基线：

```text
DeepSeek Harness commit 47f943859bef60e4160492346772ded9b24f765a
```

依赖的正式扩展点包括：

- Cordis `Service` 与 `inject`；
- `ctx.subprocess.spawn()`；
- `ctx.tools.register(defineTool())`；
- `agent/pre-step` waterfall；
- `session/created`、`session/event`、`session/disposed`；
- `createUserMessage()` 的 plugin snapshot source。

Harness 升级时，优先运行本目录测试，再核对 `context.js` 和 `events.js` 的事件形状。插件尚未声称兼容所有未来预览版本。

## 八、当前边界

- 仅实现本地 Provider，要求 Harness 与项目文件处于同一文件系统；
- 暂无图形化语义审查面板；
- 暂不自动读取 Harness 的供应商 Token 账单；
- 没有把 Harness 完整会话日志导入 AgentNavi；
- 远程沙箱和多机部署留给后续 HTTP/MCP Provider。
