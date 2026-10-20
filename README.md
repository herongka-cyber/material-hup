# 素材管家 Material Hub

本地素材管理软件（BGM / 音效 / 图片 / 视频），基于 FastAPI + SQLite。

## Windows 桌面版（推荐）

每次 push 到 main 后，GitHub Actions 会自动在 Windows 云主机上构建单文件
`MaterialHub.exe`：仓库页面 → Actions → 最新一次 "Build Windows Desktop App" →
Artifacts 下载 `MaterialHub-windows`，解压双击即用。

- 首次启动会自动打开浏览器进入界面；数据（数据库/缩略图）保存在 exe 同级的
  `data\` 目录里，重装/更新 exe 不会丢。
- 需要本机安装 ffmpeg 才能用视频缩略图：`winget install Gyan.FFmpeg`
  （不装也能用，只是视频没有缩略图）。
- 全量版包含 librosa：「补全 BGM 特征」在桌面版中可用（单曲分析约几十秒）。

## 源码运行

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
