"""Small Windows desktop manager for the public PetNote Vercel deployment."""
from __future__ import annotations

import ctypes
from datetime import datetime
import os
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.vercel_control import ControlError, PUBLIC_ORIGIN, VercelController

TITLE = "펫노트 서버 관리"
BG = "#f4f6f9"
INK = "#192b42"
MUTED = "#65748b"
BLUE = "#3367e8"
GREEN = "#188268"
AMBER = "#ac6c0a"


def configure_tk():
    # Some Windows virtual environments omit the base Tcl search directory.
    # Set paths for this process only; do not change the user or system environment.
    base = Path(sys.base_prefix) / "tcl"
    for variable, directory, marker in (
        ("TCL_LIBRARY", f"tcl{tk.TclVersion}", "init.tcl"),
        ("TK_LIBRARY", f"tk{tk.TkVersion}", "tk.tcl"),
    ):
        candidate = base / directory
        if (candidate / marker).is_file():
            os.environ.setdefault(variable, str(candidate))


class ManagerWindow:
    def __init__(self, window, controller=None, *, autostart=True):
        self.window = window
        self.controller = controller
        self.messages = queue.SimpleQueue()
        self.busy = False
        self.state = None
        self.close_requested = False
        window.title(TITLE)
        window.configure(bg=BG)
        window.geometry("560x640")
        window.minsize(540, 620)
        window.resizable(True, True)
        window.option_add("*Font", ("Malgun Gothic", 10))
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<F5>", lambda event: self.submit("status"))
        window.bind("<Control-r>", lambda event: self.submit("status"))
        outer = tk.Frame(window, bg=BG, padx=30, pady=26)
        outer.pack(fill="both", expand=True)
        tk.Label(outer, text="PETNOTE", font=("Segoe UI", 10, "bold"), fg=BLUE, bg=BG).pack(anchor="w")
        tk.Label(outer, text="펫노트 서버 관리", font=("Malgun Gothic", 23, "bold"), fg=INK, bg=BG).pack(anchor="w", pady=(6, 8))
        tk.Label(outer, text="공개 서버를 한곳에서 켜고 끄세요.", fg=MUTED, bg=BG).pack(anchor="w", pady=(0, 22))

        card = tk.Frame(outer, bg="white", highlightbackground="#e0e6ef", highlightthickness=1, padx=22, pady=18)
        card.pack(fill="x")
        headline = tk.Frame(card, bg="white")
        headline.pack(fill="x")
        self.dot = tk.Label(headline, text="●", font=("Segoe UI", 15), fg=MUTED, bg="white")
        self.dot.pack(side="left", padx=(0, 8))
        self.status_label = tk.Label(headline, text="상태 확인 중", font=("Malgun Gothic", 17, "bold"), fg=INK, bg="white")
        self.status_label.pack(side="left")
        self.detail = tk.StringVar(value="저장된 Vercel 연결을 확인하고 있습니다.")
        tk.Label(card, textvariable=self.detail, anchor="w", justify="left", wraplength=420, height=2, fg=MUTED, bg="white").pack(fill="x", pady=(8, 9))
        tk.Label(card, text=PUBLIC_ORIGIN.removeprefix("https://"), font=("Segoe UI", 11), fg=INK, bg="white").pack(anchor="w")
        self.updated = tk.StringVar(value="")
        tk.Label(card, textvariable=self.updated, font=("Malgun Gothic", 9), fg=MUTED, bg="white").pack(anchor="w", pady=(5, 0))

        actions = tk.Frame(outer, bg=BG)
        actions.pack(fill="x", pady=(22, 12))
        actions.columnconfigure((0, 1), weight=1, uniform="actions")
        self.start_button = self.button(actions, "서버 켜기", lambda: self.submit("start"), primary=True)
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.stop_button = self.button(actions, "서버 끄기", lambda: self.submit("stop"))
        self.stop_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        secondary = tk.Frame(outer, bg=BG)
        secondary.pack(fill="x")
        secondary.columnconfigure((0, 1), weight=1, uniform="secondary")
        self.refresh_button = self.button(secondary, "상태 확인", lambda: self.submit("status"), small=True)
        self.refresh_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.open_button = self.button(secondary, "펫노트 열기 ↗", self.open_site, small=True)
        self.open_button.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        progress_holder = tk.Frame(outer, bg=BG, height=4)
        progress_holder.pack(fill="x", pady=(17, 10))
        self.progress = ttk.Progressbar(progress_holder, mode="indeterminate")
        self.note = tk.StringVar(value="프로그램을 닫아도 서버 상태는 유지됩니다.")
        tk.Label(outer, textvariable=self.note, justify="left", anchor="w", wraplength=480, fg=MUTED, bg=BG, font=("Malgun Gothic", 9)).pack(fill="x")
        tk.Label(outer, text="끄면 사이트 접속이 중지됩니다. 저장된 기록은 유지됩니다.", justify="left", anchor="w", wraplength=480, fg=MUTED, bg=BG, font=("Malgun Gothic", 9)).pack(fill="x", pady=(6, 0))
        self.reconnect_button = self.button(outer, "Vercel 다시 연결", self.reconnect, small=True)
        self._buttons()
        window.after(80, self.drain)
        if autostart:
            window.after(100, lambda: self.submit("status"))

    @staticmethod
    def button(parent, text, command, *, primary=False, small=False):
        return tk.Button(
            parent, text=text, command=command,
            bg=BLUE if primary else "#e7ecf4", fg="white" if primary else INK,
            activebackground="#2855c4" if primary else "#d9e2ef",
            activeforeground="white" if primary else INK,
            disabledforeground="#a2adbf", relief="flat", borderwidth=0,
            font=("Malgun Gothic", 10 if small else 12, "normal" if small else "bold"),
            padx=14, pady=9 if small else 13, cursor="hand2",
            highlightthickness=2, highlightbackground=BG, highlightcolor=BLUE,
        )

    def _buttons(self):
        known = self.state is not None
        can_start = known and self.state.paused and not self.busy
        self.start_button.configure(state="normal" if can_start else "disabled", bg=BLUE if can_start else "#e7ecf4")
        self.stop_button.configure(state="normal" if known and not self.state.paused and not self.busy else "disabled")
        self.refresh_button.configure(state="disabled" if self.busy else "normal")
        self.reconnect_button.configure(state="disabled" if self.busy else "normal")

    def submit(self, action):
        if self.busy:
            return
        self.busy = True
        self.reconnect_button.pack_forget()
        self._buttons()
        self.progress.place(x=0, y=0, relwidth=1, height=4)
        self.progress.start(12)
        self.detail.set({"status": "현재 서버와 공개 주소를 확인하고 있습니다.",
                         "start": "서버를 켜고 있습니다. 잠시만 기다려 주세요.",
                         "stop": "서버를 끄고 있습니다. 잠시만 기다려 주세요."}[action])
        # Non-daemon worker finishes the requested operation even if the window closes.
        threading.Thread(target=self.work, args=(action,), daemon=False).start()

    def work(self, action):
        try:
            if self.controller is None:
                self.controller = VercelController()
            if action == "status":
                state = self.controller.status()
            else:
                state = self.controller.set_paused(
                    action == "stop", progress=lambda message: self.messages.put(("progress", message)),
                )
            self.messages.put(("state", state))
        except ControlError as error:
            self.messages.put(("error", error))
        except Exception:
            self.messages.put(("error", ControlError("상태를 확인하지 못했습니다. '상태 확인'을 다시 눌러 주세요.")))
        finally:
            self.messages.put(("done", action))

    def show_state(self, state):
        self.state = state
        if state.paused:
            label, color = ("꺼짐", MUTED) if state.confirmed else ("끄는 중", AMBER)
            detail = "공개 접속이 중지돼 있습니다. 서버 켜기를 누르면 다시 접속할 수 있습니다."
        else:
            label, color = ("켜짐", GREEN) if state.confirmed else ("접속 확인 필요", AMBER)
            detail = "공개 주소에 정상 접속할 수 있습니다." if state.confirmed else "Vercel은 켜져 있지만 공개 주소 응답을 확인하지 못했습니다."
        self.status_label.configure(text=label)
        self.dot.configure(fg=color)
        self.detail.set(detail)
        self.updated.set("마지막 확인  " + datetime.now().strftime("%H:%M:%S"))
        self.note.set("프로그램을 닫아도 서버 상태는 유지됩니다.")
        self._buttons()

    def drain(self):
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "progress":
                    self.detail.set(value)
                elif kind == "state":
                    self.show_state(value)
                elif kind == "error":
                    self.state = None
                    self.status_label.configure(text="연결 필요" if value.kind == "auth" else "확인 필요")
                    self.dot.configure(fg=AMBER)
                    self.detail.set(str(value))
                    if value.kind == "auth":
                        self.reconnect_button.pack(anchor="w", pady=(12, 0))
                    self.note.set("최근 확인 상태를 기준으로 표시합니다. 상태 확인으로 다시 조회하세요.")
                elif kind == "done":
                    self.busy = False
                    self.progress.stop()
                    self.progress.place_forget()
                    self._buttons()
                    if self.close_requested:
                        self.window.destroy()
                        return
        except queue.Empty:
            pass
        self.window.after(80, self.drain)

    def reconnect(self):
        try:
            if self.controller is None:
                self.controller = VercelController()
            self.controller.reconnect()
            self.note.set("열린 창에서 연결을 완료한 뒤 이 창의 '상태 확인'을 눌러 주세요.")
        except ControlError as error:
            self.detail.set(str(error))

    def open_site(self):
        webbrowser.open(PUBLIC_ORIGIN + "/", new=2)

    def close(self):
        if self.busy:
            self.close_requested = True
            self.window.withdraw()
        else:
            self.window.destroy()


def windows_instance():
    """Bring the existing window forward instead of opening duplicate controls."""
    if os.name != "nt":
        return None, False
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    handle = kernel.CreateMutexW(None, False, "Local\\PetNoteVercelManagerWindow")
    if not handle:
        raise OSError("Could not acquire the application mutex")
    duplicate = ctypes.get_last_error() == 183
    if duplicate:
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user.FindWindowW.restype = wintypes.HWND
        user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user.SetForegroundWindow.argtypes = [wintypes.HWND]
        hwnd = user.FindWindowW(None, TITLE)
        if hwnd:
            user.ShowWindow(hwnd, 9)
            user.SetForegroundWindow(hwnd)
    return handle, duplicate


def main():
    handle = None
    try:
        handle, duplicate = windows_instance()
        if duplicate:
            return 0
        if os.name == "nt":
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                pass
        configure_tk()
        window = tk.Tk()
        ManagerWindow(window)
        window.mainloop()
        return 0
    except Exception:
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(
                None, "관리 창을 열지 못했습니다. 프로젝트의 Python 실행 환경을 확인해 주세요.", TITLE, 0x10,
            )
        return 1
    finally:
        if handle and os.name == "nt":
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(handle)


if __name__ == "__main__":
    raise SystemExit(main())
