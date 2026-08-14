# Agent 集成

推荐直接运行：

```bash
agentnavi integration install codex
agentnavi integration install claude
```

这里的文件用于审查、手工合并或无法运行安装器的环境：

- `codex/hooks.json`
- `claude/settings.fragment.json`
- `context-first/SKILL.md`

这些配置均调用全局命令：

```bash
agentnavi hook ingest --agent <agent>
```

因此必须先安装 AgentNavi，并确保对应 Agent 进程能从 `PATH` 找到该命令。

用户级 Skill 安装位置：

```text
Codex：~/.agents/skills/context-first/SKILL.md
Claude Code：~/.claude/skills/context-first/SKILL.md
```

用户级 Hook 配置位置：

```text
Codex：~/.codex/hooks.json
Claude Code：~/.claude/settings.json
```

不要直接用示例覆盖已有配置；使用安装器或人工合并 `hooks` 字段。
