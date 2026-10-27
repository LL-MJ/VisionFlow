# 实际进度

| Task ID | 标题 | 状态 | 证据 | 遗留问题 | 用户验收 |
|---|---|---|---|---|---|
| P0-01 | 项目初始化 | DONE | 2026-10-27：pytest 1 passed、前端构建成功；两端在 18080/15173 启动，HTTP 健康接口和代理均返回 `{"status":"ok"}`；浏览器验证连接和断开提示 | 干净机器复现未验证 | 用户已验收 |
| P0-02 | API + Web 最小闭环 | IN_PROGRESS | 2026-10-27：pytest 1 passed；`/health` 与代理均返回 200；`/docs` 返回 200；OpenAPI 3.1 包含 `/health`；浏览器实测连接和断开提示 | 页面加载时检查一次，状态变化后需刷新 | 未明确验收；用户已要求进入 P0-03 |
| P0-03 | 最简单的前后端自动化测试与开发命令 | IN_PROGRESS | 2026-10-27：后端 pytest 1 passed；前端 Vitest 3 passed；前端构建通过 | 后端测试有 Starlette/AnyIO 弃用警告；干净机器复现未验证 | 未验收 |
| P1-01 | 视频上传与校验 | DONE | 2026-10-27：后端测试 12 passed；真实 HTTP 上传返回 201、按 ID 查询返回 200；使用 FFmpeg 生成的 1 秒 64×48 测试视频 | multipart 解析临时空间需部署层限制；干净机器复现未验证 | 用户已确认 API 和测试结果 |
| P1-02 | 后台任务与 fake detector | IN_PROGRESS | 2026-10-27：后端测试 20 passed；真实 HTTP 任务从 queued 到 succeeded，独立 Worker 返回两条 fake 结果 | 仅有 fake 结果，中断任务标记失败后需手动创建新任务；干净机器复现未验证 | 未验收 |
| P1-03 | 真实检测模型基线 | TODO | — | 未开始，需用户另行批准 | 未验收 |

本机检查：Windows 10.0.26300 x64；Python 3.11.6；Node 24.15.0；npm 11.12.1；FFmpeg/ffprobe 8.1.1；NVIDIA GeForce MX330，2048 MiB，驱动 582.67。CIM 查询受权限限制；OS 信息来自 .NET，GPU 信息来自 `nvidia-smi`。未验证 CUDA。

本机路径中的 `#` 使 Vite 直接运行失败；通过无 `#` 的目录联接、Vite 的 `preserveSymlinks` 和 Node 保留联接路径选项已验证构建、服务器启动与浏览器显示。默认 8000 和 5173 已占用，验证使用替代端口。pytest 输出 1 条 Starlette/AnyIO 弃用警告。仓库所有权检查需要 Git 的 `safe.directory`；本次仅对 Git 命令临时配置，未更改全局设置。

P0-02 的功能已在 P0-01 的详细任务卡中提前实现。用户随后明确要求进入 P0-03、P1-01 和 P1-02；P0-02、P0-03 的用户验收尚未明确记录。P1-02 的检测结果为合成数据，没有运行真实模型。
