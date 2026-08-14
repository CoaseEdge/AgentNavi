# 外部语义提供器协议

## 目的

默认 L2 只依靠路径、结构元数据和 L1 依赖，优点是便宜、稳定、可解释，但无法完整理解业务语义。

外部语义提供器允许接入：

- 本地小模型；
- 企业内部模型网关；
- 任意云模型；
- 规则引擎；
- 人工维护的本体映射器。

AgentNavi 不绑定模型供应商。

## 启用

环境变量优先：

```bash
export AGENTNAVI_SEMANTIC_COMMAND="python /path/to/provider.py"
```

也可以写入 `~/.agentnavi/config.json`：

```json
{
  "semantic_command": "python /path/to/provider.py"
}
```

然后执行：

```bash
agentnavi scan --full
```

## 输入

命令从标准输入接收 UTF-8 JSON：

```json
{
  "schema_version": 1,
  "project": {
    "id": "flowit-write",
    "name": "Flowit Write",
    "root": "/Users/example/Code/flowit-write",
    "kind": "software"
  },
  "concepts": [
    {
      "key": "membership",
      "label": "会员系统",
      "data": {
        "active": true,
        "file_count": 8
      }
    }
  ],
  "physical_edges": [
    {
      "source": "src/membership/upgrade.ts",
      "relation": "imports",
      "target": "src/payment/client.ts"
    }
  ]
}
```

当前为控制输入规模，物理边最多发送 5000 条。后续版本会改为基于图差异的增量协议。

## 输出

标准输出必须是一个 JSON 对象：

```json
{
  "concepts": [
    {
      "key": "membership-upgrade",
      "label": "会员升级",
      "files": [
        "src/membership/upgrade.ts",
        "tests/membership/upgrade.test.ts"
      ],
      "confidence": 0.91,
      "data": {
        "reason": "文件共同实现升级资格、补差价和权益变更"
      }
    }
  ],
  "relations": [
    {
      "from": "membership-upgrade",
      "relation": "depends_on",
      "to": "payment",
      "confidence": 0.86,
      "evidence": [
        "src/membership/upgrade.ts imports src/payment/client.ts"
      ]
    }
  ]
}
```

## 字段约束

### concepts

| 字段 | 必需 | 含义 |
|---|---|---|
| `key` | 是 | 稳定、可 slug 化的概念键 |
| `label` | 否 | 人类可读名称 |
| `files` | 否 | 项目内相对路径数组 |
| `confidence` | 否 | 0 到 1，默认 0.7 |
| `data` | 否 | 提供器自定义证据或解释 |

不存在的文件会被忽略。

### relations

| 字段 | 必需 | 含义 |
|---|---|---|
| `from` | 是 | 源概念键 |
| `relation` | 否 | 默认 `related_to` |
| `to` | 是 | 目标概念键 |
| `confidence` | 否 | 0 到 1，默认 0.7 |
| `evidence` | 否 | 证据数组 |

源、目标概念必须已存在于默认图谱或本次提供器输出中。

## 错误处理

以下情况 AgentNavi 会忽略提供器结果，并保留内置 L2：

- 命令不存在；
- 超过 120 秒；
- 非零退出码；
- 标准输出为空；
- 输出不是合法 JSON 对象；
- 单项字段格式错误。

提供器失败不会阻断扫描。

## 最小示例

见 [`examples/semantic_provider.py`](../examples/semantic_provider.py)。

## 建议的模型提示结构

模型不应直接“自由创作知识图谱”，而应执行受约束抽取：

1. 只创建能由输入文件或依赖证实的概念；
2. 概念数量保持小于文件数量一个数量级；
3. 每个关系给出 evidence；
4. 不确定时降低 confidence，而不是补全故事；
5. 业务概念优先于通用技术词，如 `utils`、`common`；
6. 只输出 JSON，不输出解释文字。
