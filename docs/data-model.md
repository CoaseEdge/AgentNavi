# 数据模型

AgentNavi 当前使用 SQLite。数据库开启外键、WAL 和 busy timeout，以支持多个本地 Agent 进程短时并发写入。

## projects

项目注册表。

| 字段 | 含义 |
|---|---|
| `id` | 稳定项目标识 |
| `name` | 显示名称 |
| `root` | 项目绝对路径，唯一 |
| `kind` | `software`、`writing`、`video` 或 `generic` |
| `last_scan_at` | 最近扫描时间 |

## nodes

三层统一节点表。

| 字段 | 含义 |
|---|---|
| `layer` | 1、2 或 3 |
| `kind` | `file`、`project`、`concept`、`task` 等 |
| `key` | 项目内稳定键，如相对路径或概念键 |
| `label` | 人类可读名称 |
| `data_json` | 结构化派生信息 |
| `confidence` | 0 到 1 的置信度 |
| `source` | `filesystem`、`parser`、`semantic-heuristic`、`external-semantic-provider`、`task-events` 等 |

节点 ID 由：

```text
project_id + layer + kind + key
```

确定性生成。重复扫描不会制造新节点 ID。

## edges

统一关系表。

### L1 常见关系

- `imports`
- `references`
- `tests`

### L2 常见关系

- `contains`
- `implemented_by`
- `tested_by`
- `documented_by`
- `configured_by`
- `depends_on`
- `related_to`

### L3 常见关系

- `read`
- `modified`
- `tested`
- `searched`
- `affects`

边 ID 由：

```text
project_id + layer + source + relation + target
```

确定性生成。重复事件会在 `data_json.count` 中累计，而不是制造重复边。

## file_state

增量扫描缓存：

- 相对路径；
- mtime 纳秒值；
- 大小；
- SHA-256 摘要；
- 更新时间。

先用 mtime 与大小筛选，变化时再更新摘要和依赖。

## sessions

Agent 会话映射：

```text
agent + external_session_id → session_key
```

保存当前活动任务，用于把工具事件归入正确任务。

## tasks

L3 任务实体：

- 原始提示；
- 标题；
- Agent；
- 状态；
- 摘要；
- 起止时间；
- 会话键。

## events

追加式工具事件：

- 事件类型；
- 工具名称；
- 项目内相对路径；
- 压缩后的结构化数据；
- 时间。

当前事件表与图谱共用 SQLite。未来会增加独立 JSONL 事件日志，使 L3 也能在删除数据库后完整重放。

## 事实与解释

必须区分：

```text
事实：文件存在、A import B、工具修改了文件、测试退出码
解释：A 属于某业务概念、任务为何如此修改、某关系意味着业务依赖
```

AgentNavi 用 `source`、`confidence` 和 `data_json.evidence` 保存解释来源，避免把模型或规则推断伪装成确定事实。
