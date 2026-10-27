# 阶段 1：任务和独立 Worker

## 概念解释

API 创建并查询任务；Worker 从 SQLite 领取 `queued` 任务，处理结束后记录 `succeeded` 或 `failed`。API 不等待 Worker 完整处理视频。单个 Worker 的进程锁和 SQLite 原子领取分别负责限制 Worker 数量及避免重复领取。

## 关键设计

模拟 detector 不读取像素，输出合成的 `fake_object` 全帧框。Worker 逐帧读取源时间戳并采样，写入 JSONL 后才将任务标为成功；重启时遗留的 `running` 任务标为失败，不自动重跑。具体取舍见 [ADR 0001](../decisions/0001-single-worker-recovery.md)。

## 亲自实验

按 README 启动 API 与 Worker，上传合法短视频、创建任务，再查询任务和分页结果。观察状态与返回的 `model_id`；这些框只是模拟结果。

## 思考问题

1. 停止 Worker 后创建任务，任务状态是什么？再次启动 Worker 后会怎样？
2. 为什么任务创建接口返回成功时，视频处理不一定已经完成？
3. 如果查询结果中的 `class_name` 为 `fake_object`，这是否能说明视频里真的有目标？

## 实验复盘

- Codex 生成 1 秒纯色测试视频，真实 HTTP 创建任务为 `queued`；独立 Worker 处理后为 `succeeded`，返回 2 条假检测结果，源时间戳分别为 0、600 毫秒。
- 后端测试 20 项通过、1 条既有弃用警告；覆盖重复领取、任务失败与部分文件清理、空检测、Worker 锁及重启恢复。用户自己的观察待填写。没有运行真实模型或测量模型效果。
