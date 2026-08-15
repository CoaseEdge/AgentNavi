# Hook 工作流

## 目标

Hook 从 Agent 已经产生的生命周期事件构造 L3，不要求 Agent 手工维护日志。

## 安装

```bash
agentnavi integration install codex
agentnavi integration install claude
```

## 生命周期

| Agent 事件 | AgentNavi 行为 |
|---|---|
| `SessionStart` | 注册项目、增量扫描、写会话事件、注入项目概览 |
| `UserPromptSubmit` | 创建任务、记录 prompt、注入任务上下文 |
| `PostToolUse` | 记录读取、修改、搜索、测试或命令 |
| `PostToolUseFailure` | 记录失败工具事件 |
| `Stop` | 记录结果、增量扫描、关联概念、关闭任务 |
| `SessionEnd` | 记录结束；必要时把活动任务标为 interrupted |

## 日志优先

所有 L3 状态变化先追加到 `events.jsonl`，再在 SQLite 事务中物化。日志成功、数据库失败时可通过重放恢复。

## 为什么 Stop 才更新语义图

```text
PostToolUse：只记录 Dirty Event
Stop：一次增量扫描 → L2 重建与 Overlay → 任务关联概念
```

`File Change` 不等于 `Semantic Change`，因此不在每次写文件后重建 L2。

## Fail-open

Hook 适合导航和记忆，不是安全强制边界。索引或记录错误会写标准错误，但不阻断主 Agent。

## 性能

- PostToolUse：一次 JSONL append 和短 SQLite 事务；
- SessionStart / UserPromptSubmit：增量扫描；
- Stop：增量扫描和任务归因；
- SessionEnd：短收尾。

大型仓库应先手工运行 `agentnavi project add .` 完成首次索引。
