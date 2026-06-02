from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import BooleanVar, IntVar, StringVar, Tk, filedialog, messagebox
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText


PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
VIDEOS_DIR = PROJECT_ROOT / "videos"
KNOWN_FFMPEG_BIN = (
    Path.home()
    / "AppData"
    / "Local"
    / "Microsoft"
    / "WinGet"
    / "Packages"
    / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "ffmpeg-8.1.1-full_build"
    / "bin"
)


class AnalyzerLauncher:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("AI Video News Analyzer")
        self.root.geometry("920x680")
        self.root.minsize(780, 560)

        self.video_path = StringVar(value=str((VIDEOS_DIR / "test.mp4").resolve()))
        self.chunk_minutes = IntVar(value=10)
        self.force = BooleanVar(value=False)
        self.status = StringVar(value="就緒")
        self.process: subprocess.Popen[str] | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()

        self._build_ui()
        self.root.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = ttk.Frame(self.root, padding=(16, 14, 16, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        title = ttk.Label(header, text="AI Video News Analyzer", font=("Segoe UI", 18, "bold"))
        title.grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status).grid(row=1, column=0, sticky="w", pady=(4, 0))

        controls = ttk.Frame(self.root, padding=(16, 8))
        controls.grid(row=1, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="影片檔").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(controls, textvariable=self.video_path).grid(row=0, column=1, sticky="ew")
        ttk.Button(controls, text="選擇影片", command=self.choose_video).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(controls, text="切段分鐘").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(10, 0))
        ttk.Spinbox(controls, from_=1, to=120, textvariable=self.chunk_minutes, width=8).grid(
            row=1, column=1, sticky="w", pady=(10, 0)
        )
        ttk.Checkbutton(controls, text="忽略 cache，重新分析", variable=self.force).grid(
            row=1, column=1, sticky="w", padx=(110, 0), pady=(10, 0)
        )

        buttons = ttk.Frame(controls)
        buttons.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(14, 0))
        self.start_button = ttk.Button(buttons, text="開始分析", command=self.start_analysis)
        self.start_button.pack(side="left")
        self.stop_button = ttk.Button(buttons, text="停止", command=self.stop_analysis, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="開啟輸出資料夾", command=self.open_outputs).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="開啟完整報告", command=self.open_latest_report).pack(side="left", padx=(8, 0))

        log_frame = ttk.Frame(self.root, padding=(16, 8, 16, 16))
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log = ScrolledText(log_frame, wrap="word", font=("Consolas", 10))
        self.log.grid(row=0, column=0, sticky="nsew")
        self._append_log("選擇影片後按「開始分析」。分析完成後到 outputs 查看報告。\n")

    def choose_video(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=str(VIDEOS_DIR),
            title="選擇影片檔",
            filetypes=[
                ("Video files", "*.mp4 *.mov *.mkv *.avi *.m4v *.webm"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.video_path.set(path)

    def start_analysis(self) -> None:
        video = Path(self.video_path.get()).expanduser()
        if not video.exists():
            messagebox.showerror("找不到影片", f"找不到影片檔：\n{video}")
            return
        if not PYTHON_EXE.exists():
            messagebox.showerror("找不到虛擬環境", f"找不到：\n{PYTHON_EXE}\n請先安裝 requirements。")
            return
        try:
            chunk_minutes = int(self.chunk_minutes.get())
            if chunk_minutes <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("參數錯誤", "切段分鐘必須是大於 0 的整數。")
            return

        command = [
            str(PYTHON_EXE),
            "main.py",
            "analyze",
            str(video),
            "--chunk-minutes",
            str(chunk_minutes),
        ]
        if self.force.get():
            command.append("--force")

        self._set_running(True)
        self.status.set("分析中")
        self._append_log("\n$ " + " ".join(command) + "\n\n")

        thread = threading.Thread(target=self._run_command, args=(command,), daemon=True)
        thread.start()

    def stop_analysis(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self._append_log("\n已送出停止指令。\n")
            self.status.set("停止中")

    def open_outputs(self) -> None:
        OUTPUTS_DIR.mkdir(exist_ok=True)
        os.startfile(OUTPUTS_DIR)

    def open_latest_report(self) -> None:
        reports = sorted(OUTPUTS_DIR.glob("*_full_report.md"), key=lambda path: path.stat().st_mtime, reverse=True)
        if not reports:
            messagebox.showinfo("尚無報告", "目前 outputs 裡沒有 *_full_report.md。")
            return
        webbrowser.open(reports[0].as_uri())

    def _run_command(self, command: list[str]) -> None:
        env = os.environ.copy()
        if KNOWN_FFMPEG_BIN.exists():
            env["PATH"] = str(KNOWN_FFMPEG_BIN) + os.pathsep + env.get("PATH", "")
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            self.process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.log_queue.put(line)
            code = self.process.wait()
            if code == 0:
                self.log_queue.put("\n分析完成。\n")
                self.root.after(0, lambda: self.status.set("完成"))
            else:
                self.log_queue.put(f"\n分析結束，exit code: {code}\n")
                self.root.after(0, lambda: self.status.set("失敗或已停止"))
        except Exception as exc:
            self.log_queue.put(f"\n啟動失敗：{exc}\n")
            self.root.after(0, lambda: self.status.set("啟動失敗"))
        finally:
            self.root.after(0, lambda: self._set_running(False))

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def _drain_log_queue(self) -> None:
        while True:
            try:
                self._append_log(self.log_queue.get_nowait())
            except queue.Empty:
                break
        self.root.after(100, self._drain_log_queue)

    def _append_log(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")


def main() -> None:
    if "--self-test" in sys.argv:
        print("launcher ok")
        return
    root = Tk()
    AnalyzerLauncher(root)
    root.mainloop()


if __name__ == "__main__":
    main()

