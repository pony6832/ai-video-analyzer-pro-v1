from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def find_port(start: int = 8001, end: int = 8010) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("Ports 8001-8010 are busy.")


def prepare_runtime(root: Path) -> None:
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    ffmpeg_bin = root / "runtime" / "ffmpeg" / "bin"
    if (ffmpeg_bin / "ffmpeg.exe").exists():
        os.environ["PATH"] = f"{ffmpeg_bin}{os.pathsep}{os.environ.get('PATH', '')}"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"


def run_cli(root: Path) -> bool:
    args = sys.argv[1:]
    if not args:
        return False
    if args[0].lower() == "main.py":
        args = args[1:]
    if not args:
        return False

    prepare_runtime(root)
    sys.argv = [sys.argv[0], *args]
    from main import app as cli_app

    cli_app()
    return True


def wait_and_open(port: int, log_path: Path) -> None:
    url = f"http://127.0.0.1:{port}/pro-workbench"
    deadline = time.time() + 30
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                webbrowser.open(url)
                return
        time.sleep(0.5)
    log_path.write_text(
        log_path.read_text(encoding="utf-8", errors="ignore")
        + f"\nServer did not become ready within 30 seconds. URL: {url}\n",
        encoding="utf-8",
    )


def main() -> None:
    root = app_root()
    if run_cli(root):
        return

    prepare_runtime(root)
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "server.log"

    port = find_port()
    url = f"http://127.0.0.1:{port}/pro-workbench"
    with log_path.open("w", encoding="utf-8") as log:
        log.write("AI AudioVideo Pro V2 starting...\n")
        log.write(f"Root: {root}\n")
        log.write(f"URL: {url}\n")
        log.flush()

        threading.Thread(target=wait_and_open, args=(port, log_path), daemon=True).start()

        try:
            from app.web import app

            uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
        except Exception as exc:
            log.write(f"\nStartup failed: {exc!r}\n")
            log.flush()
            raise


if __name__ == "__main__":
    main()
