"""
MaterialHub Windows 桌面启动器（PyInstaller 打包入口）。

行为：
- 数据目录固定为 exe 同级目录下的 data\\（数据库 + 缩略图），便携不丢数据。
- 启动 uvicorn（127.0.0.1:8123），服务就绪后自动用默认浏览器打开前端页面。
- 8123 端口被占用时给出明确提示后退出。

本地直接运行（未打包）时同样可用：python desktop_main.py
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

BIND_HOST = "0.0.0.0"          # 局域网可达：手机连同一 WiFi 可访问
LOCAL_HOST = "127.0.0.1"
PORT = 8123
APP_URL = f"http://{LOCAL_HOST}:{PORT}/"


def _exe_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _lan_ip() -> str:
    """尽力获取本机局域网 IP（手机访问用）；失败返回空串。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return ""
    finally:
        s.close()


def _port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((LOCAL_HOST, PORT)) == 0


def _wait_and_open_browser(timeout: float = 30.0) -> None:
    """等服务就绪后打开浏览器（后台线程，不阻塞主线程的 uvicorn）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            if s.connect_ex((LOCAL_HOST, PORT)) == 0:
                break
        time.sleep(0.5)
    webbrowser.open(APP_URL)


def main() -> int:
    base = _exe_dir()

    # 数据目录：exe 同级 data\（打包/源码运行都一致，便携）
    os.environ["MH_DATA_DIR"] = str(base / "data")

    # 打包后 static 资源在 _MEIPASS 里，PROJECT_ROOT 会自动指向那里，无需处理

    print("=" * 52)
    print("  MaterialHub 素材管家 正在启动 …")
    print("=" * 52)

    if _port_in_use():
        print(f"[错误] {LOCAL_HOST}:{PORT} 端口被占用。")
        print("请先关闭占用该端口的程序（可能是另一个 MaterialHub），再重新双击启动。")
        input("按回车退出 …")
        return 1

    # 延迟导入：必须在 MH_DATA_DIR 设置之后
    import uvicorn  # noqa: E402

    from main import app  # noqa: E402  直接导入 app 对象（不要用 "main:app" 字符串：打包工具分析不到字符串导入，会漏打包主程序模块）

    threading.Thread(target=_wait_and_open_browser, daemon=True).start()

    lan_ip = _lan_ip()
    print(f"[就绪] 浏览器将自动打开 {APP_URL}")
    if lan_ip:
        print(f"[手机] 同一 WiFi 下，手机浏览器访问 http://{lan_ip}:{PORT}/")
        print("      （首次运行 Windows 防火墙弹窗请点 允许访问）")
    print("[提示] 不要关闭这个黑窗口，关闭它 = 退出软件。")
    print("-" * 52)

    uvicorn.run(app, host=BIND_HOST, port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
