Reverse Agent（GUI 逆向解题助手）

这是一个面向 CTF / RE 场景的 Windows GUI 工具，支持静态与动态证据采集、模型辅助候选收敛、运行时验证和结构化报告输出。

核心能力
- 输入：本地文件路径或 URL。
- 模式：自动判断 / 静态分析 / 动态调试。
- 模型：Copilot CLI 或 OpenAI 兼容接口。
- 工具链：IDA 自动化 + OllyDbg 脚本自动化（可注入结构化 evidence/candidates）。
- 验证：支持 stdin 校验与 GUI（pywinauto）窗口级校验。
- 样本增强：`samplereverse` 已支持 GUI 运行时证据采集、低字节精确注入、可选 Z3 分区求解。
- 输出：`solve_reports\` 下生成结果与证据报告。

项目结构
- `app.py`：程序入口。
- `reverse_agent\gui.py`：GUI 主逻辑。
- `reverse_agent\harness.py`：批量评测 harness（任务集、可恢复重跑、样本级结果与汇总）。
- `reverse_agent\pipeline.py`：主流程编排（证据采集、候选生成、验证、报告）。
- `reverse_agent\models.py`：Copilot CLI / 本地模型调用封装。
- `reverse_agent\tool_runners.py`：IDA / Olly / angr 等工具调用。
- `reverse_agent\sample_solver.py`：`samplereverse` 专项求解与 checkpoint。
- `reverse_agent\reporter.py`：报告生成。
- `tests\`：pytest 测试集。
- `PROJECT_PROGRESS_LOG.txt`：详细迭代日志与样本进展（长文档已从 README 中拆出）。

快速开始
1) 安装依赖  
`pip install -r requirements.txt`

2) 启动 GUI  
`launch_reverse_agent.bat`  
或  
`python app.py`

3) 可选：创建桌面快捷方式  
`powershell -ExecutionPolicy Bypass -File .\create_desktop_shortcut.ps1`

4) 可选：安装高级求解依赖  
`pip install angr`

批量 Harness（新增）
1) 准备 JSON 任务集，例如：
```json
{
  "cases": [
    {
      "case_id": "sample-local",
      "input_value": "E:\\samples\\sample.exe",
      "expected_flag": "flag{demo}",
      "tags": ["smoke", "gui"]
    }
  ]
}
```

2) 运行可复现实验：
`python -m reverse_agent.harness --dataset .\cases.json --run-name smoke_suite --analysis-mode "Static Analysis"`

3) 结果目录：
- `solve_reports\harness_runs\<run_name>\run_manifest.json`：本次运行配置、git commit、digest。
- `solve_reports\harness_runs\<run_name>\case_results\*.json`：每个样本单独结果。
- `solve_reports\harness_runs\<run_name>\summary.json` / `summary.md`：聚合统计与人工可读汇总。

4) 断点续跑：
- 对同一个 `--run-name` 再次执行时，默认会跳过已完成样本。
- 可结合 `--case-id`、`--tag`、`--limit` 做 smoke / regression 子集运行。

常用配置
- Copilot CLI 推荐模板（Windows）：
  - `gh copilot -p "{prompt}" --allow-all-tools --allow-all-paths -s`
  - `copilot -p "{prompt}" --allow-all-tools --allow-all-paths -s`
  - `github-copilot-cli -p "{prompt}" --allow-all-tools --allow-all-paths -s`
- `samplereverse` 相关环境变量：
  - `REVERSE_AGENT_SAMPLE_MAX_ATTEMPTS`（默认 `250000`）
  - `REVERSE_AGENT_SAMPLE_MAX_SECONDS`（默认 `21600`）
  - `REVERSE_AGENT_SAMPLE_RANDOM_SEED`（默认 `1337`）
  - `REVERSE_AGENT_SAMPLE_ENABLE_Z3`（如设为 `1/true`，启用样本专用 Z3 分区探测）

排障建议
1) IDA 未执行：检查 IDA 路径与脚本路径。  
2) Copilot CLI 超时：使用非交互模板，并提高 GUI 中调用超时。  
3) Olly 未执行：确认动态模式、Olly 路径、脚本输出 JSON 约定。  
4) 未安装 angr：会自动跳过，不影响基础流程。  

说明
- `solve_reports\` 是运行产物目录，默认不应提交。
- 历史与细粒度进展请查看：`PROJECT_PROGRESS_LOG.txt`。

当前状态（2026-04-18）
- 测试基线：`python -m pytest -q` -> `50 passed`。
- `samplereverse.exe` 仍未解出，但针对该样本的运行时可观测性已明显增强：
  - 可稳定采集 GUI 输出证据；
  - 可向 GUI 输入框注入低字节候选（不再局限于可打印 ASCII）；
  - 已新增结构化长窗口搜索与 Z3 分区探测入口。
- 本轮结束时的结论：
  - `m40/m44/m48` 既有搜索窗口仍无命中；
  - 新增长窗口定向搜索可稳定得到 `3/5` 前缀近似解，但尚未到 `5/5`；
  - 若继续攻关，最高优先级仍是“compare 前真值提取”或更强的约束求解，而不是继续无约束盲搜。
