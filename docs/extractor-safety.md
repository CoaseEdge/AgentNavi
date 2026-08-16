# 提取器安全预算与确定性关系

本页记录文件提取器在扫描仓库时必须遵守的资源边界，以及本地依赖无法唯一解析时的处理原则。

## 默认预算

`ExtractionContext` 向内置与第三方提取器统一提供以下预算：

| 预算 | 默认值 | 作用 |
|---|---:|---|
| `max_binary_file_bytes` | 256 MiB | 超过后只登记文件、格式、角色和诊断，不进入科学或压缩格式的内部解析 |
| `max_archive_entries` | 10,000 | 限制 XLSX、NPZ 等 ZIP 容器的项目数量 |
| `max_archive_uncompressed_bytes` | 64 MiB | 限制元数据与内部结构的累计解压读取量 |
| `max_line_chars` | 1 MiB | 限制 CSV、TSV、JSONL、NDJSON 的单个物理行 |
| `max_stream_chars` | 64 MiB | 限制流式结构化文本一次扫描的累计字符数 |

预算触发采用 fail-open：文件节点仍然保留，角色和格式仍可查询，同时在 `extractor_warnings` 中说明未完成的解析范围。提取器不得因为单个异常文件中断整个项目扫描。

## 确定性关系

自动图谱只保存具有唯一目标的文件关系：

- Go import 对应唯一项目文件时，建立 `imports`；
- Go import 指向由多个 `.go` 文件组成的本地 package 时，不任意选择字典序第一个文件，而是记录候选数量和诊断；
- XLSX 通过 `xl/_rels/workbook.xml.rels` 将物理 worksheet XML 映射回逻辑工作表，再建立 `工作表 → formula_depends_on → 工作表`；
- 资源关系必须填写真实 `source_key`。只有确实以整个文件为来源的关系才允许 `source_key=None`。

这条规则的目的不是追求最多的边，而是避免把不确定性伪装成确定事实。漏掉一条低置信度边可以后续补充；写入一条错误边会污染 L2 聚合、影响范围分析和 Agent 上下文路由。

## 科学格式的元数据优先原则

可选科学依赖只用于读取结构和元数据：

- Parquet 读取 Schema、Row Group 和行数元数据；
- Arrow/Feather 读取 IPC Schema，不把整张表载入内存；
- HDF5 与 NetCDF 在达到资源上限后停止遍历；
- FITS 从 header 推导 shape，不访问 `hdu.data`；
- NPY/NPZ 只读取受限 header，NPZ 同时限制容器项目数与累计 header 字节。

第三方提取器应采用相同原则，并在返回结果前自行执行 `ExtractionContext` 中的预算。
