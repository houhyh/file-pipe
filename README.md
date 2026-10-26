# Office Synth · 办公文件合成管线

依据 [实施说明书](office_file_synthesis_cli_spec.md)实现的独立 Python CLI。输入角色与场景维度、公开参考来源，输出共享事实驱动的 DOCX、XLSX、PPTX、PDF 工作区。支持无密钥 fixture 演示、真实 URL 采集、Brave Search，以及配置化的 JSON LLM 接口。

当前业务生成器实现**采购结算 / 三单匹配**。采购、财务、运营是同一业务家族的三种角色视角；实例数量不等于业务类型数量。其他业务需要增加事实生成器、文件模板与不变量，不能仅替换 taxonomy 后宣称已支持。

## 安装与离线运行

```bash
python -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/office-synth --help
.venv/bin/office-synth run --config configs/demo/pipeline.yaml --dry-run
.venv/bin/office-synth run --config configs/demo/pipeline.yaml --resume
.venv/bin/office-synth report --run-dir runs/demo
.venv/bin/python -m pytest -q
```

基本演示生成一个 fixture seed、两个工作区，分别为八份和六份原生文件。启用事件的实例保留原合同、补充协议及两版对账说明。所有组织与交易均为虚构。

```bash
# 三角色、六工作区；启用可选 LibreOffice 重算及页图检查
.venv/bin/office-synth run --config configs/demo/roles.yaml --resume

# 在其他目录创建配置；存在同名配置时拒绝覆盖
.venv/bin/office-synth init --output configs/my-demo
```

配置中的相对路径以配置文件所在目录解析。使用环境现有 Python 3.13 完成开发；项目声明支持 Python 3.11 及以上。不要把运行目录设为项目源码目录。

## 真实采集与模型配置

```bash
# 三个公开参考 URL，包含 HTML 和 PDF；无需搜索/模型密钥
.venv/bin/office-synth run --config configs/demo/urls.yaml --resume

# 也可单独提交 JSONL URL，每行一个字符串或 {"url": "https://..."}
.venv/bin/office-synth sample --config configs/demo/urls.yaml
.venv/bin/office-synth collect --config configs/demo/urls.yaml --urls configs/demo/source_urls.jsonl
.venv/bin/office-synth build-seeds --config configs/demo/urls.yaml
```

URL 模式独立于搜索和 LLM。在线配置请设置 `run.fixture: false`；显式 `--urls` 也会关闭该次调用的 fixture 模式。来源证据只参考流程或规则结构；合成公司的付款天数、交易及事件都是明确的虚构选择，不把公开机构的政策当作通用规则。

`configs/demo/online.yaml` 提供搜索加模型的示例。设置 `BRAVE_SEARCH_API_KEY`、`OFFICE_SYNTH_LLM_KEY` 环境变量，并在 YAML 中配置实际可用的 `llm.base_url` 与 `llm.model`。不把密钥写入配置。搜索适配器依据 [Brave 官方接口](https://api-dashboard.search.brave.com/api-reference/web/search/post)，通过 `X-Subscription-Token` 认证；没有搜索密钥时使用 URL 模式。

LLM 接口需要支持 `/chat/completions`、JSON object 输出和 usage（可选）。它负责从候选证据中选择蓝图依据，并选择受约束的审核文字；金额、实体、日期、公式与文件依赖由程序控制。响应按模型、请求及 endpoint 指纹缓存；API 鉴权失败不重试，暂态网络失败与无效 JSON 有有限重试。模型服务并非离线运行的前置条件。

## 阶段、恢复与修复

阶段命令依次为 `sample`、`collect`、`build-seeds`、`instantiate`、`plan`、`generate`、`validate`、`repair`、`export`。每个命令都接收 `--config`；缺少前置数据时给出错误。`report` 使用 `--run-dir`。退出码：0 成功，1 执行失败或阻塞，2 验证未接受，130 用户中断。

- `--resume` 默认开启，根据输入、配置相关部分、代码与输出哈希复用成功阶段。缓存结果缺失或被篡改会重建。
- `--force` 重新执行当前命令的目标对象。指纹变化或产物变化使后续阶段在下次执行时失效；不清空输出目录。`run --force` 重新执行整条链。
- `--workspace-id`、`--seed-id` 用于选择已有工作区；`--limit` 限制采样/处理数量。单独生成或验证时可只处理目标工作区。
- `repair` 消费验证失败，保留诊断与失败文件，重建错误文件；实际生成依赖的上游哈希改变后，下游自动重建。世界/计划级错误从已保存 seed 确定性重建；不会擅自补造来源证据。
- 全局运行锁阻止两个进程同时写同一目录；SQLite 阶段事务和同目录临时文件加原子替换保护中断恢复。文件依赖按拓扑串行执行，独立对象目前也保守串行调度，因此模型并发不会超过配置上限。

预算在请求发出前检查。搜索、模型调用次数与下载字节跨恢复累计；墙钟上限按每次 CLI 调用计算，防止中断后的运行永久不可恢复。缺少价格表时美元成本为 null；provider 未返回 token 使用量时为 null。预算耗尽保留已完成文件与缓存，阶段标记 blocked。

可选 `budget.max_llm_tokens` 默认 null。启用后，每次调用按消息 UTF-8 字节数、最大输出及固定开销保守预留 token 额度，重试分别预留；这是预算上界估算，实际 token 使用量仍取 provider usage，不能把估算当作实测。下载字节包含最后一个有界在途 chunk，若越限会记录实际字节并停止。

## 文件与验证

每个工作区默认包含合同、采购订单、结算表、对账说明、可编辑汇报和结算 PDF。XLSX 包含 Transactions、Summary、Terms 三个 sheet、真实公式、缓存值与可编辑图表。DOCX 包含表格、页眉页脚；PPTX 使用可编辑文字和表格。可通过 `generation.optional_formats: [md, eml]` 增加沟通文件。

派生链为 **读取实际 XLSX → Decimal 重新计算 → DOCX/PPTX/PDF → 与台账核验**。解析失败、公式不在允许集合、公式缓存不匹配都阻断派生，不会回退台账冒充成功。Decimal 用于业务计算，只有写入 XLSX 数值接口时转换为 Excel 所需数值。

默认必需检查为结构、文件事实、计算、引用、依赖、版本和业务不变量。检查实际文件内容，不以 ZIP 能解压作为通过条件。`validation.required_checks` 可额外要求 `external_recalculation` 和 `visual`；对应检查 skipped 时工作区不接受。

LibreOffice 可选重算与 DOCX/PPTX 到 PDF 转换使用临时用户配置；设置 `validation.recalculate`、`validation.render` 开启。页图检查需要可选依赖 `pip install -e '.[render]'`。检查空白页、文本越界及 PPTX 对象几何边界；不宣称自动证明所有字体/遮挡问题。生成页图位于 private/rendered，可人工抽查。

`quality_tier: semantic` 表示结构、程序事实和计算一致性已检查；全部适用页图检查通过才为 rendered。模型语义推理并非该标签的含义。没有外部工具时准确报告 skipped；失败、缺失必需检查或必需检查被跳过均拒绝入池。

## 产物与下游接口

```text
runs/demo/
  sources.json, evidence.json, seeds.json, worlds.json
  candidates.json, coverage.json, workspaces.json, report.json
  state.sqlite
  cache/blobs/, cache/requests/, sources/
  workspaces/<workspace_id>/
    public/                       # 用户可见文件
    private/                      # world、seed、证据、计划、IR、验证、历史与页图
  export/<workspace_id>/
    manifest.json                 # 稳定版本化接口
    public/                       # 仅拷贝用户文件
```

下游读取 `workspaces.json` 中的 manifest 路径。manifest 含 `schema_version`、`workspace_id`、共同来源 `group_key`、provenance、quality_tier 和文件清单；每个文件包含相对路径、格式、SHA-256、版本及依赖类型。不导出事实台账、验证答案或证据片段。不自动发布或上传。

二进制哈希及四词 shingle Jaccard 近重复检测只标记，不删除多格式关联文件或历史版本。训练/验证/测试划分默认不执行；应按 group_key 整组划分。同源模板和角色视角共享家族键，避免实例泄漏。

详细数据关联见 [数据模型](docs/data-models.md)，来源与证据语义见 [来源说明](docs/sources.md)，本次实际运行记录见 [验证记录](docs/verification.md)。
