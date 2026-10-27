# VisionFlow

智能视频分析与业务自动化学习项目。目前有 MP4 上传、任务状态查询和独立 Worker 的模拟检测流程；真实模型推理尚未实现。详细范围和验收状态见 [开发计划](docs/VisionFlow_Development_Plan.md) 和 [路线图](docs/roadmap.md)。

## 环境

本次验证：Windows x64、Python 3.11.6、Node 24.15.0、npm 11.12.1、FFmpeg/ffprobe 8.1.1。上传视频需安装 `ffprobe` 并加入 PATH；自动生成测试视频还需要 `ffmpeg`。无需 GPU。安装依赖需要访问包源或有完整缓存。Python 的 `requirements.txt` 固定了本机实际安装的全部依赖版本，npm 使用 `package-lock.json`；其他系统及干净机器复现尚未验证。

在项目根目录运行环境检查：

```powershell
.\scripts\check_environment.ps1
```

## 后端（PowerShell，终端 1）

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

访问 `http://127.0.0.1:8000/health`；接口文档在 `http://127.0.0.1:8000/docs`。如果 8000 已被占用，可用 `--port 18080`，并按下方说明设置前端代理。

## 上传视频和查询元数据

后端启动后，可以在 `/docs` 中选择 `POST /api/v1/videos`，通过 `file` 字段上传自己有权使用的 MP4；也可以在 PowerShell 中使用 `curl.exe`（将路径换成你自己的文件）：

```powershell
curl.exe -X POST -F "file=@C:\path\sample.mp4" http://127.0.0.1:8000/api/v1/videos
# 把返回的 video_id 填入下方 URL
curl.exe http://127.0.0.1:8000/api/v1/videos/替换为video_id
```

成功返回 `video_id`、原始文件名、`duration_ms`、宽、高、平均 FPS 和创建时间；视频保存在项目 `data/uploads/`，记录保存在 `data/visionflow.sqlite3`。默认最大 **100 MiB**、**300 秒**，仅接收扩展名为 `.mp4` 且主品牌为常见 MP4/ISO BMFF 的文件。读取元数据不等于验证每一帧均可解码，也不执行视频分析；目前没有前端上传页面。

可在启动后端的同一 PowerShell 终端中设置以下环境变量；`.env.example` 仅为示例，程序不会自动读取 `.env`：

```powershell
$env:VISIONFLOW_MAX_UPLOAD_BYTES = '104857600'
$env:VISIONFLOW_MAX_DURATION_SECONDS = '300'
# 可选：更改数据根目录。请自行确保该目录不被提交到 Git。
# $env:VISIONFLOW_STORAGE_DIR = 'D:\VisionFlowData'
```

常见拒绝：非 `.mp4` 为 415，文件过大为 413，损坏或时长超限为 422，缺少 `ffprobe` 为 503。错误响应格式如 `{"detail":{"code":"file_too_large","message":"..."}}`。应用会在保存时分块限制文件大小，但 multipart 解析可能先使用系统临时空间；对外部署前还需在服务器或反向代理层限制请求体大小。本项目目前仅用于本地开发。

## 创建任务并运行 Worker

1. 按上文启动后端并上传 MP4，记下返回的 `video_id`。
2. 在另一个 PowerShell 终端，从项目根目录启动独立 Worker：

   ```powershell
   cd backend
   .\.venv\Scripts\python.exe -m app.worker
   ```

   API 与 Worker 必须使用相同的 `VISIONFLOW_STORAGE_DIR`；未设置时两者默认使用项目的 `data/`。Worker 会持续等待新任务，按 Ctrl+C 停止。只处理至多一个排队任务并退出可使用 `.\.venv\Scripts\python.exe -m app.worker --once`。

3. 在后端 `/docs` 中，给 `POST /api/v1/jobs` 提交 `{"video_id":"上传后返回的ID"}`，记下 `job_id`。也可以在 PowerShell 中执行（请先替换 ID 和端口）：

   ```powershell
   $videoId = '替换为上传返回的 video_id'
   $body = @{ video_id = $videoId } | ConvertTo-Json -Compress
   $job = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/v1/jobs' -ContentType 'application/json' -Body $body
   Invoke-RestMethod "http://127.0.0.1:8000/api/v1/jobs/$($job.job_id)"
   Invoke-RestMethod "http://127.0.0.1:8000/api/v1/jobs/$($job.job_id)/detections?offset=0&limit=100"
   ```

任务初始为 `queued`，Worker 领取后为 `running`，完成后为 `succeeded`，处理失败为 `failed`；`cancelled` 为预留状态，当前没有取消接口。结果接口只对成功任务开放，支持 `offset` 和 `limit`（1–100）。输出保存在 `data/results/` 的 JSONL 文件中。

**当前 detector 是模拟实现**：它只接收帧时间戳和尺寸，不读取像素、不运行模型；返回的 `fake_object`、框和置信度都是合成数据，不能作为真实检测结果。Worker 用 `ffprobe` 逐帧读取源时间戳，按至少 500 ms 间隔抽样；时间戳以容器起点为零，缺少有效时间戳时任务失败，不用 FPS 推算。Worker 对单个视频帧扫描设置 120 秒超时。

同一数据目录一次只能运行一个 Worker。Worker 异常退出后，重新启动时会将遗留的 `running` 任务标为 `failed` 并清理其未完成输出；不会自动重试，需要时可为同一视频创建新任务。API 请求不会执行视频分析。

## 前端（PowerShell，终端 2）

```powershell
cd frontend
npm ci
```

如果路径不含 `#`，运行 `npm run dev`；含 `#` 时先按下方步骤进入目录联接并设置 `NODE_OPTIONS`，再启动。

访问 `http://127.0.0.1:5173/`。开发服务器将 `/health` 代理到 `http://127.0.0.1:8000`；例如后端改用 18080 且 5173 被占用时，先在前端终端执行：

```powershell
$env:VISIONFLOW_BACKEND_TARGET = 'http://127.0.0.1:18080'
npm run dev -- --port 15173
```

若项目路径包含 `#`（本机当前路径即如此），Vite 的路径解析可能失败。在执行 `npm ci` 后，创建一个不含 `#` 的 Windows 目录联接，并从联接进入前端目录运行：

```powershell
# 在 frontend 目录中执行；若目标名称已存在，请选一个未使用的名称。
$frontendPath = (Get-Location).Path
$frontendLink = Join-Path $env:TEMP 'VisionFlow-frontend'
New-Item -ItemType Junction -Path $frontendLink -Target $frontendPath
Set-Location $frontendLink
$env:NODE_OPTIONS = '--preserve-symlinks --preserve-symlinks-main'
npm run dev
```

本机通过目录联接构建和启动成功，并在浏览器检查了连接和断开提示；其他环境仍需验证。`npm run dev` 是开发服务器，代理仅在开发服务器中生效，生产部署尚未配置。

## 测试

```powershell
# backend 目录
.\.venv\Scripts\python.exe -m pytest -q

# frontend 目录；路径包含 # 时先按上述方法进入联接并设置 NODE_OPTIONS
npm test
npm run build
```

前端测试模拟健康接口的成功、网络失败和 HTTP 错误响应，不需要启动后端；后端测试使用 FastAPI TestClient。
后端视频集成测试会临时生成无业务数据的纯色视频；缺少 FFmpeg/ffprobe 时，依赖它们的测试会标记为跳过。

页面只在加载时检查一次后端状态；启动或关闭后端后需刷新页面。健康检查只表示 API 进程可响应，不表示模型、数据库或视频处理可用。上传视频和任务结果保存在本地且默认不提交 Git；使用第三方数据与模型前需分别核查许可。
