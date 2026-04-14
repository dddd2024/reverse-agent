Reverse Agent（GUI 逆向解题助手）

功能概览
- 支持输入：本地文件路径或下载 URL。
- 支持模式：静态分析 / 动态调试。
- 支持模型：Copilot CLI 或本地 OpenAI 兼容模型。
- 输出最可能 flag，并在 `solve_reports\` 生成详细报告。
- 支持非花括号答案格式（如纯字符串口令 `SEPTA`）的结果提取。
- 对本地 EXE 可执行样本支持候选运行时校验（检测 `Correct!`）以提升命中率。
- 支持工具链自动化（第一版）：**IDA 全自动** + **OllyDbg 接口预留**。

快速开始
1) 安装依赖：
   pip install -r requirements.txt

2) 启动 GUI：
   launch_reverse_agent.bat
   或
   python app.py

3) （可选）创建桌面快捷方式：
   powershell -ExecutionPolicy Bypass -File .\create_desktop_shortcut.ps1

Copilot CLI 模式
- 在 GUI 里设置命令模板，支持 `{prompt}` 占位符。
- 推荐模板（Windows）：
  - gh copilot -p "{prompt}" --allow-all-tools --allow-all-paths -s
  - copilot -p "{prompt}" --allow-all-tools --allow-all-paths -s
  - github-copilot-cli -p "{prompt}" --allow-all-tools --allow-all-paths -s

本地模型模式
- 使用 OpenAI 兼容接口：
  POST {base_url}/v1/chat/completions
- 在 GUI 中填写 Base URL / 模型名称 / API Key（可选）。

工具链自动化（IDA + OllyDbg）
- 启用“工具链自动分析”后：
  - IDA：自动执行 headless 分析脚本，提取字符串与函数证据，并注入模型 prompt。
  - OllyDbg：当前版本仅预留接口（记录配置与状态），不执行完整自动调试。

IDA 配置说明
- 可执行文件：优先使用 GUI 手动填写路径；支持两种填写方式：
  - 直接填写可执行文件（如 `E:\Program Files\ida_pro\idat64.exe`）
  - 填写 IDA 安装目录（如 `E:\Program Files\ida_pro`，程序会自动查找 exe）
- 留空时自动尝试：
  `idat64.exe` / `idat.exe` / `ida64.exe` / `ida.exe`
- 脚本路径：留空时默认使用项目内脚本：
  `reverse_agent\ida_scripts\collect_evidence.py`
- 超时：建议 120~300 秒，视样本复杂度调整。
- 如果填写了 IDA/OllyDbg 路径或脚本，即使未手动勾选总开关，程序也会自动启用工具链分析。

常见问题
1) IDA 未执行：
   - 检查 IDA 路径是否正确；
   - 检查脚本路径是否存在且可读。
2) Copilot CLI 卡住或无输出：
   - 使用带 `-p`、`--allow-all-tools`、`--allow-all-paths`、`-s` 的非交互模板。
3) 动态模式没有自动调试：
   - 第一版仅实现 OllyDbg 接口预留，后续可在此基础上接入脚本插件自动化。

收尾与发布说明
- 关闭 GUI 前建议确认：
  - `solve_reports\` 下已生成本次报告；
  - 工具链配置（IDA 路径/脚本）已按当前机器保存。
- 仓库发布建议：
  - 不提交 `solve_reports\` 的产物与本地缓存；
  - 提交代码后可直接用 `gh repo create` 创建公开仓库并推送。
