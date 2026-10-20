# 素材管家 Material Hub

本地素材管理软件（BGM / 音效 / 图片 / 视频），基于 FastAPI + SQLite。

## 运行

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

- API 文档：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/api/health

## 外部依赖

- ffmpeg（视频时长/缩略图，M5 起使用）：需本机安装并加入 PATH。
  - Windows: `winget install Gyan.FFmpeg` 或从 https://ffmpeg.org 下载
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`

未安装时视频元数据与缩略图降级为占位，其余功能不受影响。

## 数据目录

默认 `./data/material_hub.db`，可用环境变量 `MH_DATA_DIR` 覆盖。
