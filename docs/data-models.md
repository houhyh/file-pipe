# 数据模型与状态

所有模型定义于 `office_synth.schemas`，使用 Pydantic `extra=forbid`。Record 包含 schema_version=1.0、稳定 id、UTC created_at、input_refs 与配置指纹。可迁移数据使用运行目录内相对路径；绝对路径只用于本机执行配置。

| 模型 | 关键关联与用途 |
|---|---|
| CandidateSeed | 六维 taxonomy 组合、兼容结果与采样依据；不携带猜测的流程 |
| SourceRecord | 原始/最终 URL、机构、时间、HTTP、MIME、原始哈希、正文哈希、路径、许可状态及失败原因 |
| EvidenceRecord | source_id、限长逐字片段、原文位置、主张、用途、提取方法、定位所针对的 raw hash |
| SeedBlueprint | candidate_id、流程/规则的逐条证据引用、输入材料、决策、文件角色、参数、不变量、缺口、provenance、family_key |
| WorldInstance | seed_id、随机种子、虚构实体关系、交易、规则、事件、事实及共同来源分组 |
| Fact | fact_id、类型、值、单位、半开生效区间；派生事实另存输入及计算方法 |
| FilePlan | 相对路径、格式、角色、日期、版本、生效范围、fact_ids、模板、结构与依赖 |
| FileDependency | references、derived_from、summarizes、supersedes；只有中间两类构成生成 DAG |
| ValidationResult | scope、check_id、blocking/warning、pass/fail/skipped、expected/observed、定位与修复提示 |
| StageRecord | 阶段/对象、输入指纹、状态、尝试数、时间、输出、provider/model、请求及 token、错误分类 |

Seed 状态：candidate、needs_evidence、accepted、rejected。证据不足保留 needs_evidence，不靠模板或随机事实补足真实证据。

阶段状态：pending、running、succeeded、failed、blocked、skipped。进程中断后 running 记录不能用于缓存命中，恢复重新执行该对象。下载、解析错误保存在来源记录；模型 schema 错误、文件生成失败及预算错误也保存在阶段记录。

Fact 的业务金额以十进制字符串序列化。规则时间区间为 `[valid_from, valid_to)`；空 valid_to 表示没有终止时间。付款条件变更针对指定未付发票保留版本链，历史文件不会被新条款覆盖。

IR 存于 private/ir，包含由程序绑定的事实、相关记录路径与实际 XLSX 派生哈希；验证仍读取原生文件，不把 IR 自身当作文件内容正确的证明。

export manifest 1.0 的必需顶层字段是 schema_version、workspace_id、group_key、provenance、quality_tier、files。files 中每项含 file_id、path、format、sha256、version、dependencies。消费者应只按 manifest 指定的 public 路径取文件，group_key 用于共同来源划分。
