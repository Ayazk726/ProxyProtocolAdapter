#!/usr/bin/env python3
"""Tkinter GUI for proxy_nat.py.

Supports multiple independent NAT rules. The window can be closed to the
system tray; the tray menu has "关闭 GUI" and "彻底退出".
"""

from __future__ import annotations

import argparse
import atexit
import ctypes
import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import pystray
from PIL import Image, ImageDraw

import proxy_nat

FROZEN = bool(getattr(sys, "frozen", False))
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
LOG_DIR = os.path.join(BASE_DIR, "logs")
CONFIG_PATH = os.path.join(CONFIG_DIR, "proxy_nat.json")
PID_PATH = os.path.join(LOG_DIR, "proxy_nat.pid")
GUI_PID_PATH = os.path.join(LOG_DIR, "proxy_nat_gui.pid")
NAT_SCRIPT = os.path.join(BASE_DIR, "proxy_nat.py")
NAT_COMMAND = [sys.executable, "--run-nat"] if FROZEN else [sys.executable, NAT_SCRIPT]
FONT = ("Microsoft YaHei UI", 9)


def ensure_layout() -> None:
    """Create the config and logs directories next to the application."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)


def resource_path(relative: str) -> str:
    base = getattr(sys, "_MEIPASS", BASE_DIR) if FROZEN else BASE_DIR
    return os.path.join(base, relative)


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _pid_running(pid: int) -> bool:
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel32.CloseHandle.restype = ctypes.c_bool
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
    except Exception:
        pass
    return False


def nat_instance_running() -> bool:
    try:
        if not os.path.exists(PID_PATH):
            return False
        with open(PID_PATH, "r", encoding="ascii") as fp:
            pid = int(fp.read().strip() or "0")
        return pid > 0 and _pid_running(pid)
    except (OSError, ValueError):
        return False


def gui_instance_running() -> bool:
    try:
        if not os.path.exists(GUI_PID_PATH):
            return False
        with open(GUI_PID_PATH, "r", encoding="ascii") as fp:
            pid = int(fp.read().strip() or "0")
        return pid > 0 and _pid_running(pid)
    except (OSError, ValueError):
        return False


def acquire_gui_lock() -> bool:
    os.makedirs(LOG_DIR, exist_ok=True)
    if gui_instance_running():
        return False
    with open(GUI_PID_PATH, "w", encoding="ascii") as fp:
        fp.write(str(os.getpid()))
    return True


def release_gui_lock() -> None:
    try:
        if not os.path.exists(GUI_PID_PATH):
            return
        with open(GUI_PID_PATH, "r", encoding="ascii") as fp:
            pid = fp.read().strip()
        if pid == str(os.getpid()):
            os.remove(GUI_PID_PATH)
    except OSError:
        pass


def relaunch_as_admin() -> None:
    params = subprocess.list2cmdline([os.path.abspath(__file__), *sys.argv[1:]])
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, BASE_DIR, 1
    )
    if result <= 32:
        raise SystemExit("管理员权限启动失败或用户取消")


def load_config() -> dict:
    try:
        return proxy_nat.load_config(CONFIG_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        messagebox.showerror("配置读取失败", str(exc))
        return proxy_nat.normalize_config(proxy_nat.DEFAULT_CONFIG)


def resolve_log_path(config: dict) -> str:
    log_path = config.get("log", "logs/proxy_nat.log")
    if not os.path.isabs(log_path):
        log_path = os.path.join(BASE_DIR, log_path)
    return log_path


class RuleDialog:
    def __init__(self, parent, rule: dict | None, existing: list[dict]):
        self.result = None
        self.existing = existing

        self.win = tk.Toplevel(parent)
        self.win.title("编辑 NAT 规则" if rule else "新增 NAT 规则")
        self.win.resizable(False, False)
        self.win.transient(parent)
        self.win.grab_set()

        body = ttk.Frame(self.win, padding=14)
        body.pack(fill="both", expand=True)

        self.var_name = tk.StringVar(value=(rule or {}).get("name", f"NAT {len(existing) + 1}"))
        self.var_enabled = tk.BooleanVar(value=(rule or {}).get("enabled", True))
        self.var_target_host = tk.StringVar(value=(rule or {}).get("target_host", "127.0.0.1"))
        self.var_target_port = tk.StringVar(value=str((rule or {}).get("target_port", 27021)))
        self.var_reverse_proxy_source = tk.StringVar(value=(rule or {}).get("reverse_proxy_source", "127.0.0.1"))
        self.var_reply_source = tk.StringVar(value=(rule or {}).get("reply_source", "127.0.0.1"))
        self.original = rule

        rows = [
            ("名称", self.var_name),
            ("目标地址", self.var_target_host),
            ("目标端口", self.var_target_port),
            ("反向代理源地址", self.var_reverse_proxy_source),
            ("回包源地址", self.var_reply_source),
        ]
        for row, (label, variable) in enumerate(rows):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
            entry = ttk.Entry(body, textvariable=variable, width=32)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            if row == 0:
                entry.focus_set()

        ttk.Checkbutton(body, text="启用此规则", variable=self.var_enabled).grid(
            row=len(rows), column=1, sticky="w", pady=(6, 2)
        )

        buttons = ttk.Frame(body)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="取消", command=self.win.destroy, width=10).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="确定", command=self._accept, width=10).pack(side="right")

        self.win.bind("<Return>", lambda _event: self._accept())
        self.win.bind("<Escape>", lambda _event: self.win.destroy())
        self.win.wait_window()

    def _accept(self) -> None:
        name = self.var_name.get().strip() or f"NAT {len(self.existing) + 1}"
        target_host = self.var_target_host.get().strip() or "127.0.0.1"
        reverse_proxy_source = self.var_reverse_proxy_source.get().strip() or "127.0.0.1"
        reply_source = self.var_reply_source.get().strip() or "127.0.0.1"
        try:
            target_port = int(self.var_target_port.get().strip())
        except ValueError:
            messagebox.showerror("错误", "目标端口必须是数字", parent=self.win)
            return
        if not 1 <= target_port <= 65535:
            messagebox.showerror("错误", "目标端口必须在 1-65535 之间", parent=self.win)
            return

        for existing in self.existing:
            if existing is self.original:
                continue
            if (
                int(existing.get("target_port", 0)) == target_port
                and existing.get("reverse_proxy_source", "") == reverse_proxy_source
            ):
                messagebox.showerror(
                    "错误",
                    f"已存在相同目标端口和反向代理源的规则：{existing.get('name', '')}",
                    parent=self.win,
                )
                return

        self.result = {
            "name": name,
            "enabled": bool(self.var_enabled.get()),
            "target_host": target_host,
            "target_port": target_port,
            "reverse_proxy_source": reverse_proxy_source,
            "reply_source": reply_source,
        }
        self.win.destroy()


class ProxyNatGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Proxy Protocol Adapter")
        self._set_window_icon()
        self.root.geometry("980x720")
        self.root.minsize(860, 600)

        ensure_layout()
        self.config = load_config()
        self.rules = [dict(rule) for rule in self.config.get("rules", [])]
        self.proc: subprocess.Popen | None = None
        self.log_path = resolve_log_path(self.config)
        try:
            self.log_offset = os.path.getsize(self.log_path) if os.path.exists(self.log_path) else 0
        except OSError:
            self.log_offset = 0
        self.stderr_path = os.path.join(LOG_DIR, "proxy_nat_stderr.log")
        self.stderr_file = None
        self.was_external = False
        self.tray_icon = None

        self._build_ui()
        self._load_globals()
        self._refresh_rule_tree()
        self._create_tray()
        self._refresh_status()
        self._tail_log()
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

    # ------------------------------------------------------------------ UI
    def _set_window_icon(self) -> None:
        try:
            self.root.iconbitmap(resource_path(r"icons\B_bridge.ico"))
        except Exception:
            try:
                self.root.iconphoto(True, tk.PhotoImage(file=resource_path(r"icons\B_bridge.png")))
            except Exception:
                pass

    def _build_ui(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure(".", font=FONT)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Ok.TLabel", foreground="#187a2f")
        style.configure("Bad.TLabel", foreground="#b00020")

        header = ttk.Frame(self.root, padding=(14, 12, 14, 6))
        header.pack(fill="x")
        ttk.Label(header, text="Proxy Protocol Adapter", style="Title.TLabel").pack(side="left")

        status = ttk.LabelFrame(self.root, text="状态", padding=(12, 8))
        status.pack(fill="x", padx=14, pady=(0, 8))
        self.status_var = tk.StringVar(value="NAT: 已停止")
        self.pid_var = tk.StringVar(value="PID: -")
        self.driver_var = tk.StringVar(value="WinDivert 驱动: 检查中")
        self.status_label = ttk.Label(status, textvariable=self.status_var, style="Bad.TLabel")
        self.status_label.grid(row=0, column=0, sticky="w", padx=(0, 20))
        ttk.Label(status, textvariable=self.pid_var).grid(row=0, column=1, sticky="w", padx=(0, 20))
        self.driver_label = ttk.Label(status, textvariable=self.driver_var)
        self.driver_label.grid(row=0, column=2, sticky="w")
        status.columnconfigure(2, weight=1)

        rules_box = ttk.LabelFrame(self.root, text="NAT 规则", padding=(8, 6))
        rules_box.pack(fill="both", expand=False, padx=14, pady=(0, 8))
        columns = ("enabled", "name", "target", "port", "reverse_proxy", "reply")
        self.tree = ttk.Treeview(rules_box, columns=columns, show="headings", height=6, selectmode="browse")
        headings = {
            "enabled": ("启用", 60),
            "name": ("名称", 150),
            "target": ("目标地址", 160),
            "port": ("目标端口", 90),
            "reverse_proxy": ("反向代理源地址", 170),
            "reply": ("回包源地址", 150),
        }
        for key, (text, width) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(rules_box, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.bind("<Double-1>", lambda _event: self.edit_rule())

        rule_buttons = ttk.Frame(self.root, padding=(14, 0, 14, 8))
        rule_buttons.pack(fill="x")
        ttk.Button(rule_buttons, text="新增规则", command=self.add_rule, width=12).pack(side="left", padx=(0, 6))
        ttk.Button(rule_buttons, text="编辑规则", command=self.edit_rule, width=12).pack(side="left", padx=(0, 6))
        ttk.Button(rule_buttons, text="删除规则", command=self.delete_rule, width=12).pack(side="left", padx=(0, 6))
        ttk.Button(rule_buttons, text="启用/禁用", command=self.toggle_rule, width=12).pack(side="left")

        globals_box = ttk.LabelFrame(self.root, text="全局设置", padding=(12, 8))
        globals_box.pack(fill="x", padx=14, pady=(0, 8))
        globals_box.columnconfigure(1, weight=1)
        globals_box.columnconfigure(3, weight=1)
        self.var_log = tk.StringVar()
        self.var_timeout = tk.StringVar()
        ttk.Label(globals_box, text="日志文件").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(globals_box, textvariable=self.var_log).grid(row=0, column=1, sticky="ew", padx=(0, 16), pady=3)
        ttk.Label(globals_box, text="会话超时（秒）").grid(row=0, column=2, sticky="w", padx=(0, 6), pady=3)
        ttk.Entry(globals_box, textvariable=self.var_timeout, width=14).grid(row=0, column=3, sticky="w", pady=3)

        button_row = ttk.Frame(self.root, padding=(14, 0, 14, 8))
        button_row.pack(fill="x")
        self.btn_start = ttk.Button(button_row, text="启动", command=self.start_nat, width=13)
        self.btn_stop = ttk.Button(button_row, text="停止", command=self.stop_nat, width=13, state="disabled")
        self.btn_save = ttk.Button(button_row, text="重启", command=self.save_config_and_restart, width=13)
        self.btn_reload = ttk.Button(button_row, text="重载配置", command=self.reload_config, width=13)
        self.btn_open_log = ttk.Button(button_row, text="打开日志", command=self.open_log, width=13)
        self.btn_clear_log = ttk.Button(button_row, text="清空显示", command=self.clear_log_view, width=13)
        for button in (
            self.btn_start,
            self.btn_stop,
            self.btn_save,
            self.btn_reload,
            self.btn_open_log,
            self.btn_clear_log,
        ):
            button.pack(side="left", padx=(0, 6))

        log_box = ttk.LabelFrame(self.root, text="日志", padding=(8, 6))
        log_box.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.log_text = scrolledtext.ScrolledText(
            log_box, wrap="none", font=("Consolas", 9), state="disabled", height=12
        )
        self.log_text.pack(fill="both", expand=True)

    # --------------------------------------------------------------- rules
    def _refresh_rule_tree(self) -> None:
        selected = self.tree.selection()
        selected_index = None
        if selected:
            try:
                selected_index = int(selected[0])
            except ValueError:
                selected_index = None
        for item in self.tree.get_children():
            self.tree.delete(item)
        for index, rule in enumerate(self.rules):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    "是" if rule.get("enabled", True) else "否",
                    rule.get("name", ""),
                    rule.get("target_host", ""),
                    rule.get("target_port", ""),
                    rule.get("reverse_proxy_source", ""),
                    rule.get("reply_source", ""),
                ),
            )
        if selected_index is not None and 0 <= selected_index < len(self.rules):
            self.tree.selection_set(str(selected_index))

    def _selected_index(self) -> int | None:
        selected = self.tree.selection()
        if not selected:
            return None
        try:
            return int(selected[0])
        except ValueError:
            return None

    def add_rule(self) -> None:
        dialog = RuleDialog(self.root, None, self.rules)
        if dialog.result is not None:
            self.rules.append(dialog.result)
            self._refresh_rule_tree()

    def edit_rule(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        dialog = RuleDialog(self.root, self.rules[index], self.rules)
        if dialog.result is not None:
            self.rules[index] = dialog.result
            self._refresh_rule_tree()

    def delete_rule(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        if not messagebox.askyesno("确认", f"删除规则“{self.rules[index].get('name', '')}”？", parent=self.root):
            return
        del self.rules[index]
        self._refresh_rule_tree()

    def toggle_rule(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        self.rules[index]["enabled"] = not self.rules[index].get("enabled", True)
        self._refresh_rule_tree()

    # ------------------------------------------------------------- globals
    def _load_globals(self) -> None:
        self.var_log.set(str(self.config.get("log", "proxy_nat.log")))
        self.var_timeout.set(str(self.config.get("timeout", 300)))

    def _build_config(self) -> dict:
        if not self.rules:
            raise ValueError("至少需要一条 NAT 规则")
        try:
            timeout = float(self.var_timeout.get().strip())
        except ValueError:
            raise ValueError("会话超时必须是数字")
        if timeout <= 0:
            raise ValueError("会话超时必须大于 0")
        raw = {
            "log": self.var_log.get().strip() or "proxy_nat.log",
            "timeout": timeout,
            "rules": [dict(rule) for rule in self.rules],
        }
        return proxy_nat.normalize_config(raw)

    def save_config(self, show_message: bool = True, restart: bool = False) -> bool:
        try:
            config = self._build_config()
        except ValueError as exc:
            messagebox.showerror("配置错误", str(exc), parent=self.root)
            return False
        was_running = (self.proc is not None and self.proc.poll() is None) or nat_instance_running()
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as fp:
                json.dump(config, fp, indent=2, ensure_ascii=False)
                fp.write("\n")
        except OSError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.root)
            return False
        self.config = config
        self.rules = [dict(rule) for rule in config["rules"]]
        new_log_path = resolve_log_path(config)
        if new_log_path != self.log_path:
            self.log_path = new_log_path
            try:
                self.log_offset = os.path.getsize(self.log_path) if os.path.exists(self.log_path) else 0
            except OSError:
                self.log_offset = 0
        if show_message:
            self._append_log("[GUI] 配置已保存\n")
        if restart and was_running:
            self._append_log("[GUI] 配置变更，正在重启 NAT\n")
            self._restart_nat()
        return True

    def save_config_and_restart(self) -> None:
        self.save_config(show_message=True, restart=True)

    def reload_config(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            messagebox.showinfo("提示", "请先停止 NAT 再重新读取配置。", parent=self.root)
            return
        try:
            self.config = proxy_nat.load_config(CONFIG_PATH)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("读取失败", str(exc), parent=self.root)
            return
        self.rules = [dict(rule) for rule in self.config["rules"]]
        self._load_globals()
        self._refresh_rule_tree()
        new_log_path = resolve_log_path(self.config)
        if new_log_path != self.log_path:
            self.log_path = new_log_path
            try:
                self.log_offset = os.path.getsize(self.log_path) if os.path.exists(self.log_path) else 0
            except OSError:
                self.log_offset = 0
        self._append_log("[GUI] 配置已重新读取\n")

    # ---------------------------------------------------------------- NAT
    def start_nat(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        if nat_instance_running():
            messagebox.showinfo("提示", "已有 NAT 实例在运行，请先停止它。", parent=self.root)
            return
        if not is_admin():
            messagebox.showerror("权限不足", "请以管理员身份运行此 GUI。", parent=self.root)
            return
        if not self.save_config(show_message=False, restart=False):
            return
        self.log_path = resolve_log_path(self.config)
        try:
            self.stderr_file = open(self.stderr_path, "a", encoding="utf-8")
            self.proc = subprocess.Popen(
                [*NAT_COMMAND, "--config", CONFIG_PATH],
                cwd=BASE_DIR,
                stdout=subprocess.DEVNULL,
                stderr=self.stderr_file,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as exc:
            self.proc = None
            messagebox.showerror("启动失败", str(exc), parent=self.root)
            return
        self._append_log(f"[GUI] NAT 已启动，PID={self.proc.pid}\n")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.was_external = False
        self._refresh_status()

    def _restart_nat(self) -> None:
        self.stop_nat()
        self.start_nat()

    def stop_nat(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            pid = 0
            try:
                if os.path.exists(PID_PATH):
                    with open(PID_PATH, "r", encoding="ascii") as fp:
                        pid = int(fp.read().strip() or "0")
            except (OSError, ValueError):
                pid = 0
            if pid > 0 and _pid_running(pid):
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                self._append_log(f"[GUI] 已停止外部 NAT，PID={pid}\n")
            try:
                if os.path.exists(PID_PATH):
                    os.remove(PID_PATH)
            except OSError:
                pass
            self.proc = None
            self._refresh_status()
            return
        proc = self.proc
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        except OSError:
            pass
        self._append_log(f"[GUI] NAT 已停止，PID={proc.pid}\n")
        self.proc = None
        if self.stderr_file is not None:
            try:
                self.stderr_file.close()
            except OSError:
                pass
            self.stderr_file = None
        self._refresh_status()

    # -------------------------------------------------------------- status
    def _refresh_status(self) -> None:
        running = self.proc is not None and self.proc.poll() is None
        if running:
            self.status_var.set("NAT: 运行中")
            self.pid_var.set(f"PID: {self.proc.pid}")
            self.status_label.configure(style="Ok.TLabel")
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
        else:
            if self.proc is not None:
                self._append_log(f"[GUI] NAT 进程已退出，退出码={self.proc.poll()}\n")
                self.proc = None
            external = nat_instance_running()
            if external:
                self.status_var.set("NAT: 已有外部实例运行")
                self.pid_var.set("PID: 非本 GUI 管理")
                self.status_label.configure(style="Ok.TLabel")
                self.btn_start.configure(state="disabled")
                self.btn_stop.configure(state="normal")
                if not self.was_external:
                    self.was_external = True
                    self._append_log("[GUI] 检测到已有外部 NAT 实例；可点击“停止 NAT”结束它。\n")
            else:
                self.was_external = False
                self.status_var.set("NAT: 已停止")
                self.pid_var.set("PID: -")
                self.status_label.configure(style="Bad.TLabel")
                if is_admin():
                    self.btn_start.configure(state="normal")
                self.btn_stop.configure(state="disabled")

        try:
            result = subprocess.run(
                ["sc.exe", "query", "WinDivert"],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if "RUNNING" in result.stdout:
                self.driver_var.set("WinDivert 驱动: 运行中")
                self.driver_label.configure(style="Ok.TLabel")
            elif "STOPPED" in result.stdout:
                self.driver_var.set("WinDivert 驱动: 已停止")
                self.driver_label.configure(style="Bad.TLabel")
            else:
                self.driver_var.set("WinDivert 驱动: 未安装/未加载")
                self.driver_label.configure(style="Bad.TLabel")
        except Exception:
            self.driver_var.set("WinDivert 驱动: 检查失败")

        self.root.after(1000, self._refresh_status)

    # --------------------------------------------------------------- log
    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _tail_log(self) -> None:
        try:
            if os.path.exists(self.log_path):
                size = os.path.getsize(self.log_path)
                if size < self.log_offset:
                    self.log_offset = 0
                if size > self.log_offset:
                    with open(self.log_path, "r", encoding="utf-8", errors="replace") as fp:
                        fp.seek(self.log_offset)
                        data = fp.read()
                        self.log_offset = fp.tell()
                    if data:
                        self._append_log(data)
        except OSError:
            pass
        self.root.after(700, self._tail_log)

    def clear_log_view(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def open_log(self) -> None:
        self.save_config(show_message=False, restart=False)
        self.log_path = resolve_log_path(self.config)
        if not os.path.exists(self.log_path):
            try:
                open(self.log_path, "a", encoding="utf-8").close()
            except OSError as exc:
                messagebox.showerror("打开失败", str(exc), parent=self.root)
                return
        try:
            os.startfile(self.log_path)
        except OSError as exc:
            messagebox.showerror("打开失败", str(exc), parent=self.root)

    # --------------------------------------------------------------- tray
    def _create_tray(self) -> None:
        try:
            image = Image.open(resource_path(r"icons\B_bridge.png")).convert("RGBA")
            image = image.resize((64, 64), Image.Resampling.LANCZOS)
        except Exception:
            image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse((3, 3, 61, 61), fill=(24, 98, 168, 255))
            draw.rectangle((18, 18, 46, 46), fill=(255, 255, 255, 255))
            draw.rectangle((27, 12, 37, 52), fill=(24, 98, 168, 255))
            draw.rectangle((12, 27, 52, 37), fill=(24, 98, 168, 255))
        menu = pystray.Menu(
            pystray.MenuItem("显示 GUI", self._tray_show, default=True, visible=False),
            pystray.MenuItem("关闭 GUI", self._tray_hide),
            pystray.MenuItem("彻底退出", self._tray_quit),
        )
        try:
            self.tray_icon = pystray.Icon("ProxyProtocolAdapter", image, "Proxy Protocol Adapter", menu)
            self.tray_icon.run_detached()
        except Exception as exc:
            self.tray_icon = None
            self._append_log(f"[GUI] 托盘图标创建失败: {exc}\n")

    def _tray_show(self, icon=None, item=None) -> None:
        self.root.after(0, self.show_window)

    def _tray_hide(self, icon=None, item=None) -> None:
        self.root.after(0, self.hide_to_tray)

    def _tray_quit(self, icon=None, item=None) -> None:
        self.root.after(0, self.quit_app)

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_to_tray(self) -> None:
        if self.tray_icon is None:
            self.root.iconify()
            return
        self.root.withdraw()

    def quit_app(self) -> None:
        if self.proc is not None or nat_instance_running():
            self.stop_nat()
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None
        release_gui_lock()
        self.root.destroy()


def main() -> int:
    if "--run-nat" in sys.argv:
        sys.argv.remove("--run-nat")
        return proxy_nat.main()

    parser = argparse.ArgumentParser(description="GUI for proxy_nat.py")
    parser.add_argument("--no-elevate", action="store_true", help="do not request admin rights")
    args = parser.parse_args()

    if not is_admin() and not args.no_elevate:
        try:
            relaunch_as_admin()
        except SystemExit:
            return 1
        return 0

    ensure_layout()
    if not acquire_gui_lock():
        ctypes.windll.user32.MessageBoxW(
            0,
            "Proxy Protocol Adapter 已经在运行。",
            "Proxy Protocol Adapter",
            0x40,
        )
        return 1
    atexit.register(release_gui_lock)

    root = tk.Tk()
    ProxyNatGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
