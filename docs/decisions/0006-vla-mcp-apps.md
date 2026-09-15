# ADR-0006：采用稳定 VLA 协议连接 Core 与 MCP Apps

- 状态：已接受
- 日期：2026-09-14

## 背景

AgentNavi 需要把项目概览、架构、流程、任务上下文、影响、历史和语义审查同时提供给模型与人类。若 UI 直接读取 SQLite、项目目录或事件日志，Presentation 会成为第二套查询引擎，并可能泄漏绝对路径、原始数据库行或未经筛选的事件。若模型文本完全由 UI JSON 或 HTML 反向生成，无 UI 的 Host 又无法获得等价结果。

## 决策

1. 协议版本固定为 `agentnavi.vla.v1`，统一 Envelope 包含 `schemaVersion`、`view`、`project`、`sourceState`、`data` 和 `warnings`；
2. 分层固定为 `Core DTO → Presentation Adapter → AgentNaviView → MCP Apps`；每个 Adapter 必须按 view allowlist 逐字段构造输出，不得透传数据库 Row、事件 payload 或 Core mapping；
3. 模型使用的 `content` 与 UI 使用的 `structuredContent` 语义一致，但由 Adapter 独立生成；
4. 公共 DTO 为 `EntityRef`、`GraphEdge`、`Evidence`、`Warning`、`Error`、`Project` 和 `SourceState`；Project 显式携带 kind，GraphEdge 显式携带稳定 id，实体、边和证据保留 `layer`、`source`、`confidence` 与 evidence；
5. UI 只消费 MCP 返回的 `structuredContent`，不得访问 SQLite、项目文件、权威日志、`localhost` 或公网；
6. 协议输出递归限制为严格 JSON-safe 值，所有路径必须是 POSIX 项目相对路径；所有 wire key 与字符串 value 都执行绝对路径 canary，并显式拒绝已知敏感字段；这些检查仅是 defense-in-depth，不声称通用 denylist 能完整证明语义隐私；
7. `Error` 供 MCP 错误映射使用，不加入成功 Envelope；
8. 协议 DTO 只使用 Python 标准库，不导入 MCP SDK。MCP Server 通过 optional extra 和 lazy import 在后续实现；
9. 新增可选 extension 字段必须向后兼容，且不能以大小写或 camelCase、snake_case、kebab-case 变体覆盖保留字段；删除、改名、改类型或改变语义时升级 `schemaVersion`；
10. JSON object 的 key 使用确定性排序；数组保留 Adapter 给出的业务顺序，协议层不把 key 排序误用为数组排序。
11. 每个 view 的 Adapter 必须有合同测试，证明原始 SQLite Row、未筛选 event 和完整源文件正文无法到达 wire。

## 原因

- Core 与 Presentation 可以分别测试和演进；
- Claude 等支持 MCP Apps 的 Host 可以展示交互视图，不支持 UI 的 Host 仍能使用等价文本；
- 单一筛选边界降低内部路径和原始数据泄漏风险；
- 明确的版本与 DTO 合同使后续工具、HTML resource 和多 Host 配置可以独立开发；
- 基础安装无需 MCP 依赖，保留当前零依赖核心。

## 后果

优点：

- UI 不需要也不能理解 AgentNavi 数据库 schema；
- 文本 fallback 与可视化可以分别针对消费者优化；
- 所有 view 共享来源、证据、warning、路径 canary 和基础安全规则，同时由各自 Adapter allowlist 承担语义隐私责任；
- 合同测试能够在 MCP Server 和 App 之前发现兼容性回归。

代价：

- Adapter 需要分别生成文本与结构化数据；
- 新字段必须维护兼容性和稳定排序；
- defense-in-depth 检查可能把命中 canary 的内部脏数据变成明确错误，需要 Server 映射为可操作提示；
- denylist 不能识别所有业务语义，新增 view 时必须同步实现 allowlist 和不可达性合同测试；
- UI 无法自行补查数据库，所需信息必须由工具合同显式提供。

## 不采用的方案

### UI 直接读取 SQLite 或项目文件

这会破坏旁车边界、增加权限与路径泄漏风险，并使远程 Host 行为不一致。

### 只返回 HTML

模型无法稳定理解界面状态，不支持 MCP Apps 的 Host 也失去等价 fallback。

### 只返回一份 JSON，再由 UI 生成模型文本

模型文本会依赖 UI runtime，无法针对上下文预算独立筛选和表达。

### 在 Core 中直接依赖 MCP SDK

这会让基础安装承担非必要依赖，并把传输协议耦合到项目图谱核心。
