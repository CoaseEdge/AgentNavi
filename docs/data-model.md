# 数据模型

AgentNavi 使用 SQLite 作为查询投影，并用两个 append-only JSONL 保存不可轻易丢弃的事实。

## 一、外部事实日志

### `events.jsonl`

保存 L3：

- 会话建立和结束；
- 任务创建和关闭；
- 工具事件；
- 项目快照；
- 受影响概念快照。

### `semantic-overlays.jsonl`

保存人工 L2 判断：

- correction upsert；
- correction remove；
- 项目快照。

SQLite 删除后，项目重新关联并扫描即可重新物化人工校正；L3 通过 `replay l3` 重建。

## 二、`projects`

项目注册表：`id`、`name`、`root`、`kind`、扫描时间。

## 三、`nodes` 与 `edges`

三层统一图模型。

节点稳定 ID：

```text
project_id + layer + kind + key
```

边稳定 ID：

```text
project_id + layer + source + relation + target
```

L1 常见关系：`imports`、`references`、`tests`。

L2 常见关系：`contains`、`implemented_by`、`tested_by`、`documented_by`、`configured_by`、`depends_on`、`related_to`、`merged_into`。

L3 常见关系：`read`、`modified`、`tested`、`searched`、`affects`。

### `l2_concept_edges`

schema v4 增加的有界查询投影，只保存真实 source/target 都是 L2 `concept` 的 edge ID、端点与记录顺序。它不按 relation 名分类，因此人工 Overlay 和外部提供器可合法使用与 concept→file 映射同名的关系。

`edges` 插入或身份字段更新、`nodes` 的 `project_id/layer/kind` 更新时，SQLite trigger 会同步该投影。升级自 v3 或投影表缺失时，初始化从 `nodes` 与 `edges` 重建；健康的 v4 数据库重复启动不会重写投影。该表属于可删除、可恢复的派生状态，不是新的事实来源。

## 四、`file_state`

保存文件 mtime、大小、摘要和更新时间，用于增量扫描。

## 五、L3 物化表

### `sessions`

外部会话 ID、Agent、活动任务、开始和结束时间。

### `tasks`

原始提示、标题、Agent、状态、摘要、起止时间。

### `events`

工具事件、文件路径、压缩数据和时间。一个带多个文件路径的日志事件会物化为多行查询事件。

### `applied_log_events`

保存已应用的 L3 `event_id`，实现幂等重放。

## 六、人工语义物化表

### `semantic_overlays`

字段包括：

- `action`；
- `subject_key`；
- `relation`；
- `object_key`；
- `value_json`；
- `note`；
- `enabled`。

它不是唯一副本；权威副本位于 `semantic-overlays.jsonl`。

### `applied_overlay_events`

保存已经物化的人工校正日志事件 ID。

## 七、`benchmark_runs`

保存：

- suite、case、run kind、mode；
- 任务文本和 task ID；
- 必要文件与候选文件；
- 召回、精度、无关读取等指标；
- 实际 Token、输出 Token、耗时和 success。

比较器只使用同一 case / kind / mode 的最新记录，并对质量合格的配对计算 reduction。

## 八、事实与解释

必须区分：

```text
事实：文件存在、A import B、工具修改文件、测试退出码、人的明确拒绝
解释：A 属于某概念、概念之间代表业务依赖、任务动机摘要
```

自动解释保留 `source`、`confidence` 和 evidence。人工 Overlay 的最终结果使用 `source=human-overlay`、`confidence=1.0`，但仍可通过日志审计是谁做了什么校正及其 note。
