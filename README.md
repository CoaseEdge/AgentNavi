# AgentNavi

AgentNavi 是一个**独立于项目仓库、独立于具体 Agent、也独立于 Obsidian 的三层项目认知导航引擎**。

它解决的不是“怎样让模型读更多文件”，而是更基础的问题：

> 一个 Agent 收到任务后，怎样先确定应该读什么，再只读取真正相关的文件？

AgentNavi 把项目外部化为三层可查询图谱：

```text
L3 任务图：为什么做、做过什么、结果怎样
        ↓
L2 语义图：项目里有哪些概念，它们怎样关联
        ↓
L1 物理图：文件、引用、导入、测试怎样连接
        ↓
真实项目仓库：唯一事实来源
```

Agent 默认沿着：

```text
当前任务 → 相关概念 → 真实文件 → 必要的一跳依赖
```

逐层下钻，而不是从仓库根目录开始无目的扫描。

## 当前实现

第一版已经具备可运行闭环：

- **外置项目注册表**：项目仓库无需加入 `.agentnavi`、`_graph` 或 `.obsidian`。
- **L1 物理图自动构建**：发现文件，解析 Python、JavaScript、TypeScript、Markdown 依赖，并推断常见测试关系。
- **L2 语义图自动构建**：按模块路径、文档标题、标题层级、符号和物理依赖形成保守语义概念及概念关系。
- **可插拔语义提供器**：可接本地模型、云模型或纯规则程序，但默认不依赖任何模型供应商。
- **L3 任务图自动沉淀**：通过 Codex / Claude Code Hook 记录任务、读取、修改、测试、结果和受影响概念。
- **上下文路由 CLI**：查询概念、建议文件、影响范围和历史任务。
- **Obsidian 投影**：从外部 SQLite 图谱生成可删除、可重建的 Markdown Vault 视图。
- **全局集成安装器**：幂等合并用户已有 Hook 配置，并安装“上下文优先”Skill。
- **零运行时第三方依赖**：核心只使用 Python 标准库。

## 核心原则

### 1. 仓库才是真实事实

AgentNavi 的图谱是 `Derived State`，不是 `Source of Truth`。

删除 `~/.agentnavi/agentnavi.db` 后，项目仍应正常运行；重新扫描即可恢复 L1/L2。L3 可以从保留的 Agent 事件重新生成，后续版本会补齐完整重放能力。

### 2. 图谱与项目仓库解耦

默认数据目录：

```text
~/.agentnavi/
├── config.json
├── agentnavi.db
└── obsidian-vault/
```

被索引项目不会被写入任何 AgentNavi 专用文件。

### 3. 自动化分层处理

- L1：确定性程序维护。
- L2：保守规则自动推断，可由外部语义提供器增强。
- L3：Hook 采集确定事件，任务摘要属于可重新生成的解释层。

### 4. Obsidian 只是人类界面

Agent 查询 SQLite 图谱；Obsidian 读取生成的 Markdown。两者是同级消费者，AgentNavi 不依赖 Obsidian 才能工作。

## 安装

要求 Python 3.11 或更高版本。

开发安装：

```bash
git clone https://github.com/Andrewlislin/AgentNavi.git
cd AgentNavi
python -m pip install -e .
```

也可以用 `pipx` 隔离安装：

```bash
pipx install -e .
```

初始化外部工作区：

```bash
agentnavi init
```

## 最短使用路径

进入任意项目：

```bash
cd /path/to/your-project
agentnavi project add .
```

第一次会完成全量扫描，以后默认增量更新。

查询当前任务所需上下文：

```bash
agentnavi context "修改会员升级与支付逻辑"
```

典型输出会给出：

- 相关概念；
- 一跳语义依赖；
- 建议优先读取的真实文件；
- 相关历史任务；
- 每条推断关系的来源和置信度。

分析文件影响范围：

```bash
agentnavi impact src/membership/upgrade.py
```

查询历史任务：

```bash
agentnavi history "会员升级"
```

增量更新：

```bash
agentnavi scan
```

强制全量重建：

```bash
agentnavi scan --full
```

## 接入 Codex 与 Claude Code

安装用户级 Hook 和“上下文优先”Skill：

```bash
agentnavi integration install codex
agentnavi integration install claude
```

同时安装：

```bash
agentnavi integration install all
```

安装器会：

1. 读取已有 JSON 配置；
2. 保留非 AgentNavi Hook；
3. 幂等替换 AgentNavi 自己的 Hook；
4. 写入前生成 `.agentnavi.bak` 备份；
5. 把 Skill 安装到各 Agent 的用户级技能目录。

安装后应在 Codex 或 Claude Code 内审查并信任新增 Hook。Hook 命令必须能够从 Agent 进程的 `PATH` 找到 `agentnavi`。

只查看配置模板，不写文件：

```bash
agentnavi integration show codex
agentnavi integration show claude
agentnavi integration show skill
```

仓库内也提供了可人工合并的示例：[`integrations/`](integrations/README.md)。

## Obsidian 视图

导出所有项目：

```bash
agentnavi export obsidian
```

导出单个项目：

```bash
agentnavi export obsidian --project flowit-write
```

指定已有 Vault：

```bash
agentnavi export obsidian --destination ~/Documents/MyVault
```

AgentNavi 只管理目标 Vault 内的：

```text
AgentNavi/
```

子目录。重新导出时可以安全删除并重建该目录，不会修改项目仓库。

## 常用命令

```text
agentnavi init
agentnavi project add [路径]
agentnavi project list
agentnavi project remove <项目>
agentnavi scan [项目] [--full]
agentnavi query <关键词>
agentnavi context <任务描述>
agentnavi impact <相对文件路径>
agentnavi history [关键词]
agentnavi task start|event|close|list
agentnavi export obsidian
agentnavi integration show|install
agentnavi hook ingest --agent codex|claude|generic
agentnavi doctor
```

所有查询命令都支持在项目子目录运行；AgentNavi 会按“最长匹配根目录”自动定位项目。

## 外部语义提供器

默认 L2 不调用模型，只做可解释、保守的路径与依赖聚合。需要更深语义时，可以配置一个外部命令：

```bash
export AGENTNAVI_SEMANTIC_COMMAND="python /path/to/provider.py"
agentnavi scan --full
```

提供器从标准输入接收 JSON，从标准输出返回：

```json
{
  "concepts": [
    {
      "key": "membership-upgrade",
      "label": "会员升级",
      "files": ["src/membership/upgrade.py"],
      "confidence": 0.91
    }
  ],
  "relations": [
    {
      "from": "membership-upgrade",
      "relation": "depends_on",
      "to": "payment",
      "confidence": 0.86,
      "evidence": ["src/membership/upgrade.py:12"]
    }
  ]
}
```

完整协议见 [`docs/semantic-provider.md`](docs/semantic-provider.md)。

## 隐私与安全边界

AgentNavi 默认在本机记录：

- 项目绝对路径；
- 文件结构元数据和依赖；
- 用户任务文本；
- Agent 工具事件；
- 最终任务摘要；
- 部分 Shell 命令文本。

它不会默认上传数据，也不会保存完整项目正文，但任务和命令仍可能含敏感信息。请保护 `~/.agentnavi`，不要把数据库同步到不可信位置。

Hook 采用 **fail-open**：索引失败不会阻止主 Agent 工作。这适合导航和记忆，不应被当作安全强制边界。

## 当前限制

这是 `0.1.0` 架构基线，不是完整产品：

- L1 深度解析目前集中在 Python、JavaScript、TypeScript 和 Markdown；其他文件仍会成为节点，但依赖解析较浅。
- L2 默认是保守启发式，不等于人工维护的业务本体；外部提供器输出也必须以证据和置信度标注。
- 当前没有常驻文件监听器；索引在手动扫描、SessionStart、UserPromptSubmit 和 Stop 时更新。
- 本机 Hook 只适用于能访问同一文件系统和 SQLite 数据库的本地 Agent；远程沙箱需要未来的 HTTP/MCP Context Service。
- 尚未提供严格的 Token 节省基准测试，当前价值主要来自减少候选文件和重复探索。
- Obsidian 目前是单向投影；在 Obsidian 中手工修改生成页不会回写数据库。

## 文档

- [架构说明](docs/architecture.md)
- [数据模型](docs/data-model.md)
- [Hook 工作流](docs/hooks.md)
- [外部语义提供器协议](docs/semantic-provider.md)
- [开发路线](docs/roadmap.md)
- [架构决策记录](docs/decisions/)

## 测试

```bash
python -m compileall -q src
python -m unittest discover -s tests -v
```

当前测试覆盖零侵入扫描、L1/L2 构建、中文上下文路由、增量更新、L3 Hook 闭环、Obsidian 导出、外部语义提供器和集成配置幂等合并。

## 许可证

MIT
