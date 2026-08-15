# L3 事件日志与完整重放

## 一、为什么 SQLite 不足以承担唯一历史

SQLite 很适合查询，但如果任务、事件和图谱只存在数据库中，删除或损坏数据库就会丢失 L3。AgentNavi 0.2 把职责拆开：

```text
append-only JSONL = 权威事实
SQLite             = 可查询投影
L3 nodes / edges    = 派生关系
```

每次状态变化先写 `events.jsonl`，成功落盘后再在一个 SQLite 事务中更新任务、事件和图谱，并记录该日志事件已经应用。

因此可能出现的失败只有两类：

1. 日志写入失败：数据库不更新，调用直接失败；
2. 日志成功、数据库失败：日志仍在，下一次重放补回。

不会出现“数据库已经记录，但权威日志没有事实”的正常写入路径。

## 二、日志事件

当前事件类型：

- `session_upsert`
- `task_created`
- `task_event`
- `task_closed`
- `session_end`

每行包含：

```json
{
  "schema_version": 1,
  "event_id": "log_...",
  "event_type": "task_event",
  "created_at": "2026-08-15T12:00:00+00:00",
  "project": {
    "id": "flowit-write",
    "name": "flowit-write",
    "root": "/Users/me/Code/flowit-write",
    "kind": "software"
  },
  "payload": {}
}
```

项目快照使数据库完全丢失后仍能重建任务所属项目。任务关闭事件同时保存受影响概念的稳定 key 和 label；即使 L2 尚未重建，也能先恢复 `Task --affects--> Concept`。

## 三、验证

```bash
agentnavi event-log verify
```

检查：

- JSON 是否有效；
- schema 版本；
- event type；
- event ID；
- 项目快照；
- 重复 ID。

损坏行不会在非严格重放中阻断其他有效事件，但命令返回非零状态。

## 四、重放

增量补应用：

```bash
agentnavi replay l3
```

清空 L3 后从头重建：

```bash
agentnavi replay l3 --reset --strict
```

只重放一个项目：

```bash
agentnavi replay l3 --project flowit-write --reset
```

重放是幂等的。`applied_log_events` 保存已应用事件 ID；重复执行不会重复插入工具事件。使用 `--reset` 时会清空相应 L3 和应用标记后重建。

## 五、升级前历史回填

旧数据库已有任务、会话和事件，但没有 JSONL 时：

```bash
agentnavi event-log backfill
```

回填使用确定性事件 ID，重复执行不会反复追加同一批旧历史。建议升级后顺序执行：

```bash
agentnavi event-log backfill
agentnavi event-log verify
```

## 六、恢复演练

在备份环境中可以做完整演练：

```bash
cp ~/.agentnavi/agentnavi.db ~/.agentnavi/agentnavi.db.backup
rm ~/.agentnavi/agentnavi.db
agentnavi init
agentnavi replay l3 --reset --strict
agentnavi history
```

不要在没有备份的生产工作区直接删除数据库。事件日志默认 `fsync`，可以在配置中关闭以降低写入延迟，但会降低突然断电时的持久性保证。

## 七、隐私

日志可能包含：

- 用户任务文本；
- Agent 最终摘要；
- Shell 命令；
- 本地绝对项目路径；
- 文件相对路径。

应将 `~/.agentnavi` 视为敏感工作数据，不要同步到不可信位置。
