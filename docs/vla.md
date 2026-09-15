# AgentNavi VLA 协议

AgentNavi VLA（Visual Layer for Agents）把同一份项目认知同时提供给模型和可视化界面。它只负责呈现已有的 L1/L2/L3 事实与解释，不改变项目仓库，也不把界面变成新的事实来源。

当前协议版本固定为 `agentnavi.vla.v1`。

## 一、分层边界

```text
AgentNavi Core DTO
        ↓
Presentation Adapter
        ↓
AgentNaviView
        ├── content：面向模型的独立文本
        └── structuredContent：面向 MCP Apps 的 VLA JSON
                                      ↓
                               MCP Apps HTML
```

- Core 负责查询项目图谱并返回领域数据，不依赖 MCP 或 UI。
- Adapter 负责筛选、排序、截断和解释，将 Core 数据转换为 `AgentNaviView`。每个 view 必须声明允许输出的字段 allowlist，不能透传 Core mapping、数据库 Row 或事件 payload。
- `AgentNaviView` 是稳定的 JSON 输出边界。`content` 与 `structuredContent` 表达相同语义，但必须独立生成，不能从 HTML 或彼此反向解析。
- MCP Apps 只消费 `structuredContent`。UI 不得访问 SQLite、项目文件、日志、`localhost` 或公网。
- Obsidian 与 MCP Apps 都是投影视图，不是运行事实来源。

本阶段只冻结 DTO 与 wire contract，不实现 MCP Server。

## 二、统一 Envelope

所有成功视图都使用以下顶层字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schemaVersion` | string | 固定为 `agentnavi.vla.v1` |
| `view` | string | 受支持的视图名 |
| `project` | object | 公开项目身份，不包含项目根目录 |
| `sourceState` | object | 本次结果对应的索引状态 |
| `data` | object | 当前视图的数据 |
| `warnings` | array | 非致命、可展示的诊断 |

示例：

```json
{
  "schemaVersion": "agentnavi.vla.v1",
  "view": "context",
  "project": {
    "id": "agentnavi",
    "name": "AgentNavi",
    "kind": "software"
  },
  "sourceState": {
    "status": "ready",
    "revision": "scan-42",
    "indexedAt": "2026-09-14T09:30:00Z"
  },
  "data": {
    "entities": []
  },
  "warnings": []
}
```

`data` 的具体结构由每个 view 的合同定义。它必须是 JSON object，不能直接暴露数据库 Row 或内部对象。

## 三、支持的 View

`view` 只能取以下值：

| View | 作用 |
|---|---|
| `repo-overview` | 项目目的、存在原因和核心模块概览 |
| `repo-tour` | 面向陌生使用者的项目导览 |
| `architecture` | 分层、模块职责和关键依赖 |
| `flow` | 有证据支持的主要执行流程 |
| `context` | 当前任务的候选文件、原因、证据与阅读顺序 |
| `impact` | 修改对象的直接和间接影响 |
| `history` | 历史任务、时间线与项目故事 |
| `semantic-review` | 待人工判断的 L2 语义关系 |

新增一种完全不同的 view 属于协议能力扩展；改变既有 view 或公共 DTO 的字段语义属于破坏性变更。

## 四、公共 DTO

### `Project`

仅包含可公开的 `id`、`name` 和 `kind`。禁止加入 `root`、workspace path、数据库路径或日志路径。

### `SourceState`

包含：

- `status`：只能是 `ready`、`partial` 或 `stale`；
- `revision`：可选的稳定索引修订标识；
- `indexedAt`：可选、以 `Z` 结尾的 RFC 3339 UTC 时间文本。

它描述来源状态，不承载内部文件系统位置。

### `Evidence`

包含 `kind`、`summary`、`layer`、`source`、`confidence`，并可带 `path`、`lineStart` 和 `lineEnd`。证据只保存定位与短摘要，不保存完整源文件正文。

### `EntityRef`

包含 `id`、`kind`、`label`、`layer`、`source`、`confidence`、`evidence`，文件型实体可带 `path`。

### `GraphEdge`

包含稳定 `id`、`sourceId`、`targetId`、`relation`、`layer`、`source`、`confidence` 和 `evidence`。每条新关系必须至少包含一个 Evidence。方向固定为 `sourceId → targetId`，不得依赖 UI 布局猜测关系方向。

### `Warning`

包含稳定 `code`、面向用户的 `message` 和可选 `evidence`。Warning 不代表工具调用失败，结果仍放在成功 Envelope 中。

### `Error`

包含稳定 `code`、面向用户的 `message`、`retryable` 和筛选后的 `details`。它供后续 MCP 错误映射使用，不添加到成功 Envelope 顶层。

## 五、来源与可信度

实体、边和证据不能抹平数据性质：

- `layer` 必须是 `L1`、`L2` 或 `L3`；
- `source` 说明数据来自仓库扫描、语义启发式、外部提供器、人工 Overlay 或事件事实；
- `confidence` 必须是 0 到 1 的有限数值；
- `evidence` 提供可追溯的依据。

L1 事实不能伪装成 L2/L3 解释，自动推断也不能省略置信度或伪装成人工决定。

## 六、路径与隐私

所有进入 VLA 的项目路径必须是规范的 POSIX 相对路径，例如 `src/agentnavi/query.py`。协议层以 defense-in-depth 方式拒绝：

- `/Users/...` 等 POSIX 绝对路径；
- Windows drive path 和 UNC path；
- `file://` URI；
- `..`、`~`、反斜杠和非规范的 `./` 路径。

MCP 输出不得包含：

- `project.root` 或 workspace 根目录；
- SQLite、数据库或权威日志路径；
- 原始 SQLite Row；
- 未筛选的事件 payload；
- 完整源文件正文。

`data`、`details`、`extensions` 和嵌套 DTO 都执行相同的递归 JSON-safe 与显式 canary 检查。绝对路径检测覆盖所有 wire key 和字符串 value，不只检查名为 `path` 的字段，因此说明文本、错误详情和 path-index key 也会经过检查。只允许字符串字段名、JSON scalar、array 和 object；拒绝 `NaN`、Infinity、`Path`、bytes 和任意 Python 对象。

这些检查只是 defense-in-depth，不能完整证明语义隐私。通用 denylist 无法判断任意业务字段是否实际包含原始 Row、未筛选事件或正文；后续每个 Adapter 都必须：

1. 按对应 view 的字段 allowlist 逐项构造 DTO，不使用 `dict(row)` 或同类透传；
2. 只复制完成展示所需的已筛选事件字段与短证据摘要；
3. 用 view 级合同测试证明原始 SQLite Row、未经筛选的 event payload 和完整正文无法到达 wire；
4. 把新的敏感形态同时加入 Adapter 测试，并在适用时增加协议 canary。

## 七、序列化与兼容策略

`to_dict()` 输出公共 wire 字段；`to_json()` 使用 UTF-8 可读字符、稳定 key 排序、紧凑分隔符并拒绝非有限浮点值。数组顺序保留业务含义，Adapter 必须在截断前自行完成稳定排序，协议层不会擅自重排数组。

兼容规则：

1. 既有字段不得删除、改名或改变含义；
2. 可选字段只能增量新增，消费者必须忽略不认识的 extension 字段；
3. extension 不得覆盖任何当前或可选的保留字段；比较时忽略大小写及 camelCase、snake_case、kebab-case 的分隔差异；
4. 增加 enum 值前要确认旧消费者具有安全降级行为；
5. 删除字段、改变类型、改变方向或改变既有语义时，必须升级 `schemaVersion`；
6. `schemaVersion` 变化必须新增迁移说明和跨版本合同测试。

## 八、后续 MCP 工具边界

Reasoning tools 固定为：

- `agentnavi_context`
- `agentnavi_impact`
- `agentnavi_history`
- `agentnavi_semantic_review`

Presentation 统一使用 `agentnavi_visualize`。唯一写操作为 App 可见的 `agentnavi_review_decide`。这些工具由后续阶段实现；本协议模块不会导入 MCP SDK，基础安装继续保持零 MCP 依赖。

## 九、Presentation Policy

Agent 在需要理解仓库、评估影响或解释历史时，先调用与任务对应的 reasoning tool，再把结果交给 `agentnavi_visualize`。Presentation tool 只负责把已筛选的结果展示给人，不替代 reasoning，也不扩大候选集合。

### 选择顺序

1. 新任务或陌生仓库：`agentnavi_context`，必要时补充 `repo-overview`、`repo-tour`、`architecture`、`flow`。
2. 修改前评估：`agentnavi_impact`。
3. 需要了解项目演进：`agentnavi_history`。
4. 发现自动语义关系需要人判断：`agentnavi_semantic_review`，决定只能由 App 调用 `agentnavi_review_decide`。

### 交互边界

- VLA UI/MCP Projection 展示 Why、Evidence、Next Step 和允许动作；不得绕过 AgentNavi Core 直接访问项目根目录、SQLite、事实日志或 localhost。
- 执行任务的 Agent 可以通过 Host 正常文件工具读取和修改 AgentNavi 返回的真实项目相对路径；UI/Projection 不代替这些文件操作。
- `content` 是模型可读的独立 fallback；`structuredContent` 是 UI 合同，二者语义一致但分别生成。
- Evidence 不足时显示 warning 或省略不可验证项，不用模型补齐事实。
- 人工决定后只重放 Overlay 并刷新当前视图，不触发全仓扫描。
- Host 不支持 MCP Apps 时使用文本 fallback；不能把 fallback 描述成视觉交互已经发生。

### 验收清单

- 请求是否先经过 reasoning tool，且候选数量和一跳限制未放宽？
- 每个可见主张是否能追溯到 Evidence，路径是否为 POSIX 相对路径？
- UI 是否把不可信输入作为文本节点渲染，无 `innerHTML`、`eval`、外联或 localhost 请求？
- 需要人工确认的动作是否只有 App 可见，且决定已经写入事实日志并可重放？
