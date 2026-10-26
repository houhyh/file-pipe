# 来源与证据

fixture 来源位于包内 `assets/fixtures`。两份 Markdown 由本项目作者为软件验证编写，声明 CC0-1.0；不代表真实公司的政策。fixture:// URI、SourceRecord.source_type=fixture、SeedBlueprint.provenance=fixture 及运行报告都保留该标记。fixture 不计入真实联网验收。

真实来源通过用户 URL 或 Brave Search 选出。搜索标题和摘要只用于排序，实际证据必须来自下载正文。URL 配置提供 HTML 与 PDF 参考示例；机构信息按站点 hostname 记录，许可不明确时为 unknown。采集成功不等于取得再分发许可，来源原文保存在内部运行缓存，不进入 public 导出。

解析器支持 HTML、PDF、DOCX、XLSX、PPTX、MD、TXT。HTML 保存正文节点索引和规范化文本偏移，PDF 保存页码，DOCX 保存段落或表格坐标，XLSX 保存 sheet/cell，PPTX 保存 slide/shape/单元格，MD/TXT 保存文本偏移。所有坐标都绑定下载文件 SHA-256。

证据记录保存原文及模型主张两个独立字段。当前提取采用逐字句子，LLM 只能选择已存在的 evidence_id，最终主张保持原文。入池前逐条校验原文位置与哈希；逐字主张通过可复核的原文同一性检查，不把关键词命中宣称为通用语义推理器。候选必须同时有匹配流程、付款规则及至少两份非重复来源。

下载器只允许 HTTP(S)，拒绝 URL 内嵌凭据，解析所有 DNS 结果并默认拒绝非公网地址；连接固定到已检查 IP，TLS 仍校验原 hostname。每次跳转重新检查。显式 `retrieval.allowed_private_hosts` 才能放行某个内网主机。限制下载字节、连接时间、重定向次数及 ZIP 展开规模，拒绝宏文件、路径越界 ZIP、旧 Office 二进制、无文本 PDF与伪装 Office 的 HTML 登录页。不执行来源脚本、宏或指令。

相同正文哈希不能算两份独立来源。相同内容的多个 URL 可保留来源上下文，但不能补足证据数量。文本近重复只标记，工作区共源分组由来源内容和模板家族共同决定。所有组织交易与付款天数由虚构事实层产生，公开规则保留机构范围，不自动成为该虚构公司的真实政策。
