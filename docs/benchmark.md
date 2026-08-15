# 基准测试

## 一、需要证明的不是“图更漂亮”

AgentNavi 的价值应由任务指标验证：

- 首次有效修改前读取多少文件；
- 探索阶段使用多少 Token；
- 无关文件读取比例；
- 必要文件召回率；
- 任务是否成功；
- 完成耗时；
- 索引开销是否低于节省。

因此基准分为检索基准与真实 Agent 对照。

## 二、检索基准

用例格式：

```json
[
  {
    "id": "membership-upgrade",
    "task": "修改会员升级和支付逻辑，并运行对应测试",
    "expected_files": [
      "src/membership/upgrade.py",
      "src/payment/service.py",
      "tests/test_upgrade.py"
    ]
  }
]
```

运行：

```bash
agentnavi benchmark evaluate cases.json --suite membership-v1
```

默认比较：

- `full-scan`：把全部索引文件视为候选；
- `filename-search`：只按任务词匹配文件名和路径；
- `agentnavi`：按任务→概念→文件路由。

关键指标：

- `recall`：必要文件有多少进入候选集；
- `precision`：候选中有多少是必要文件；
- `candidate_reduction`：候选文件数相对全仓库减少多少；
- `estimated_context_tokens`：候选文件体积除以可配置字符/Token 比例；
- `estimated_token_reduction`：相对全仓库的透明估算。

该 Token 指标是**可重复的文件体积近似值**，不是供应商账单，也未假装包含模型内部缓存、工具协议和系统提示开销。

## 三、真实 Agent 对照

先分别运行一个不使用 AgentNavi 的 baseline 任务，以及一个使用 AgentNavi 的任务。Hook 会留下两条 L3 任务记录。

然后录入供应商或运行时提供的实际探索 Token、耗时和结果：

```bash
agentnavi benchmark record <baseline_task_id> \
  --suite membership-real \
  --case membership-upgrade \
  --mode baseline \
  --expected src/membership/upgrade.py \
  --expected src/payment/service.py \
  --expected tests/test_upgrade.py \
  --exploration-tokens 10800 \
  --duration-ms 130000 \
  --status success

agentnavi benchmark record <agentnavi_task_id> \
  --suite membership-real \
  --case membership-upgrade \
  --mode agentnavi \
  --expected src/membership/upgrade.py \
  --expected src/payment/service.py \
  --expected tests/test_upgrade.py \
  --exploration-tokens 3200 \
  --duration-ms 72000 \
  --status success
```

L3 事件自动给出：

- 实际读过的文件；
- 实际触及的文件；
- 必要文件召回；
- 无关读取数与比例。

Token、耗时和成功状态由调用者显式录入，因为不同 Agent 对 Token 统计口径不同，AgentNavi 不伪造这些数据。

## 四、比较与质量门槛

```bash
agentnavi benchmark compare --suite membership-real --kind observed
```

只有质量合格的配对才计入正式 reduction：

```text
baseline 必要文件召回 ≥ 95%
AgentNavi 必要文件召回 ≥ 95%
真实对照双方 success = true
```

低召回、失败或未知状态会显示为 `excluded_for_low_quality`。这避免以下错误结论：

> AgentNavi 少用了 80% Token，因为它没有读到真正需要修改的文件。

## 五、推荐实验设计

至少准备 20 个真实任务，并固定：

- 同一代码版本；
- 同一 Agent 和模型；
- 同一权限；
- 同一任务说明；
- 同一验收标准；
- baseline 与 AgentNavi 交替执行，降低顺序偏差。

必要文件应由任务完成后的人工审查或独立 Reviewer 确认，而不是由 AgentNavi 自己定义。

建议同时记录：

- 测试是否通过；
- Reviewer 是否接受；
- 漏读文件；
- 错误图谱关系；
- 初次索引和增量索引耗时。

## 六、结果解释

检索基准证明的是：

> 图谱能否在保持必要文件召回的前提下缩小候选集合。

真实对照证明的是：

> 候选缩小是否真的转化为更少 Token、更少无关读取和更短时间，同时任务质量不下降。

两者都成立，才能较有把握地说 AgentNavi 对该类项目有效。
