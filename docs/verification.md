# 实际运行与验收记录

运行日期：2026-10-26。环境：Python 3.13.13、Linux。所有数据均在本地生成，未自动发布或上传。

## 已执行命令

```bash
python -m venv --system-site-packages .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/office-synth init --output configs/demo
.venv/bin/python -m pytest -q --tb=short
.venv/bin/office-synth run --config configs/demo/pipeline.yaml --resume
.venv/bin/office-synth run --config configs/demo/urls.yaml --resume
.venv/bin/office-synth run --config configs/demo/roles.yaml --resume
.venv/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/office-synth-dist .
```

依赖安装、真实 URL 首次采集及 LibreOffice 渲染在获得环境权限后执行。常规测试与离线示例无需网络；后续 URL 示例可以复用原始内容缓存，不再次下载。

## 结果

| 检查 | 实际结果 |
|---|---|
| 自动化测试 | 30 passed、1 skipped；跳过项为默认关闭的 opt-in 联网测试 |
| 基础离线闭环 | 1 个 fixture seed、2 个 accepted 工作区、14 份原生文件 |
| 三角色演示 | 3 个 fixture seed、6 个 accepted 工作区、42 份原生文件：24 DOCX、6 XLSX、6 PPTX、6 PDF |
| 真实 URL 闭环 | 下载并解析 2 HTML、1 PDF，得到 1 个有来源定位的 seed、2 个 accepted 工作区、14 份原生文件 |
| 真实联网消耗 | 来源下载共 243,632 字节；搜索 API 0 次、LLM 0 次；无价格表，美元成本为 null |
| 共享事实和实际依赖 | XLSX 多 sheet 与真实公式；下游读取实际 XLSX、Decimal 计算并与台账核验 |
| 故障注入 | 金额篡改、公式篡改、引用文件删除、依赖环、无效版本、来源篡改均被检测 |
| 修复与恢复 | 失败版本及诊断保留；局部修复、事实层重建、未受影响文件复用、预算持久化和中断恢复测试通过 |
| 导出隔离 | manifest 及 public 文件；不包含事实、答案、内部证据；被篡改的工作区不能利用旧验证结果导出 |
| 安装包 | wheel 构建成功，包内包含两份 fixture |

三角色演示只表示同一采购结算家族的不同角色视角。报告独立统计业务家族、蓝图、实例、结构与内容数量，不把六个实例当成六种业务。

## 渲染与外部重算限制

当前 LibreOffice 7.3.7.2 仅具备 Impress 组件：`libsdlo.so` 存在，Writer 的 `libswlo.so` 和 Calc 的 `libsclo.so` 缺失。PPTX 到 PDF 的转换成功；DOCX 转换与 XLSX 外部重算均返回无法加载源文件，准确记为 skipped。

三角色运行中 PPTX/PDF 共 12 项页图检查通过，输出 24 张页图；6 项 PPTX 几何检查通过。24 项 DOCX 页图检查和 6 项 XLSX 外部重算跳过。全部工作区质量层级为 semantic，没有标为 rendered。公式允许集的 Decimal 计算与缓存核验通过，但这不等于 Excel/LibreOffice 已完成重算。

已人工查看代表性 PPTX 的首页、事实表页及结算 PDF 页图，文字清晰、表格可读，未发现明显越界。未对 DOCX 声称完成视觉检查。

## 尚未执行的集成项

- 未配置 `BRAVE_SEARCH_API_KEY`，真实 Brave Search 请求未运行；适配器认证、缓存和失败策略使用隔离响应测试。
- 未配置 `OFFICE_SYNTH_LLM_KEY` 与实际模型，在线 LLM 提炼/文字选择未运行；schema 重试、缓存重放和预算行为使用隔离响应测试。
- opt-in 联网测试默认跳过；真实下载已通过 URL 全链路命令独立验证，未把 fixture 或搜索摘要当作真实来源。

## 可检查产物

- `runs/demo/report.json`：基础离线报告。
- `runs/roles/report.json`：三角色覆盖、重复、验证、跳过项与消耗报告。
- `runs/urls/report.json`：真实来源采集及工作区报告。
- 各运行目录的 `workspaces.json`：当前 accepted 导出索引。
- `runs/<配置>/export/<workspace_id>/public/`：下游可直接消费的原生文件。
- `runs/roles/workspaces/<workspace_id>/private/rendered/`：实际页图和转换 PDF。
- `/tmp/office-synth-dist/office_file_synthesis-0.1.0-py3-none-any.whl`：可安装包。

生成内容与运行缓存已从 git 忽略；源码包仅保留小型 fixture。开发期间旧工作区目录可能被保留，accepted 池以当前 workspaces.json 索引为准。
