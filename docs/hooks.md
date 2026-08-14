# Hook 工作流

## 目标

Hook 不是用来强迫 Agent 每次手工写项目日志，而是从 Agent 本来就产生的生命周期事件中自动构造 L3，并在合适时间触发 L1/L2 增量更新。

## 安装

```bash
agentnavi integration install codex
agentnavi integration install claude
```

示例配置位于：

- `integrations/codex/hooks.json`
- `integrations/claude/settings.fragment.json`

安装器会保留已有 Hook、移除旧 AgentNavi 条目后追加新条目，并备份原文件。

## 事件映射

| Agent 生命周期事件 | AgentNavi 行为 |
|---|---|
| `SessionStart` | 自动注册项目、扫描、注入项目概览 |
| `UserPromptSubmit` | 创建 L3 任务、扫描、注入任务上下文 |
| `PostToolUse` | 记录读取、修改、搜索、测试或命令 |
| `PostToolUseFailure` | Claude Code 中记录失败工具事件 |
| `Stop` | 扫描、归因受影响概念、保存摘要、关闭任务 |
| `SessionEnd` | 快速结束会话；不做重扫描 |

## 工具分类

AgentNavi 根据工具名称和 Shell 命令进行保守分类：

```text
Read / fetch_file              → read
Edit / Write / apply_patch     → modify
Grep / Glob / Search / Find    → search
pytest / npm test / go test    → test
其他 Bash / Shell              → command
```

文件路径来源包括：

- `file_path`、`path`、`notebook_path` 等工具输入字段；
- 列表型 `paths` / `files` 字段；
- `apply_patch` 的 Add/Update/Delete File 标记；
- Shell 命令中明确存在、且位于项目内的文件参数。

不在项目根目录内的路径不会进入项目文件图。

## 为什么 Stop 才更新语义图

如果每次文件写入都重建 L2，会造成不必要开销和抖动。

当前策略：

```text
PostToolUse：只记录 Dirty Event
Stop：一次增量扫描 → 重建 L2 → 任务关联概念
```

也就是：

```text
File Change ≠ Semantic Change
```

只有新的物理结构经过聚合后，才会改变语义关系。

## Fail-open

Hook 记录失败时：

- 标准错误输出简短错误；
- 返回 0；
- 不阻断 Agent 的主要工作。

因此 Hook 适合作为导航和记忆基础设施，不适合作为安全策略的唯一强制机制。

## 性能边界

- `PostToolUse` 只做短 SQLite 写入，不扫描项目；
- `SessionStart` 和 `UserPromptSubmit` 做增量扫描；
- `Stop` 做增量扫描与任务归因；
- `SessionEnd` 只结束会话，以满足较短生命周期超时。

超大型仓库建议先手动运行：

```bash
agentnavi project add .
```

完成初次全量索引，后续 Hook 只处理增量。

## 官方配置位置

截至 2026 年 8 月：

- Codex 用户级 Hook：`~/.codex/hooks.json`
- Claude Code 用户级设置：`~/.claude/settings.json`
- Codex 用户级 Skill：`~/.agents/skills/<skill>/SKILL.md`
- Claude Code 用户级 Skill：`~/.claude/skills/<skill>/SKILL.md`

参考：

- [Codex Hooks](https://developers.openai.com/codex/hooks)
- [Codex Skills](https://developers.openai.com/codex/skills)
- [Claude Code Hooks](https://code.claude.com/docs/en/hooks)
- [Claude Code Skills](https://code.claude.com/docs/en/skills)
