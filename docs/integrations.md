# AgentNavi 集成与 Presentation Policy

AgentNavi 的通用 Agent 集成遵循 `docs/vla.md` 的 Presentation Policy。集成层可以把当前任务转为 Context、Impact、History 或 Semantic Review 请求，但不能把 AgentNavi 当作编排状态后端，也不能绕过 Core 的候选边界。

## 推荐调用流程

1. 用 `agentnavi_context` 缩小任务相关的概念与文件集合。
2. 用 `agentnavi_visualize` 展示对应 view；无 MCP Apps 时读取 `content` 文本 fallback。
3. 修改前用 `agentnavi_impact` 检查调用方、依赖、测试和风险。
4. 需要项目演进时调用 `agentnavi_history`。
5. 语义候选必须通过 `agentnavi_semantic_review` 展示，Accept/Reject 只由 App 调用 `agentnavi_review_decide`。

## 安全与降级

VLA UI/MCP Projection 不得绕过 AgentNavi Core，直接访问 SQLite、`events.jsonl`、`semantic-overlays.jsonl`、项目根目录或 localhost；这些由 AgentNavi Server 处理。执行任务的 Agent 仍可通过 Host 提供的正常文件工具，读取和修改 AgentNavi 返回的真实项目相对路径。输入和 Hook payload 都是不可信数据。Host 不支持 MCP Apps 时保留文本说明，不伪造视觉验收，不因索引失败阻断主 Agent 工作。

## 可用资产

- `integrations/vla/SKILL.md`：通用 Agent Skill。
- `integrations/context-first/SKILL.md`：在陌生仓库中先定位 Context 的本地工作流。
- `integrations/deepseek-harness/`：DeepSeek Harness 适配器与合同测试。
