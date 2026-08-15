# AgentNavi 架构说明

## 一、问题定义

大型项目中的 Agent 成本，往往主要来自修改前的探索：

```text
列目录 → 搜关键词 → 读文件 → 发现依赖 → 再搜索 → 再读取
```

AgentNavi 把问题定义为：

> 给定任务 T，怎样在不漏掉必要文件的前提下，降低“哪些文件与 T 有关”的不确定性？

它是外部上下文索引和路由层，不是自动编程 Agent。

## 二、总架构

```text
真实项目仓库
    │
    ├── 文件系统 / Git / 语法
    └── Agent Hook 事件
              │
              ▼
┌─────────────────────────────────────┐
│ AgentNavi Context Engine            │
│                                     │
│ L1 物理图：文件、导入、引用、测试       │
│ L2 语义图：概念、关系、概念到文件        │
│ L3 任务图：目标、事件、修改、测试、结果   │
│                                     │
│ Query / Benchmark / Replay / Overlay│
└──────────────┬───────────────┬──────┘
               │               │
               ▼               ▼
       Codex / Claude        Obsidian
       紧凑上下文              人类投影
```

## 三、事实、派生状态和人工判断

0.2 以后必须区分三类数据：

```text
仓库事实
  文件、语法依赖、Git

L3 事实日志
  用户任务、工具事件、会话、结果

人工语义事实
  接受、拒绝、重命名、合并、映射

派生状态
  SQLite 查询表、L1/L2/L3 图、Obsidian 页面、基准汇总
```

外部权威日志：

```text
~/.agentnavi/events.jsonl
~/.agentnavi/semantic-overlays.jsonl
```

SQLite 负责高效查询，不再承担 L3 历史和人工判断的唯一副本。

## 四、三层图谱

### L1：物理图

确定性程序维护：

- Git 感知文件发现；
- Python AST 导入；
- JavaScript / TypeScript import、export、require；
- Markdown 链接和 Wiki Link；
- 常见测试文件关系；
- 增量文件状态。

### L2：语义图

自动层先按路径、标题、符号、文档和 L1 依赖形成保守概念；随后叠加外部语义提供器；最后叠加人工 Overlay。

```text
自动推断
    ↓
外部提供器
    ↓
人工 Overlay（最高优先级）
    ↓
最终查询视图
```

每次扫描会重建自动 L2，但不会删除外部校正日志。拒绝规则会持续压制错误推断；接受、重命名、合并和映射会在每次重建后恢复。

### L3：任务图

Hook 和手工任务命令先写 JSONL，再物化：

```text
Task
├── prompt
├── read / modified / tested → File
├── affects → Concept
└── result / status
```

`event_id` 保证幂等重放；任务关闭日志包含概念快照，使 L2 尚未扫描时也能恢复历史语义指向。

## 五、查询路由

`agentnavi context "任务"`：

1. 提取中英文词和中文 n-gram；
2. 检索概念、文件元数据和历史任务；
3. 文件命中反查概念；
4. 选择少量概念；
5. 获取直接文件；
6. 只展开受上限约束的一跳邻居；
7. 返回紧凑上下文。

上限是架构要求：图谱的目标是缩小搜索空间，不是把整张图塞回上下文。

## 六、可靠写入与重放

### L3

```text
append events.jsonl + fsync
    ↓
SQLite transaction
    ├── tasks / sessions / events
    ├── L3 nodes / edges
    └── applied_log_events
```

### 人工 Overlay

```text
append semantic-overlays.jsonl + fsync
    ↓
SQLite transaction
    ├── semantic_overlays
    └── applied_overlay_events
```

日志成功但数据库失败时，下一次 replay 补回；数据库事务成功时，应用标记与业务状态同时提交。

## 七、基准架构

```text
Benchmark Case
  task + expected_files
          │
   ┌──────┼──────────┐
   ▼      ▼          ▼
full   filename   AgentNavi
scan   search     context
   └──────┼──────────┘
          ▼
候选数、召回率、体积估算
```

真实 Agent 对照从 L3 读取实际读过的文件，再显式录入 Token、耗时和 success。比较器设置召回率和成功状态门槛，避免以任务质量换取虚假节省。

## 八、外置旁车

项目注册表只保存：

```text
project_id → project_root
```

项目仓库无需加入 AgentNavi 文件。删除 SQLite 不影响项目运行；L1/L2 可扫描恢复，L3 和人工 Overlay 可从外部日志恢复。

## 九、Obsidian

Obsidian 读取生成的 Markdown：

```text
<Vault>/AgentNavi/
├── 首页.md
├── Projects/
├── Concepts/
└── Tasks/
```

它是单向投影。Agent 和 Obsidian 都是 Context Engine 的消费者。

## 十、下一阶段

- schema migration 与重命名身份继承；
- 更深语言、API、数据库和符号解析；
- 图形化语义审查；
- HTTP/MCP Context Service；
- 权限隔离和 Dispatcher；
- 跨项目 Work Graph。
