# 开源 NL2SQL / 语义层项目调研

> 本文是 **数语（Shuyu）** 项目的技术选型记录。  
> 调研日期：**2026-10-17**  
目标：为「自然语言快速挖掘 PostgreSQL / MySQL 数据并生成常见图表报告」选择一个可本地部署、可二次开发的开源底座。  
参考思路：Ontology / 语义层先行，把业务概念、同义词、跨表关系、指标口径和业务规则映射到数据库，再生成 SQL。

---

## 1. 评估维度

| 维度 | 权重 | 说明 |
|---|---:|---|
| 本体 / 语义层能力 | 30% | 是否支持实体、关系、指标、维度、同义词、业务规则、示例 |
| 数据库支持 | 15% | PostgreSQL / MySQL 是否原生可用，Windows 是否可部署 |
| 图表与报告 | 20% | 是否内置图表能力，能否形成分析报告 |
| 可扩展性 | 20% | 语言/架构是否适合二次开发，是否提供 SDK/API |
| 活跃度与生态 | 10% | 最近提交、release、issue、文档质量 |
| 部署与维护成本 | 5% | 依赖数量、是否必须 Docker、技术栈复杂度 |

---

## 2. 候选项目

### 2.1 WrenAI（Canner/WrenAI）

- **Stars**：17,883（2026-10-17）
- **最近提交**：2026-10-16，`wren-v0.16.0`
- **技术栈**：Python SDK/CLI + Rust 语义引擎（Apache DataFusion）+ Next.js（历史版本）
- **许可**：核心 Apache-2.0；商业版提供行/列权限、托管 GenBI UI 等
- **数据库**：PostgreSQL、MySQL、BigQuery、Snowflake、ClickHouse、Redshift、Databricks、DuckDB、Trino、SQL Server、Oracle、Spark、Athena 等 20+
- **语义层**：MDL（Modeling Definition Language），支持 models、columns、relationships、views、cubes、business rules、NL→SQL examples
- **图表**：开源内核提供 GenBI 能力（agent 构建浏览器端 dashboard），但**开源仓库当前不包含聊天 UI**；旧版 Docker Chat BI 在 `legacy/v1` 分支，已停止维护
- **二次开发体验**：Python SDK 易嵌入；Windows 有 `wren-core-py` 预编译 wheel；`wrenai[postgres,mysql]` 安装顺利
- **结论**：语义层最成熟、最贴合本体思路，适合作为底座；需要自建 UI 和 NL2SQL 编排层

### 2.2 Vanna（vanna-ai/vanna）

- **Stars**：23,816
- **最近提交**：2026-02-02；仓库已归档
- **技术栈**：Python，RAG（DDL/文档/SQL 示例训练）+ LLM
- **许可**：MIT
- **数据库**：PostgreSQL、MySQL、Snowflake、BigQuery 等
- **语义层**：无严格语义层；通过训练数据增强 schema 上下文
- **图表**：Plotly 图表能力
- **结论**：上手最快，但已归档，缺少指标/关系约束，不适合长期二次开发

### 2.3 SuperSonic（tencentmusic/supersonic）

- **Stars**：5,133
- **最近提交**：2026-10-15
- **技术栈**：Java / Spring Boot；Headless BI 语义层 + Chat BI
- **许可**：开源
- **数据库**：MySQL、PostgreSQL、ClickHouse、Doris、StarRocks 等
- **语义层**：指标/维度/标签/关系，支持规则+LLM 语义解析
- **图表**：内置 Chat BI 图表和看板
- **结论**：开箱即用程度最高，但 Java 技术栈重、二次开发门槛高；release 较旧（v0.9.8，2024-11）

### 2.4 DB-GPT（eosphoros-ai/DB-GPT）

- **Stars**：20,156
- **最近提交**：2026-10-11
- **技术栈**：Python，Agent 平台，多模型、多数据源
- **许可**：MIT
- **数据库**：PostgreSQL、MySQL 等
- **语义层**：Agent/知识库能力，非核心语义层
- **图表**：应用内置
- **结论**：平台级能力完整，但体量大（仓库约 477MB）、依赖重，适合作为独立产品而非轻量底座

### 2.5 Chat2DB（OtterMind/Chat2DB）

- **Stars**：28,321
- **最近提交**：2026-10-16
- **技术栈**：Java + 前端
- **定位**：跨平台数据库客户端/SQL 工作台，支持 AI 生成/解释/优化 SQL
- **结论**：强在 DB Client，不是“自然语言 → 图表报告”的分析工作台

### 2.6 Cube（cube-js/cube）

- **Stars**：21,023
- **最近提交**：2026-10-17
- **定位**：开源语义层，面向 AI/BI/嵌入式分析
- **结论**：语义层能力强，但不提供 NL2SQL 聊天 UI 和报告工作台

### 2.7 Dataherald

- **Stars**：3,646
- **最近提交**：2024-07-24
- **结论**：维护停滞，不选

---

## 3. 评分矩阵

评分 1–5，加权总分满分 5。

| 项目 | 语义层 30% | 数据库 15% | 图表报告 20% | 可扩展 20% | 活跃度 10% | 部署 5% | 加权 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **WrenAI** | **5** | **5** | 3 | **5** | **5** | 4 | **4.65** |
| SuperSonic | 4 | 5 | **5** | 2 | 3 | 3 | 3.95 |
| DB-GPT | 3 | 5 | 4 | 3 | 4 | 2 | 3.55 |
| Vanna | 2 | 5 | 3 | 4 | 1 | **5** | 2.95 |
| Chat2DB | 1 | 5 | 3 | 2 | 4 | 4 | 2.55 |
| Cube | 4 | 5 | 1 | 4 | 5 | 3 | 3.55 |
| Dataherald | 2 | 4 | 1 | 3 | 1 | 5 | 2.55 |

> 说明：WrenAI 的图表报告分较低，是因为开源版不含聊天 UI；这恰好由本项目的二次开发补齐。

---

## 4. 关键发现

1. **语义层是 NL2SQL 准确率的分水岭**  
   只把 DDL 塞给 LLM，模型容易选错表、用错字段、漏 JOIN。WrenAI 的 MDL 把模型、关系、指标、视图和规则变成可校验的上下文，符合参考材料中的 Ontology 思路。

2. **WrenAI 的开源边界在 2026 年发生变化**  
   2026-05-07 起 Wren Engine 合并进主仓库，开源版定位为“GenBI engine for AI agents”：MDL 语义层、governed text-to-SQL、MCP server、CLI、22+ connectors 为 Apache-2.0；聊天/托管 GenBI UI 属于商业版。旧版 Docker Chat BI 保留在 `legacy/v1`，不再维护。  
   → 因此选择 WrenAI 作为**引擎**，自建本地 UI/NL2SQL/图表层。

3. **Vanna 虽然星数最高，但已归档**  
   归档意味着不再修复安全问题、不再适配新模型/数据库，不适合作为长期底座。

4. **SuperSonic 是最接近“开箱即用”的替代方案**  
   如果团队以 Java 为主、希望直接获得 Chat BI + 语义层 + 图表，SuperSonic 更省事；但二次开发成本和定制灵活性不如 Python + WrenAI。

5. **Windows 可用性**  
   `wren-core-py 0.8.1` 提供 `cp311-abi3-win_amd64` wheel；`mysqlclient 2.3.0` 提供 Windows wheel。实测 PostgreSQL/MySQL 均可连接、编译 MDL、执行 SQL。

6. **数据湖覆盖是 WrenAI 的额外优势**  
   WrenAI 的 connector 层原生覆盖 Trino、Databricks、Athena、DuckDB、ClickHouse、BigQuery、Spark 等数据湖/Lakehouse 入口。Trino 可统一访问 Iceberg / Delta Lake / Hudi / Hive；Databricks 对应 Delta Lake；Athena 对应 S3 + Glue；DuckDB 可用于本地 Parquet 湖仓。这让我们可以在同一套 MDL 语义层上扩展到数据湖场景，而不是另起炉灶。

---

## 5. 最终决策

选择 **WrenAI** 作为底座，打造 **数语（Shuyu）**，理由：

- 语义层/本体表达力最强，直接支撑“业务概念 → 字段/关系/指标”的映射；
- Apache-2.0 核心，工程实现成熟（Rust + DataFusion）；
- PostgreSQL/MySQL 原生支持，Windows 可本地运行；
- Python SDK 易于二次开发；
- 开源版缺少的聊天 UI/图表/报告，正好由本项目补齐，形成完整闭环。

**不选 Vanna**：已归档、缺少指标/关系约束。  
**不选 SuperSonic**：Java 技术栈重，二次开发成本高。  
**不选 DB-GPT**：平台过大，依赖重。  
**不选 Chat2DB/Cube**：定位不匹配端到端 NL→图表报告。

---

## 6. 二次开发映射

| 需求 | WrenAI 提供 | 本项目补齐 |
|---|---|---|
| 业务概念/同义词 | MDL model/column descriptions | 自动本体 + 同义词 + glossary |
| 跨表关系 | relationships | 外键 + `_id` 启发式推断 |
| 指标口径 | cubes / calculated fields | metrics（expression/filters/unit） |
| 业务规则 | knowledge/rules | rules + 提示词注入 + 指标对齐校验 |
| NL→SQL | governed text-to-SQL | 提示词编排、修复循环、只读/白名单校验 |
| SQL 执行 | WrenEngine + dry-plan | 行数限制、错误回传、Provider 回退 |
| 图表 | GenBI（商业 UI） | ECharts 推荐与渲染（6 种图） |
| 报告 | GenBI（商业） | 报告保存 + 独立 HTML 导出 |
| 数据源 | 20+ connectors | 动态连接表单；PostgreSQL / MySQL / Oracle / SQL Server；Trino / Databricks / Athena / DuckDB / ClickHouse / BigQuery / Spark；Chroma / Qdrant / Weaviate 向量库独立适配 |
| 数据溯源 | Wren 物理 SQL / MDL | 来源映射、字段级血缘、可靠性检查、新鲜度、报告导出 |
| 本地数据湖演示 | DuckDB connector | Parquet + DuckDB 视图 + 端到端溯源验证 |

---

## 7. 风险与后续

- WrenAI 版本迭代较快（0.16.0），升级时需回归 MDL 格式与 CLI/SDK API。
- 自动本体是启发式草稿，复杂业务需要人工维护 `ontology.yml`。
- WrenAI 商业版功能（行列权限、托管 GenBI）不在开源范围；如有企业需求需评估商业授权。
- 当前 UI 无鉴权，仅适合本地/内网；生产化需补充登录、RBAC、审计、密钥管理。
- 如需多数据源，可继续接入 WrenAI 的 BigQuery/Snowflake/ClickHouse 等 connector。

---

## 8. 参考资料

- WrenAI GitHub：https://github.com/Canner/WrenAI
- WrenAI 文档：https://docs.getwren.ai
- WrenAI 开源边界：https://www.getwren.ai/en/open-core
- Vanna GitHub：https://github.com/vanna-ai/vanna
- SuperSonic GitHub：https://github.com/tencentmusic/supersonic
- DB-GPT GitHub：https://github.com/eosphoros-ai/DB-GPT
- Chat2DB GitHub：https://github.com/OtterMind/Chat2DB
- Cube GitHub：https://github.com/cube-js/cube
- Apache DataFusion：https://datafusion.apache.org
- ECharts：https://echarts.apache.org
