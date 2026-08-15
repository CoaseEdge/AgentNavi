# 文件提取器与格式支持

## 设计原则

AgentNavi 通过统一提取器注册表扩展文件类型，而不是继续在 `scanning.py` 中增加后缀分支：

```text
发现文件
  → 构造 ExtractionContext
  → 注册表选择提取器
  → 合并 ExtractionResult
  → 写入 L1 文件、内部资源和确定性关系
  → 根据文件角色构建 L2
  → 最后叠加人工 Semantic Overlay
```

提取器只能返回结构化结果，不能直接访问数据库或修改项目文件。外部 Python 包可通过 `agentnavi.extractors` Entry Point 注册插件。

## 结构化文本与数据

| 格式 | 当前能力 |
|---|---|
| JSON | 根类型、顶层键、GeoJSON 概览、项目文件引用 |
| JSONL / NDJSON | 记录数、无效记录、常见字段、记录类型、流式有界扫描 |
| YAML | 浅层键和显式路径引用；不解释 anchor、merge key、自定义 tag |
| TOML | section 和项目文件引用 |
| INI / CFG / CONF | section、键和项目文件引用 |
| XML | 根元素、元素计数、href/src/include 等引用 |
| CSV / TSV | 分隔符、列、行数下限和抽样类型；支持有界流式扫描 |
| SQL | 读取表、写入表和数据库表资源 |
| Jupyter Notebook | 单元格、Kernel、Python/JS import、Markdown 链接 |
| XLSX | 工作表、跨表公式关系、外链部件数量；使用标准库 ZIP/XML |

大型 CSV、TSV、JSONL 和 NDJSON 不必全部装入内存；最多扫描 100000 条记录并明确标记截断。

## 更多编程语言

内置保守启发式提取器支持：

- Go：module import、本地 package、type、function；
- Rust：mod、use、extern crate、function、struct、enum、trait；
- Java / Kotlin：package、import、class、interface、enum、object、function；
- C / C++：include、class、struct、enum、保守函数识别；
- C#：using、namespace、class、interface、record、enum；
- Ruby、PHP、Swift、Shell、Lua。

只有唯一可解析的本地目标才建立 L1 文件关系；不确定依赖保留为 external dependency。该能力用于上下文导航，不替代编译器、LSP 或 Tree-sitter。

## 数据和科学文件

无需第三方运行时依赖：

| 格式 | 当前能力 |
|---|---|
| NPY | 版本、dtype、shape、Fortran order |
| NPZ | 内部 NPY 数组清单、dtype、shape |
| SQLite / SQLite3 / DB | 只读 immutable 打开，提取表、视图、索引、触发器、字段 |

可选增强：

| 格式 | 可选依赖 | 当前能力 |
|---|---|---|
| Parquet | pyarrow | Schema、列、行数、Row Group |
| Arrow / Feather | pyarrow | Schema、列、行数 |
| HDF5 | h5py | Group、Dataset、shape、dtype、压缩方式 |
| NetCDF | netCDF4 | 维度、变量、shape、单位、属性 |
| MATLAB MAT | scipy | 变量名、shape、MATLAB class |
| FITS | astropy | HDU、shape、HDU 类型 |

安装全部可选科学依赖：

```bash
python -m pip install -r requirements-science.txt
```

缺少可选依赖时，文件仍会进入 L1，并记录格式、角色和明确诊断，不阻断项目扫描。

## 文件内部资源

提取器可以创建有上限的内部资源节点，例如：

```text
model.xlsx#sheet:Forecast
analysis.ipynb#cell:12
science.sqlite#table:samples
arrays.npz#array:x.npy
main.go#symbol:function:main
```

文件到资源使用 `contains`。内部资源还可形成 `formula_depends_on`、`reads`、`writes`、`attached_to` 等关系。每个文件最多写入 5000 个资源和 10000 条资源关系，避免图谱爆炸；资源间关系不会自动聚合为跨概念 L2 边。

## L2 角色映射

| 文件角色 | L2 关系 |
|---|---|
| source_code | implemented_by |
| test | tested_by |
| document | documented_by |
| configuration / manifest | configured_by |
| dataset / scientific_data / database | data_provided_by |
| notebook / analysis | analyzed_by |

人工 Semantic Overlay 在最后应用，优先级高于自动角色解释。

## 增量与安全

文件节点保存提取器 ID、版本、注册表签名、角色和警告。提取器新增或升级后，即使文件内容没有变化，也会重新提取。

安全边界：

- 提取器不能修改项目文件；
- SQLite 以只读 immutable 模式打开；
- XLSX 单个 ZIP 项目最多读取 8 MiB，并检查异常压缩率；
- NPY header 限制为 1 MiB；
- 大型集合和内部资源均有上限；
- 可选科学库异常只产生 warning，不中断项目扫描。

## 当前限制

- YAML 是浅层确定性解析；
- 多语言代码支持是保守启发式；
- XLSX 不建立单元格级图；
- `.db` 不一定是 SQLite，非 SQLite 文件会留下诊断；
- 当前尚未提供统一全文内容分片索引；
- 第三方提取器后续仍需增加独立进程、CPU、内存和输出预算。
