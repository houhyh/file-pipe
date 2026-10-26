# 办公文件合成管线：CLI 实施任务说明书

版本：v1.0；日期：2026-10-26。

## 0. 给执行 CLI 的指令

请根据本说明书，在当前代码仓库中实现一套可运行、可恢复、可扩展的办公文件合成管线。先阅读仓库中的 AGENTS.md、README、依赖配置和相关模块，确认现有检索、LLM、文件解析、文件生成、日志与 CLI 基础设施，再实现代码。优先复用现有能力，避免平行造轮子；如果当前目录不是仓库，则创建独立 Python 项目。

本任务要求实际实现并验证，不要仅输出设计方案。先给出简短实施计划，再按阶段完成。常规实现选择自行决定；凭据、网络、依赖不可用时记录具体阻塞，继续完成不依赖该阻塞的部分。不得把 mock 检索或 fixture 运行声称为真实联网运行。

本说明书中的模块路径与命令名称是建议接口；如果仓库存在成熟约定，可等价适配，并在 README 中给出对应关系。不要大范围重构已有业务模块，不覆盖用户已有文件与配置，不自动发布数据集。

## 1. 目标与边界

目标：从角色、场景、工作需求等维度出发，检索公开的工作知识和原生文件，建立有证据支撑的 seed 蓝图池；将 seed 实例化为具体业务事实、规则与事件，生成多格式且相互关联的办公文件集合，验证后形成工作区池。

主要链路：

覆盖维度 → 合理组合 → 检索与采集 → 来源材料池 → seed 蓝图池 → 业务实例 → 文件计划与依赖 → 文件生成 → 验证与修复 → 工作区池。

首版必须实现：

- 配置驱动的维度定义、兼容组合采样与覆盖统计。
- 真实搜索适配器接口、一个可用实现，以及用户提供 URL 的采集入口。
- 来源下载、正文/文件解析、证据保存、去重与缓存。
- 基于证据构建 seed，执行结构与证据校验。
- 虚构业务模式下的共享事实、规则、事件与多实例生成。
- 文件计划与有向依赖；生成 DOCX、XLSX、PPTX、PDF，并支持 MD/EML。
- 结构、事实、计算、引用、版本与可选渲染验证；局部修复。
- CLI、阶段持久化、失败报告、预算控制、离线测试和端到端示例。

首版不要求：全网规模爬取、Common Crawl 分布式挖掘、完整公司经营模拟、GUI、模型训练、任务指令与 grader 合成、自动下载受限内容。为扩展预留接口即可，不要做空壳占位后宣称已完成。

本次只构建文件/工作区合成模块。已有任务合成链路通过稳定 export 接口消费产物；不要修改其行为。

## 2. 核心原则

1. 区分角色/场景等维度词条、候选组合、证据充分的 seed、具体业务实例与最终文件。
2. 公开来源提供工作流程、规则结构、模板和事实证据；每条关键主张必须可追溯。
3. 首版以虚构业务模式为主。真实机构材料的规则或结构可以参考，但不得把不同机构的真实交易拼成同一公司的事实。
4. 关键实体、数值、日期、公式和事件由统一事实层控制。金额使用 Decimal 或整数最小货币单位，不用二进制浮点计算业务金额。
5. 不让 LLM 独立编造每份文件的金额、规则和业务关系。LLM 主要负责提炼、规划与受约束的文字表达。
6. 文件依赖先规划、后生成；派生材料应读取已生成的上游文件，再与事实层核验。
7. 保留历史差异的生效范围，不把所有跨文件差异都当成错误。
8. 不把换名称/日期的实例当作新的业务类型；分别统计业务、内容、结构与实例多样性。

## 3. 建议架构

按职责组织模块，实际路径遵循仓库规范：

| 模块 | 职责 |
|---|---|
| schemas | 数据模型、版本号与跨对象校验 |
| taxonomy | 维度取值、兼容规则、采样与覆盖 |
| retrieval | 查询计划、搜索适配器、候选排序、补检索 |
| acquisition | HTTP 下载、缓存、类型识别、来源记录 |
| parsing | 网页与原生文件解析、稳定证据定位 |
| seeds | 证据提炼、蓝图生成、入池与去重 |
| world | 实体、交易、规则、事件和确定性计算 |
| planning | 文件清单、事实分布、依赖与版本计划 |
| generation | 结构化内容、模板及各格式渲染器 |
| validation | 文件、计算、事实、业务、视觉检查 |
| repair | 按失败类型局部修复、下游失效处理 |
| storage | 内容缓存、对象存储、阶段状态与原子写入 |
| orchestration | 阶段依赖、并发、恢复、预算 |
| export | 工作区、内部元数据和质量报告输出 |
| cli | 阶段命令和全链路执行入口 |

建议使用 Python、Pydantic、YAML 配置；复用现有 LLM 客户端和文件工具。若需新增库，保持依赖最小并明确可选依赖。不要要求启动数据库服务才能运行首版。

## 4. 输入配置

提供 taxonomy.yaml、pipeline.yaml、providers.example.yaml 和一个小规模演示配置。模型凭据从环境变量读取，不写入代码、日志或示例文件。

taxonomy 至少包含：domain、role、scenario、work_need、business_object、execution_pattern。每个取值有稳定 id、名称、定义、同义词和可选来源。

兼容规则支持 allow/deny 与条件约束；不要直接对全部维度做无约束笛卡尔积。采样结合维度配额、覆盖不足优先、组合去重和可选 LLM 合理性筛选。明确每轮候选上限，避免组合爆炸。

下面是配置结构示意，实施时补成可运行配置：

```yaml
schema_version: '1.0'
run:
  random_seed: 42
  output_dir: ./runs/demo
  mode: synthetic_world
sampling:
  candidate_limit: 30
  target_seeds: 3
  instances_per_seed: 2
retrieval:
  provider: configured_provider
  max_queries_per_candidate: 6
  max_results_per_query: 5
  max_sources_per_seed: 8
  max_gap_search_rounds: 2
generation:
  files_per_workspace: [5, 8]
  required_formats: [docx, xlsx, pptx, pdf]
  max_parallel_llm_calls: 2
  max_repair_attempts: 2
budget:
  max_llm_requests: 100
  max_search_requests: 100
  max_download_bytes: 104857600
  max_wall_seconds: 3600
```

参数是演示默认值，不是效果保证。支持用户下调规模。未配置价格表时不要编造美元成本，用请求数、token、字节和时间限制预算。

## 5. 数据模型与关联

所有对象带 schema_version、稳定 id、created_at、输入引用和配置指纹。路径用相对路径，不把运行机器绝对路径写成可迁移产物的唯一标识。

### 5.1 CandidateSeed

包含 domain_id、role_id、scenario_id、work_need_id、business_objects、execution_pattern、兼容检查结果、采样依据与状态。这里只是业务方向，不包含未经检索证实的流程细节。

### 5.2 SourceRecord 与 EvidenceRecord

SourceRecord 必须记录：原始 URL、最终 URL、来源机构、标题、来源类型、获取时间、状态码、声明/检测 MIME、字节数、原始文件哈希、解析内容哈希、本地路径、许可或使用条件状态、下载/解析状态。没有许可信息时标记 unknown，不推断为公有领域。

每条 EvidenceRecord 包含 source_id、有限长度原文片段、稳定位置、主张、证据用途、抽取方法。位置按格式使用页码、章节/段落、sheet+range、slide+shape 或 DOM/文本偏移，并记录定位针对哪个文件哈希。LLM 生成的主张与支持它的原文分开保存。

### 5.3 SeedBlueprint

包含：候选维度、业务目标、工作流步骤、输入材料需求、决策点、规则模式、文件角色、典型产物、可变参数、必须满足的不变量、证据引用、模板/原生资产引用、证据缺口、审核状态。

关键工作流/规则逐条引用 evidence_id；明确 evidence_supported、synthetic_choice、unverified 的区别。seed 有 candidate、needs_evidence、accepted、rejected 状态。缺关键证据时补检索或保持未接受，不强制凑数量。

### 5.4 WorldInstance

包含 seed_id、instance_seed、虚构实体、关系、交易、规则、事件、时间范围、事实台账、计算定义与结果。每条事实带 fact_id、类型、值、单位/币种、适用时间；派生事实记录输入 fact_id 和计算方法。

同时保存 business_invariants，例如金额平衡、引用存在、订单—收货—发票匹配、付款条件、生效时间与审批权限。首版只需为演示业务实现必要规则，不做通用 DSL 解释器。

### 5.5 FilePlan 与 FileDependency

FilePlan 字段：file_id、relative_path、format、role、author_entity_id、business_date、version_id、有效时间、fact_ids、upstream_file_ids、章节/表格/对象结构、模板引用、生成策略、校验项。

依赖类型至少区分 references、derived_from、summarizes、supersedes。生成顺序只基于实际生成依赖构建 DAG；互相引用不应强行造成生成死锁。路径和文件 ID 可以提前分配，再渲染正文中的相互引用。

### 5.6 ValidationResult 与 StageRecord

验证记录包含 scope、check_id、severity、status（pass/fail/skipped）、expected、observed、证据位置、失败分类、修复建议。severity 区分阻断和提示。

阶段记录包含输入指纹、状态、尝试次数、起止时间、输出引用、provider/model、请求计数、token（未知时 null）、错误类型。状态至少支持 pending/running/succeeded/failed/blocked/skipped。

## 6. 具体阶段实现

### A. 维度采样

实现人工 YAML 维度加载、兼容校验、确定性采样、维度边际覆盖与组合覆盖报告。允许导入已有维度表。首版不要求自动爬职业百科构建完整 taxonomy。

演示至少覆盖采购、财务、运营三个角色，并给出合理场景和需求。先用其中一个业务打通全链路，再扩展到另外两个。

### B. 检索与下载

SearchProvider 协议：search(query, filters, limit) → SearchHit[]，每条 hit 包含 URL、标题、摘要、提供商和查询信息。不要把某一模型的联网能力硬编码为唯一搜索方式。

必须支持：一个真实检索后端、配置提供商、用户提供 URL 列表，以及离线 fixture。无法获得搜索凭据时，真实后端实现并给出配置方法；URL 入口仍应可独立工作。

围绕候选组合生成 workflow、policy、artifact/template 三类查询；筛选后获取原文。搜索摘要只用于选源，不能单独支撑关键规则。允许按缺失材料/证据进行最多配置轮数的补检索。

下载保留来源上下文和原生文件；不能只存摘要。按站点限速、超时、有限重试、最大字节数和 MIME 控制。识别 HTML 登录页伪装为 Office 下载的情况；遇到受限页面记录失败，不绕过访问限制。

对外部 URL 只允许 HTTP(S)，默认拒绝本机、内网与云元数据地址，并在重定向后重新检查；如果需要企业内网源，应由显式配置放行。文件名与解压路径规范化，防止路径越界。不执行下载文件中的宏、脚本或指令。

来源用途标记为 real_fact、workflow_reference、policy_reference、template、background；真实规则应带适用范围，不能默认通用于所有虚构业务。

### C. 解析与 seed 入池

至少支持 HTML、PDF、DOCX、XLSX、PPTX、MD/TXT；旧格式可以明确拒绝或使用可选转换器，不静默当作成功。

从原文提炼工作流、材料需求、规则模式与文档结构；保存证据位置。首版 accepted seed 至少有两份非重复来源材料，核心工作流/规则有原文支撑；模板可以来自其中一份。可配置允许不同来源策略，但必须报告真实来源数量与缺口。

实现 seed 结构校验、证据引用存在性与抽样语义校验。证据无法支持时不通过。避免把相同来源多个 URL 算作独立证据。

### D. 实例化业务事实

同一 accepted seed 至少生成两个实例，实例差异包括实体、交易数据、时间或规则参数；不只替换文件名。

实现确定性实体 ID、交易生成、日期与金额计算。LLM 可以提出结构化业务设定，由程序验证后接受。固定世界输入和随机种子应重建相同事实与计划；在线 LLM 文本默认通过缓存重放，不承诺跨重新采样字节一致。

实现一个可验证的采购结算案例：采购订单、收货、发票、合同付款条件，生成对账数据和说明。具体字段可调整，但必须有真实计算依赖。

### E. 规划与生成

先生成 FilePlan 并检查事实覆盖、依赖完整性与格式分布，再生成实际文件。每个演示工作区至少五份文件，包含原始数据、规则和派生产物，四种主要格式均出现。

首版通过结构化内容 IR 加原生渲染器实现：

- DOCX：标题、段落、表格、页眉页脚与关键事实。
- XLSX：多 sheet、真实公式、数字格式、至少一个汇总或图表；明确重算方式与缓存策略。
- PPTX：多页、可编辑文本/表格或图表；不能整页截图冒充可编辑演示稿。
- PDF：从可靠内容源直接生成或从原生文件导出，记录派生关系。
- MD/EML：作为可选沟通/说明文件；EML 使用合法邮件结构。

不要要求所有文件都通过自由代码生成；优先可靠的渲染库与可配置模板。LLM 生成内容需结构校验与有限重试，修复后仍不合格则失败退出该对象。

派生产物至少有一条路径实际读取上游文件：例如读取生成 XLSX → 计算汇总 → 写入 DOCX/PPTX → 与事实台账比对。若输入解析失败，不允许改用台账绕过后仍标记该路径验证成功。

### F. 事件与版本

实现至少一种确定性事件，例如付款条件变更。保存旧规则、新规则、生效时间、补充协议/通知与受影响派生产物。验证按版本适用时间进行，旧版不得被直接覆盖。

首版允许只在一个示例工作区启用事件模式；不要求所有实例都有历史版本。报告是否启用及生成的版本链。

### G. 验证与局部修复

必须执行结构检查、事实一致性、金额计算、引用完整性、依赖有效性、日期/版本检查。XLSX 使用可信计算过程验证结果；若需 LibreOffice/Excel 重算而环境缺失，明确 skipped/blocked，禁止假称已经重算通过。

配置视觉验证：可用时将 DOCX/PPTX/PDF 渲染为页图，检查空白页、缺页、内容边界和抽样视觉质量；无渲染工具时保留结构/几何检查，单独标记视觉未验证。不能仅凭 ZIP 可打开就认定质量合格。

局部修复按失败分类执行：文字事实错误重写局部内容；排版错误重渲染；事实层错误修复事实后，使受影响下游生成与验证阶段失效。修复次数受配置控制，保留失败版本和诊断，不无限循环。

hard failure 不得进入 accepted 池；skipped 检查在报告中展示。accepted 需要满足配置声明的必需检查；另外用 quality_tier 表明 structural、semantic、rendered 等实际覆盖级别，避免混淆。

### H. 输出与去重

输出三个索引：sources、seeds、workspaces。每个工作区独立保存 public/（用户可见原生文件）和 private/（事实、计划、证据、验证与生成元数据）。export 默认只把 public 内容交给后续 agent。

同一事实的不同格式表达属于关联文件，不应作为重复删除。至少实现二进制哈希去重、规范化文本近重复检测，以及按 seed/实例/模板统计重复。近重复默认标记或聚类，不自动误删带版本意义的文件。

训练/验证/测试划分按 seed 家族、来源案例或模板组等共同来源分组，禁止将高度相似实例随机拆散后当成独立泛化测试；支持先不划分、只输出分组键。

## 7. CLI 接口

建议入口 office-synth（也可作为现有主 CLI 子命令）：

```bash
office-synth init --output ./configs/demo
office-synth sample --config ./configs/demo/pipeline.yaml
office-synth collect --config ./configs/demo/pipeline.yaml
office-synth collect --config ./configs/demo/pipeline.yaml --urls ./configs/demo/source_urls.jsonl
office-synth build-seeds --config ./configs/demo/pipeline.yaml
office-synth instantiate --config ./configs/demo/pipeline.yaml
office-synth plan --config ./configs/demo/pipeline.yaml
office-synth generate --config ./configs/demo/pipeline.yaml
office-synth validate --config ./configs/demo/pipeline.yaml
office-synth repair --config ./configs/demo/pipeline.yaml
office-synth export --config ./configs/demo/pipeline.yaml
office-synth run --config ./configs/demo/pipeline.yaml --resume
office-synth report --run-dir ./runs/demo
```

提供 --help、--dry-run、--limit、按 seed/workspace ID 筛选能力。dry-run 展示计划、阶段依赖与预算上限，不联网、不调用模型、不写入正式产物。

单独运行后续阶段时检查前置条件；缺前置数据返回清晰错误。--resume 根据输入/配置/版本指纹复用成功结果；--force 仅使目标及其下游失效，禁止清空整个输出目录。

预算耗尽要保存已完成结果，停止调度新请求并报告 blocked 原因；只允许可追踪的在途请求造成有限预算偏差。

## 8. 持久化与运行可靠性

可使用 JSON/JSONL 配合 SQLite 保存索引和阶段状态，选择仓库已有方案。要求原子写入、单对象锁或事务、同一 ID 幂等、内容寻址下载缓存、请求指纹缓存。

并行仅用于独立对象/阶段；同一工作区的依赖生成按拓扑调度。外部模型和来源内容不可信，不把抓取到的指令注入为系统权限。

恢复规则至少区分：下载失败、解析失败、证据不足、模型响应不合法、生成失败、验证失败、预算耗尽。默认不重试鉴权错误和确定性结构错误；网络暂态错误使用有上限的退避。日志记录状态与摘要，不输出密钥或带凭据的 URL。

## 9. 实施顺序与里程碑

### M1：离线确定性闭环

实现 schema、配置、事实层、文件规划、四种主要格式生成、确定性校验和 export。使用明确标为 fixture 的小型案例，无需网络和模型即可跑通。fixture 的生成来源、用途、限制写清楚。

### M2：检索到 seed

接入真实搜索/URL、下载、解析与证据提炼。建立 accepted seed，保留完整来源路径。没有真实凭据时仍交付适配器与 URL 路径，并报告尚未进行的集成验证。

### M3：seed 到实例工作区

接入 LLM 蓝图提炼、受约束规划/文字生成、缓存和多实例，补事件版本与下游修复，形成可恢复的完整 run 命令。

### M4：验证与示例扩展

完成断点恢复、故障注入、近重复与覆盖报告；扩到三个角色的小型演示。真实联网试跑以用户已有配置与显式预算为限，不自行进行大规模付费运行。

每个里程碑都应有可运行命令与实际产物，再进入下一阶段。不要先堆完整抽象层而没有端到端结果。

## 10. 验收标准

| 验收项 | 通过条件 |
|---|---|
| 离线闭环 | 无密钥、无外网时，fixture 模式生成至少两个工作区，每个至少五份文件，覆盖 DOCX/XLSX/PPTX/PDF |
| 真实采集 | 至少一个真实搜索后端可配置；URL 入口可获取、识别和解析公开网页/原生文件；保留来源与哈希 |
| seed 可信度 | accepted seed 核心工作流/规则有可定位原文证据；缺证据时拒绝或 needs_evidence |
| 实例差异 | 同 seed 两个实例在业务数据或参数上有实质差异，事实与计划可按随机种子复现 |
| 依赖执行 | 至少一条真实上游文件读取→计算→派生文档链，并与事实台账一致 |
| 故障检测 | 篡改金额、删除引用文件、引入无效版本或生成依赖环时，能准确报告对应错误 |
| 局部修复 | 修复上游导致相关下游失效重建，未受影响部分复用 |
| 断点恢复 | 人为中断后 resume 继续；已成功的缓存阶段不重复调用外部服务 |
| 事件演化 | 至少一例旧/新规则、生效时间和对应文件版本链有效 |
| 输出隔离 | public 不包含事实台账、验证答案和内部证据标注 |
| 质量声明 | 无外部渲染/重算工具时准确报告 skipped/blocked，不伪造通过 |
| 报告 | 输出阶段流失、失败分类、角色/场景/格式覆盖、重复、请求量与耗时 |

在线目标演示为三个 accepted seed、每个两个实例、每个五到八份文件，配置允许缩小；这是实际联网条件满足后的集成目标，不能用 fixture 数量冒充。若来源不足或预算耗尽，输出实际数量和缺口。

## 11. 测试与交付

测试集中验证高价值行为：金额/日期不变量、引用/依赖校验、证据定位、错误文件识别、模板事实注入、缓存失效、恢复幂等和导出隔离。外部服务使用 fixture 做离线测试，另提供显式 opt-in 的真实集成测试，不让常规测试消耗 API 预算。

必须交付：实现代码、依赖声明、示例配置、无密钥 fixture 示例、README、数据模型说明、来源/证据说明、运行报告和一组可检查的示例产物。生成数据默认放在配置指定目录并从 git 忽略，仓库只保留小型可复现 fixture。

最终回复应说明：修改了哪些模块、实际运行了哪些命令、产出路径、通过与未通过的检查、是否真实联网/调用模型、消耗情况，以及剩余阻塞。不得只给未来计划，不得把未运行检查写成已验证。

## 12. 最终完成检查表

- [ ] 实际代码可安装、CLI 可运行，说明书命令有对应实现。
- [ ] 完成离线端到端闭环，并人工检查代表性产物。
- [ ] 真实来源与合成事实有明确边界，每条关键证据可追溯。
- [ ] 文件包含真实内容、公式或可编辑对象，不是空模板。
- [ ] 实例由共享事实生成，派生链确实读取实际文件。
- [ ] 阶段失败、修复、缓存、恢复与预算控制可工作。
- [ ] accepted 状态与必需验证一致，未执行检查如实呈现。
- [ ] 后续任务合成可直接消费 public 工作区和稳定 export 元数据。
