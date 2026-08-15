# 语义审查与人工校正

## 一、为什么自动 L2 会腐化

L2 不是纯事实。路径、标题、代码依赖和模型都可能误判：

```text
A import B
```

只能确定文件依赖，不一定代表长期业务关系。若所有自动推断都永久累积，图谱会逐渐成为“毛线球”。

AgentNavi 采用两层结构：

```text
自动 L2：可删除、可重建
人工 Overlay：权威判断、独立保存、每次重建后重新叠加
```

人的判断写入：

```text
~/.agentnavi/semantic-overlays.jsonl
```

SQLite 中的 `semantic_overlays` 只是它的物化视图。

## 二、审查候选

```bash
agentnavi semantic review list
```

候选包括：

- 自动生成的概念；
- 概念之间的关系。

每项带有：

- 稳定 `review_id`；
- 来源；
- 置信度；
- 主体、关系和对象；
- 已有人工决定。

查看已审查项：

```bash
agentnavi semantic review list --all
```

## 三、接受和拒绝

```bash
agentnavi semantic review accept <review_id>
agentnavi semantic review reject <review_id> --note "说明原因"
```

接受与拒绝是互斥决定。后一次决定会替换前一次；被拒绝的边即使下次自动扫描再次推断出来，也会在 Overlay 阶段删除。

拒绝后仍可以用 `--all` 找到原 review ID，再改为接受。

## 四、人工校正动作

### 创建概念

```bash
agentnavi semantic correction add create_concept agent-runtime \
  --label "Agent 运行时"
```

### 重命名概念

```bash
agentnavi semantic correction add rename_concept membership \
  --label "会员与升级"
```

同一概念只保留一个重命名槽位；再次执行会更新最终名称，不会叠出多条冲突规则。

### 添加别名

```bash
agentnavi semantic correction add add_alias membership \
  --alias "订阅会员"
```

### 合并概念

```bash
agentnavi semantic correction add merge_concept subscription \
  --object membership
```

合并会把 L2 关系和历史任务的 `affects` 关系转向目标概念，并把来源概念标记为 inactive。

### 添加或否定概念关系

```bash
agentnavi semantic correction add add_edge membership \
  --relation depends_on \
  --object payment

agentnavi semantic correction add reject_edge membership \
  --relation depends_on \
  --object distribution
```

`add_edge`、`accept_edge` 和 `reject_edge` 对同一三元组互斥，后写决定生效。

### 映射或排除文件

```bash
agentnavi semantic correction add map_file membership \
  --relation implemented_by \
  --object src/membership/upgrade.py

agentnavi semantic correction add unmap_file membership \
  --relation implemented_by \
  --object src/legacy/membership.py
```

## 五、列出、删除和重新应用

```bash
agentnavi semantic correction list
agentnavi semantic correction remove <correction_id>
agentnavi semantic correction apply
```

删除也是日志事件，因此数据库重建后不会让已删除规则复活。

## 六、校正日志恢复

验证：

```bash
agentnavi semantic log verify
```

升级前已有 SQLite 校正时回填：

```bash
agentnavi semantic log backfill
```

从头重建校正表：

```bash
agentnavi semantic log replay --reset --strict
agentnavi scan
```

项目重新关联并扫描时，AgentNavi 会先增量重放该项目尚未物化的人工校正，再生成自动 L2 并叠加 Overlay。

## 七、不会立即解决的问题

人工 Overlay 能防止已知错误反复出现，但不等于图谱自动变成完美本体。后续仍需要：

- 更好的候选分组与批量审查；
- 关系证据查看；
- 概念拆分；
- 置信度校准和陈旧关系衰减；
- 图形化审查工作台；
- 团队级审批与审计。
