# AgentNavi

AgentNavi 是一个**独立于项目仓库、独立于具体 Agent、也独立于 Obsidian 的项目上下文导航引擎**。

它不负责替你写代码，而是先回答一个更基础的问题：

> Agent 收到任务后，应该先读哪些文件，过去为什么这样设计，哪些关系已经被人确认或否定？

AgentNavi 把项目组织为三层：

```text
L3 任务图：为什么做、读过什么、改过什么、测试与结果
        ↓
L2 语义图：项目里有哪些概念，它们怎样关联
        ↓
L1 物理图：文件、导入、引用和测试怎样连接
        ↓
真实项目仓库：唯一运行事实来源
```

Agent 默认沿着：

```text
当前任务 → 相关概念 → 候选文件 → 必要的一跳依赖
```

逐层下钻，而不是每次重新扫描整个仓库。

## 0.2.0 新增的三条可靠性链路

### 1. L3 可以从独立日志完整重放

任务、工具事件、会话结束和任务结果会**先追加写入**：

```text
~/.agentnavi/events.jsonl
```

再写入 SQLite。即使数据库在日志写入后损坏，事件仍可恢复：

```bash
agentnavi event-log verify
agentnavi replay l3 --reset --strict
```

升级前已经存在于 SQLite 的任务历史，可以先补写：

```bash
agentnavi event-log backfill
```

重放可恢复：

- 项目快照；
- 会话与结束状态；
- 任务原始提示、状态和摘要；
- 读取、修改、搜索、测试和命令事件；
- 任务到文件的关系；
- 任务到受影响概念的关系。

详见 [L3 事件日志与重放](docs/l3-replay.md)。

### 2. 用基准测试验证是否真的减少探索成本

基准分成两层：

1. **可重复检索基准**：比较全仓库、文件名搜索和 AgentNavi 返回的候选文件；
2. **真实 Agent 对照**：记录实际会话读取文件数、探索 Token、耗时和任务成功状态。

运行检索基准：

```bash
agentnavi benchmark evaluate examples/benchmark-cases.json \
  --suite first-proof

agentnavi benchmark compare --suite first-proof
```

记录一次真实会话：

```bash
agentnavi benchmark record <task_id> \
  --suite real-agent \
  --case membership-upgrade \
  --mode agentnavi \
  --expected src/membership/upgrade.py \
  --expected src/payment/service.py \
  --exploration-tokens 2800 \
  --duration-ms 64000 \
  --status success
```

“减少 Token”不是无条件成立。比较器只把以下配对计入正式节省结果：

- 双方必要文件召回率均不低于 95%；
- 真实 Agent 对照中，双方都明确 `success=true`。

低召回、失败或未验证的用例会被排除，避免把“少读了必要文件”误报为节省。

详见 [基准测试](docs/benchmark.md)。

### 3. L2 人工校正作为独立 Overlay 长期保存

自动语义图仍然可以随时重建；人的接受、拒绝、重命名、合并和文件映射则写入独立事实日志：

```text
~/.agentnavi/semantic-overlays.jsonl
```

它不进入项目仓库，也不会被下一次扫描覆盖。

查看待审查项：

```bash
agentnavi semantic review list
```

接受或拒绝：

```bash
agentnavi semantic review accept <review_id>
agentnavi semantic review reject <review_id> --note "代码引用不代表稳定业务依赖"
```

手工校正：

```bash
agentnavi semantic correction add rename_concept membership \
  --label "会员与升级"

agentnavi semantic correction add merge_concept subscription \
  --object membership

agentnavi semantic correction add map_file membership \
  --relation implemented_by \
  --object src/membership/upgrade.py
```

验证和恢复人工校正日志：

```bash
agentnavi semantic log verify
agentnavi semantic log replay --reset --strict
agentnavi scan
```

详见 [语义审查与人工校正](docs/semantic-review.md)。

## 安装

要求 Python 3.11 或更高版本。

```bash
git clone https://github.com/Andrewlislin/AgentNavi.git
cd AgentNavi
python -m pip install -e .
agentnavi init
```

也可以使用 `pipx` 隔离安装：

```bash
pipx install -e .
```

核心运行时只使用 Python 标准库。

## 最短使用路径

进入一个项目：

```bash
cd /path/to/your-project
agentnavi project add .
```

第一次会完成全量扫描；以后默认增量更新。

查询当前任务：

```bash
agentnavi context "修改会员升级与支付逻辑"
```

分析文件影响：

```bash
agentnavi impact src/membership/upgrade.py
```

查询历史任务：

```bash
agentnavi history "会员升级"
```

更新索引：

```bash
agentnavi scan
agentnavi scan --full
```

## 接入 Codex 与 Claude Code

```bash
agentnavi integration install codex
agentnavi integration install claude
# 或者
agentnavi integration install all
```

安装器会保留已有 Hook，备份原配置，并安装“上下文优先”Skill。

Hook 的工作流程：

```text
SessionStart
  → 注册项目、增量扫描、注入项目概览

UserPromptSubmit
  → 建立任务、写入 L3 日志、注入任务上下文

PostToolUse
  → 记录读取、修改、搜索、测试和命令

Stop
  → 增量扫描、关联受影响概念、保存结果、关闭任务

SessionEnd
  → 记录会话结束；未结束任务标记为 interrupted
```

Hook 采用 fail-open：AgentNavi 失败不会阻断主 Agent 工作。

## Obsidian 视图

```bash
agentnavi export obsidian
```

导出到已有 Vault：

```bash
agentnavi export obsidian --destination ~/Documents/MyVault
```

AgentNavi 只管理目标 Vault 内的 `AgentNavi/` 子目录。Obsidian 是可删除、可重建的人类浏览视图，不是底层数据库。

## 外置数据目录

默认：

```text
~/.agentnavi/
├── config.json
├── agentnavi.db                 # 可重建的查询投影与缓存
├── events.jsonl                 # L3 权威事实日志
├── semantic-overlays.jsonl      # 人工语义校正权威日志
└── obsidian-vault/              # 可重建投影
```

被索引项目不会被写入 `.agentnavi`、`_graph` 或 `.obsidian`。

## 当前能力

- Git 感知文件发现与增量扫描；
- Python、JavaScript、TypeScript、Markdown 物理依赖；
- 常见测试关系推断；
- 保守自动语义图与外部语义提供器；
- L3 append-only 事件日志、旧数据回填、校验和幂等重放；
- 可重复检索基准和真实 Agent 对照记录；
- L2 语义候选审查与持久化人工 Overlay；
- 上下文、影响、历史查询；
- Codex / Claude Code Hook 与 Skill；
- Obsidian 单向投影。

## 当前边界

- L1 深度解析主要覆盖 Python、JavaScript、TypeScript 和 Markdown；
- 检索基准中的 Token 是按文件体积得到的透明估算，不等同于模型供应商账单；严格证明应录入真实会话 Token；
- 人工校正目前通过 CLI 审查，尚无图形化工作台；
- Obsidian 仍为单向投影，不直接回写生成页；
- 本机 Agent 可以直接使用 SQLite，远程沙箱仍需要未来的 HTTP/MCP Context Service；
- 尚未实现跨项目概念统一和组织级权限。

## 常用命令

```text
agentnavi init
agentnavi project add|list|remove
agentnavi scan
agentnavi query
agentnavi context
agentnavi impact
agentnavi history
agentnavi task start|event|close|list
agentnavi event-log verify|backfill
agentnavi replay l3
agentnavi benchmark evaluate|record|compare
agentnavi semantic review list|accept|reject
agentnavi semantic correction add|list|remove|apply
agentnavi semantic log verify|backfill|replay
agentnavi export obsidian
agentnavi integration show|install
agentnavi doctor
```

## 文档

- [架构说明](docs/architecture.md)
- [数据模型](docs/data-model.md)
- [Hook 工作流](docs/hooks.md)
- [L3 事件日志与重放](docs/l3-replay.md)
- [基准测试](docs/benchmark.md)
- [语义审查与人工校正](docs/semantic-review.md)
- [外部语义提供器协议](docs/semantic-provider.md)
- [开发路线](docs/roadmap.md)
- [架构决策记录](docs/decisions/)

## 测试

```bash
python -m compileall -q src
python -m unittest discover -s tests -v
```

## 许可证

MIT
