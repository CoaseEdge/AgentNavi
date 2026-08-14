# AgentNavi 架构说明

## 一、问题定义

大型项目中的 Agent 成本，往往不是来自真正的修改，而是来自修改前的探索：

```text
列目录 → 搜关键词 → 读文件 → 发现依赖 → 再搜索 → 再读取
```

如果每个会话都重新执行这套过程，项目越大，Token、时延和错误概率越高。

AgentNavi 把问题重新定义为：

> 给定任务 T，如何降低“哪些文件与 T 有关”的不确定性？

因此它不是文档生成器，而是一个外部上下文索引与路由层。

## 二、总架构

```text
┌─────────────────────────────────────────────┐
│ 真实工作世界                                 │
│ Git 仓库 / 写作项目 / 研究资料 / 视频工程       │
└──────────────────────┬──────────────────────┘
                       │ 文件扫描、Git、Agent Hook
                       ▼
┌─────────────────────────────────────────────┐
│ AgentNavi Context Engine                    │
│                                             │
│ L1 物理图  文件、导入、引用、测试               │
│ L2 语义图  概念、概念关系、概念到文件            │
│ L3 任务图  目标、事件、修改、测试、结果           │
│                                             │
│ Registry / Scanner / Semantic Builder       │
│ Task Recorder / Query Router / Exporter     │
└───────────────┬───────────────────┬─────────┘
                │                   │
                ▼                   ▼
       Codex / Claude / Agent     Obsidian
       查询紧凑上下文               人类浏览视图
```

关键点是：**Agent 和 Obsidian 都是图谱消费者。**

## 三、外置旁车架构

项目注册表只保存：

```text
project_id → project_root
```

所有图谱状态默认位于：

```text
~/.agentnavi/agentnavi.db
```

这带来四个结果：

1. 项目仓库完全不需要知道 AgentNavi 存在；
2. 同一套引擎可以服务多个代码、写作、研究或视频项目；
3. 更换 Agent 或 Obsidian 不影响底层图谱；
4. 图谱可以删除重建，项目仍然正常运行。

## 四、三层图谱

### L1：物理图

L1 只记录可以从文件系统和语法确定的事实：

```text
A.py imports B.py
A.ts imports B.ts
chapter.md references paper.pdf
test_user.py tests user.py
```

当前实现：

- Git 感知文件发现：优先 `git ls-files -co --exclude-standard`；
- 普通目录回退扫描；
- Python AST 导入解析；
- JavaScript / TypeScript 导入、导出和 `require` 解析；
- Markdown 链接与 Wiki Link 解析；
- 常见测试文件到源码文件推断；
- 文件摘要、语言、标题、标题层级和符号元数据；
- 基于 mtime、大小与摘要的增量更新。

L1 不调用 LLM。

### L2：语义图

L2 回答：

```text
这个文件属于什么概念？
概念之间有什么关系？
一个概念由哪些文件实现、测试、配置或记录？
```

默认构建流程：

1. 从模块目录和文件名形成候选概念；
2. 用 Markdown 标题、包描述、符号等元数据改进概念名称和检索别名；
3. 将文件按用途连接为 `implemented_by`、`tested_by`、`documented_by`、`configured_by`；
4. 把跨概念 L1 依赖聚合为 `depends_on` 或 `related_to`；
5. 为推断关系保存来源、置信度、证据数量和示例证据；
6. 可选调用外部语义提供器补充或覆盖概念。

默认策略刻意保守。错误的少量候选通常比一个看似完整但不可解释的“毛线球图谱”更可用。

### L3：任务图

L3 记录工作意图和历史：

```text
Task
├── prompt
├── read → file
├── modified → file
├── tested → file
├── affects → concept
└── result
```

确定事实来自 Hook：

- 用户任务；
- 工具名称；
- 文件路径；
- Shell 命令摘要；
- 测试事件；
- Agent 最终输出；
- 会话起止。

任务到概念的 `affects` 关系，在任务结束前完成增量 L1/L2 更新后，根据触及文件自动推导。

## 五、查询路由

`agentnavi context "任务"` 的主要步骤：

1. 把任务拆成中英文检索词和中文 n-gram；
2. 检索 L2 概念、L1 文件元数据和 L3 历史任务；
3. 若命中文件，反查其所属概念；
4. 选择少量高相关概念；
5. 获取概念的直接文件；
6. 只展开受总上限约束的一跳邻居文件；
7. 返回紧凑文本给 Agent。

这里的上限很重要。图谱的目标是缩小搜索空间，而不是把整张图重新塞入上下文。

## 六、Hook 闭环

```text
SessionStart
  ├─ 自动注册项目
  ├─ 增量索引
  └─ 注入项目概览

UserPromptSubmit
  ├─ 创建任务
  ├─ 增量索引
  └─ 注入任务相关上下文

PostToolUse
  └─ 记录读取、修改、搜索、命令和测试

Stop
  ├─ 再次增量索引
  ├─ 任务→概念归因
  ├─ 保存结果
  └─ 关闭任务

SessionEnd
  └─ 快速收尾会话
```

Hook 采用 fail-open。SQLite 或扫描错误会写到标准错误，但不会阻断 Agent。

## 七、Obsidian 投影

SQLite 是当前结构化图谱存储。导出器把它生成到：

```text
<Vault>/AgentNavi/
├── 首页.md
├── Projects/
├── Concepts/
└── Tasks/
```

生成页包含：

- Obsidian Wiki Link；
- 概念关系；
- 文件 `file://` 链接；
- 任务事件时间线；
- 置信度与来源；
- 自动生成声明。

生成目录可以整体删除重建。当前不支持从 Obsidian 回写。

## 八、可重建性边界

理想状态：

```text
Repository + Git + Agent Events
              ↓
         L1 / L2 / L3
```

当前版本中：

- L1：完全可由项目重建；
- L2：完全可由 L1 与外部提供器重建；
- L3：任务和事件保存在同一 SQLite 数据库，尚未另行追加写入独立事件日志。

因此严格意义上的“删除整个数据库后恢复 L3”还没有完成。路线图中的事件日志和重放器将补齐这一点。

## 九、跨项目扩展方向

项目解耦之后，可以自然形成个人或组织级 Work Graph：

```text
Flowit Write ─┐
Decision OS ──┼→ Agent Context Architecture
AI-Native ────┘
```

未来 Context Service 可以在多项目之间识别共同概念，由 Dispatcher 决定把任务交给哪个 Agent、附带哪些概念和文件。
