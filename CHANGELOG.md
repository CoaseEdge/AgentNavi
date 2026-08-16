# 更新记录

## 0.2.0 — 2026-08-17

首个 GitHub Release。AgentNavi 已从基础三层图谱原型演进为可接入 Codex、Claude Code 与 DeepSeek Harness 的本地项目上下文导航引擎。

### L3 完整重放

- 新增 `~/.agentnavi/events.jsonl` 追加式权威事件日志；
- 会话、任务和工具事件改为日志优先、SQLite 事务后写；
- 新增日志校验、旧数据回填、项目过滤、严格模式和幂等重放；
- 支持数据库完全丢失后恢复任务、事件、会话、文件关系和受影响概念；
- 会话异常结束可重放为 interrupted；
- 项目移除时清理应用标记，避免错误跳过历史。

### 基准测试

- 新增 full-scan、filename-search、AgentNavi 三种可重复检索基准；
- 记录候选数、必要文件召回、精度、文件体积和透明 Token 估算；
- 新增真实 Agent baseline / AgentNavi 对照记录；
- 从 L3 自动提取实际读取和触及文件；
- 只有双方召回率至少 95%，且真实对照双方成功时，才计算正式节省；
- 内置 observed benchmark 测试夹具验证：10,000 → 2,500 探索 Token，降幅 75%，双方必要文件召回率 100%；该数据用于测试 reduction 计算，不代表生产环境固定节省比例。

### L2 人工校正

- 新增语义候选审查和稳定 review ID；
- 支持接受、拒绝、创建、重命名、别名、合并、添加关系和文件映射；
- 人工 Overlay 在每次自动 L2 重建后重新应用；
- 新增 `semantic-overlays.jsonl` 权威校正日志、校验、回填和重放；
- 接受/拒绝及相反校正互斥，后写决定覆盖前写；
- 重命名等单值槽位不再累积冲突规则；
- 删除数据库后，项目重新关联并扫描即可恢复人工决定。

### 文件与数据格式

- 引入统一提取器注册表；
- 扩展 Go、Rust、Java、Kotlin、C、C++、C#、Ruby、PHP、Swift、Shell、Lua 等代码格式；
- 扩展 JSON、YAML、TOML、INI、XML、CSV、TSV、SQL、Jupyter Notebook、XLSX；
- 支持 NPY、NPZ、SQLite，并可选支持 Parquet、Arrow、HDF5、NetCDF、MAT、FITS；
- 增加二进制、压缩包、单行和流式扫描预算；
- 科学数据格式采用元数据优先和有界遍历；
- 修复 XLSX 工作表级公式来源关系与 Go 多文件本地包歧义。

### DeepSeek Harness

- 新增 Local Provider，通过 Harness `ctx.subprocess` 调用本机 AgentNavi；
- 新增 `agentnavi_context`、`agentnavi_impact`、`agentnavi_history`、`agentnavi_scan` 四个工具；
- 新增 `agent/pre-step` 自动上下文注入；
- 新增 `session/event` 到 AgentNavi L3 的事件桥；
- 映射 completed、failed、cancelled、interrupted 等任务状态；
- 同一 Harness 会话中的 L3 写入按顺序串行化，并过滤 AgentNavi 自身工具事件。

### 文档、品牌与开源准备

- README 重构为完整项目主页，增加产品封面、动态徽章、三层架构说明、DeepSeek Harness 使用方式与 benchmark 数据边界；
- 增加 README SVG 与许可证一致性回归测试；
- 项目许可证从 MIT 切换为 **Apache License 2.0**；
- 新增 DeepSeek Harness 独立集成文档与安全边界说明。

### 文档与测试

- 新增 L3 重放、基准测试、人工校正、提取器安全预算和 DeepSeek Harness 文档；
- 新增恢复、质量门槛、持久化、冲突替换、文件格式、Harness 事件桥和 README 资产测试；
- Python 3.11 / 3.12 / 3.13 与 DeepSeek Harness Node.js 集成均纳入 GitHub Actions；
- 数据库 schema 更新至 3。

## 0.1.0 — 2026-08-14

- 建立外置项目注册表和 SQLite 图谱存储；
- 实现 L1 物理图、L2 语义图和 L3 任务图；
- 实现增量扫描和基础多语言文件发现；
- 实现 Codex / Claude Code 生命周期 Hook；
- 实现上下文、影响和历史查询；
- 实现 Obsidian 可重建投影；
- 实现外部语义提供器协议；
- 实现全局 Hook / Skill 安装器；
- 增加自动化测试和 GitHub Actions。
