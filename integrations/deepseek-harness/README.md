# AgentNavi for DeepSeek Harness

这是 AgentNavi 的 DeepSeek Harness 本地插件组合包，第一阶段落地四项能力：

1. `Local Provider`：通过 Harness 的 `ctx.subprocess` 调用本机 `agentnavi` CLI；
2. 四个模型工具：`agentnavi_context`、`agentnavi_impact`、`agentnavi_history`、`agentnavi_scan`；
3. `agent/pre-step` 自动上下文注入：每个用户任务在第一步进入模型前先查询 AgentNavi；
4. L3 事件桥：把 Harness 的会话、用户任务、工具结果和回合结果转换为 AgentNavi 任务事实。

当前适配基线为 DeepSeek Harness 提交：

```text
47f943859bef60e4160492346772ded9b24f765a
```

DeepSeek Harness 仍处于开发者预览阶段。插件将不稳定 API 集中在 `src/context.js` 与 `src/events.js`，后续升级无需改动 AgentNavi Python 核心。

## 前置条件

- Python 3.11 或更高版本；
- Node.js 24 或更高版本；
- 已安装 DeepSeek Harness；
- `agentnavi` 命令可被 Harness 进程从 `PATH` 找到。

先安装并初始化 AgentNavi：

```bash
cd /path/to/AgentNavi
python -m pip install -e .
agentnavi init
```

## 安装到 Harness Profile

在 AgentNavi 仓库根目录执行：

```bash
dsh plugin --profile web add ./integrations/deepseek-harness
```

检查最终 Cordis 配置：

```bash
dsh --profile web --dump-config
```

然后按该 Profile 原本的方式启动 Harness。

## 插件组合

`cordis.patch.yml` 会插入四个相互解耦的插件：

```text
agentnavi-local-provider
        ↓ ctx.agentNavi
agentnavi-tools
agentnavi-context-router
agentnavi-l3-event-bridge
```

其中只有 Local Provider 知道 Python CLI 的存在。工具、上下文路由和事件桥只依赖 `ctx.agentNavi` 服务，因此未来可替换成常驻进程、HTTP 或 MCP Provider。

## Local Provider 配置

```yaml
- id: agentnavi-local-provider
  name: dsh-agentnavi/provider
  config:
    command: agentnavi
    # home: /absolute/path/to/.agentnavi
    timeoutMs: 15000
    maxOutputBytes: 1048576
    maxErrorBytes: 65536
    graceMs: 3000
```

Provider 不经过 shell 拼接命令，所有参数以 `argv` 传给 `ctx.subprocess`；标准输出、标准错误、超时和取消均有明确边界。

## 四个工具

### `agentnavi_context`

根据任务查询相关概念、候选文件、一跳依赖和历史任务。

```json
{"query":"修改会员升级与支付逻辑"}
```

### `agentnavi_impact`

分析修改文件或概念可能影响的上下游。

```json
{"selector":"src/membership/upgrade.py"}
```

### `agentnavi_history`

查询相关历史任务；关键词可为空。

```json
{"query":"会员升级","limit":10}
```

### `agentnavi_scan`

更新当前工作区索引，默认增量扫描。

```json
{"full":false}
```

## 自动上下文注入

`agent/pre-step` 监听器遵守 Harness 的 waterfall 约定：先调用 `next()`，再处理下游决定。

默认只在每个 turn 的第一步执行：

```text
用户任务
  → AgentNavi UserPromptSubmit
  → 建立 L3 任务并增量扫描
  → 返回紧凑项目上下文
  → 作为 plugin snapshot 加入当前模型请求
```

注入内容只是候选导航，不是修改结论。查询失败时插件 fail-open，原模型请求继续执行。

配置：

```yaml
- id: agentnavi-context-router
  name: dsh-agentnavi/context
  config:
    enabled: true
    firstStepOnly: true
    maxContextChars: 12000
```

## L3 事件桥

事件映射如下：

```text
session/created              → SessionStart
真实用户 user/message        → UserPromptSubmit（每 turn 幂等一次）
tool/call + tool/result      → PostToolUse / PostToolUseFailure
assistant/message            → 当前 turn 的结果摘要候选
turn/end                     → Stop + completed/failed/cancelled/interrupted
session/disposed             → SessionEnd
```

同一会话的异步写入通过串行队列执行，避免工具事件尚未落库时先关闭任务。默认忽略 `agentnavi_*` 工具自身，防止把导航查询记录成业务代码探索。

Harness 继续保存完整模型与工具轨迹；AgentNavi 只保存未来导航需要的任务、文件、概念和结果事实，不复制思维流或完整对话。

## 安全与边界

- 插件不向被索引项目写入 AgentNavi 专用文件；
- Hook 输入作为不可信 JSON 处理；
- 子进程命令不经过 shell；
- 输出与时间均有预算；
- 自动注入和事件桥均 fail-open；
- 本地 Provider 必须与 Harness 工作区共享同一文件系统；远程沙箱后续应使用 HTTP/MCP Provider。

## 验证

```bash
cd integrations/deepseek-harness
npm run check
npm test
```

当前测试覆盖：四类 CLI 参数、用户消息筛选、结束状态映射、工具路径提取、输出预算和会话内事件串行化。
