#!/usr/bin/env python3
"""WinDivert transparent NAT for frp PROXY v2 -> one or more UDP services.

The program must run elevated because WinDivert loads a kernel driver.

Topology:

    frpc -> UDP 127.0.0.1:<target_port> with PROXY v2 headers
                |
                v
        WinDivert NAT
          - header-only datagram: parse real IP, remember mapping, drop
          - payload datagram: rewrite source to real IP:port, inject
          - target reply to real IP: rewrite destination back to frpc
                |
                v
        target UDP service

Multiple independent target ports are supported through the "rules" list in
proxy_nat.json. The target service sees the real client address directly.
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import time

try:
    import pydivert
except ImportError:
    print("pydivert is not installed. Run: python -m pip install pydivert", file=sys.stderr)
    raise SystemExit(2)

try:
    from proxy_protocol import parse_proxy_v2
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from proxy_protocol import parse_proxy_v2


APP_DIR = (
    os.path.dirname(os.path.abspath(sys.executable))
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.abspath(__file__))
)

CONFIG_DIR = os.path.join(APP_DIR, "config")
LOG_DIR = os.path.join(APP_DIR, "logs")
CONFIG_PATH = os.path.join(CONFIG_DIR, "proxy_nat.json")
PID_PATH = os.path.join(LOG_DIR, "proxy_nat.pid")

DEFAULT_CONFIG = {
    "log": "logs/proxy_nat.log",
    "timeout": 300.0,
    "rules": [
        {
            "name": "NAT 1",
            "enabled": True,
            "target_host": "127.0.0.1",
            "target_port": 27021,
            "reverse_proxy_source": "127.0.0.1",
            "reply_source": "127.0.0.1",
        }
    ],
}


def now_text() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _pid_running(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
    kernel32.CloseHandle.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True
    return False


def acquire_instance_lock() -> bool:
    os.makedirs(LOG_DIR, exist_ok=True)
    try:
        if os.path.exists(PID_PATH):
            with open(PID_PATH, "r", encoding="ascii") as fp:
                old_pid = int(fp.read().strip() or "0")
            if old_pid > 0 and _pid_running(old_pid):
                return False
    except (OSError, ValueError):
        pass
    with open(PID_PATH, "w", encoding="ascii") as fp:
        fp.write(str(os.getpid()))
    return True


WINDIVERT_SERVICE = "WinDivert"
WINDIVERT_STABLE_SYS = os.path.join(
    os.environ.get("SystemRoot", r"C:\Windows"),
    "System32",
    "drivers",
    "WinDivert64.sys",
)


def _sc(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["sc.exe", *args],
        capture_output=True,
        text=True,
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _service_binary_path() -> str | None:
    result = _sc("qc", WINDIVERT_SERVICE)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        if "BINARY_PATH_NAME" in line:
            path = line.split(":", 1)[1].strip()
            if path.startswith("\\??\\"):
                path = path[4:]
            return path
    return None


def ensure_windivert_driver() -> tuple[bool, str]:
    """Install/repair WinDivert from the bundled pydivert driver if needed."""
    try:
        import pydivert
    except ImportError:
        return False, "pydivert 未安装"

    bundled_sys = os.path.join(
        os.path.dirname(pydivert.__file__),
        "windivert_dll",
        "WinDivert64.sys",
    )
    if not os.path.exists(bundled_sys):
        return False, f"找不到 WinDivert64.sys：{bundled_sys}（可能被杀毒软件删除）"

    existing_path = _service_binary_path()
    if existing_path and os.path.exists(existing_path):
        return True, f"使用已安装的 WinDivert 驱动：{existing_path}"

    if existing_path:
        _sc("stop", WINDIVERT_SERVICE)
        _sc("delete", WINDIVERT_SERVICE)

    try:
        os.makedirs(os.path.dirname(WINDIVERT_STABLE_SYS), exist_ok=True)
        if os.path.abspath(bundled_sys) != os.path.abspath(WINDIVERT_STABLE_SYS):
            shutil.copy2(bundled_sys, WINDIVERT_STABLE_SYS)
    except OSError as exc:
        return False, f"复制 WinDivert64.sys 失败：{exc}"

    result = _sc(
        "create",
        WINDIVERT_SERVICE,
        "type=",
        "kernel",
        "start=",
        "demand",
        "error=",
        "normal",
        "binPath=",
        "\\??\\" + WINDIVERT_STABLE_SYS,
        "DisplayName=",
        "WinDivert",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        return False, f"创建 WinDivert 驱动服务失败：{detail}"
    return True, f"已安装 WinDivert 驱动：{WINDIVERT_STABLE_SYS}"


def _normalize_rule(raw: dict, index: int) -> dict:
    try:
        target_port = int(raw.get("target_port", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"规则 {index}: target_port 无效") from exc
    if not 1 <= target_port <= 65535:
        raise ValueError(f"规则 {index}: target_port 必须在 1-65535 之间")
    return {
        "name": str(raw.get("name") or f"NAT {index + 1}"),
        "enabled": bool(raw.get("enabled", True)),
        "target_host": str(raw.get("target_host") or "127.0.0.1"),
        "target_port": target_port,
        "reverse_proxy_source": str(
            raw.get("reverse_proxy_source") or "127.0.0.1"
        ),
        "reply_source": str(raw.get("reply_source") or "127.0.0.1"),
    }


def normalize_config(raw: dict) -> dict:
    log_path = str(raw.get("log") or "logs/proxy_nat.log")
    try:
        timeout = float(raw.get("timeout", 300.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout 无效") from exc

    raw_rules = raw.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise ValueError("rules 必须是非空列表")
    rules = [_normalize_rule(rule, index) for index, rule in enumerate(raw_rules) if isinstance(rule, dict)]
    if not rules:
        raise ValueError("至少需要一条 NAT 规则")
    return {"log": log_path, "timeout": max(1.0, timeout), "rules": rules}


def load_config(path: str) -> dict:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(DEFAULT_CONFIG, fp, indent=2, ensure_ascii=False)
            fp.write("\n")
        return normalize_config(DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8") as fp:
        return normalize_config(json.load(fp))


class ProxyNat:
    def __init__(self, rules: list[dict], log_path: str, timeout: float):
        self.rules = [rule for rule in rules if rule.get("enabled", True)]
        self.all_rules = rules
        self.log_path = log_path
        self.timeout = timeout

        # frp endpoint -> (real_ip, real_port, rule)
        self.forward: dict[tuple[str, int], tuple[str, int, dict]] = {}
        # (target_port, real_ip, real_port) -> (frp endpoint, rule)
        self.reverse: dict[tuple[int, str, int], tuple[tuple[str, int], dict]] = {}
        self.last_seen: dict[tuple[str, int], float] = {}

        filter_parts: list[str] = []
        for rule in self.rules:
            port = rule["target_port"]
            filter_parts.append(
                f"(udp and udp.DstPort == {port} and ip.SrcAddr == {rule['reverse_proxy_source']})"
            )
            filter_parts.append(
                f"(udp and udp.SrcPort == {port} and ip.DstAddr != {rule['reply_source']})"
            )
        self.filter = " or ".join(filter_parts)

    def log(self, message: str) -> None:
        line = f"{now_text()} {message}"
        print(line, flush=True)
        try:
            with open(self.log_path, "a", encoding="utf-8") as fp:
                fp.write(line + "\n")
        except OSError as exc:
            print(f"warning: cannot write log: {exc}", file=sys.stderr, flush=True)

    def _touch(self, endpoint: tuple[str, int]) -> None:
        self.last_seen[endpoint] = time.monotonic()

    def _inbound_rule(self, packet) -> dict | None:
        udp = packet.udp
        for rule in self.rules:
            if udp.dst_port == rule["target_port"] and packet.src_addr == rule["reverse_proxy_source"]:
                return rule
        return None

    def _handle_inbound(self, wd: pydivert.WinDivert, packet, rule: dict) -> None:
        payload = bytes(packet.payload)
        proxy = parse_proxy_v2(payload)
        endpoint = (packet.src_addr, packet.src_port)

        if proxy is None:
            session = self.forward.get(endpoint)
            if session is None:
                wd.send(packet)
                return
            real_ip, real_port, session_rule = session
            packet.src_addr, packet.src_port = real_ip, real_port
            self._touch(endpoint)
            wd.send(packet, recalculate_checksum=True)
            return

        if not proxy.get("complete") or proxy.get("source") is None:
            return

        real = (proxy["source"], proxy["source_port"])
        header_length = proxy["header_length"]

        if len(payload) <= header_length:
            self.forward[endpoint] = (real[0], real[1], rule)
            self.reverse[(rule["target_port"], real[0], real[1])] = (endpoint, rule)
            self._touch(endpoint)
            self.log(
                f"[{rule['name']}] header endpoint={endpoint[0]}:{endpoint[1]} real={real[0]}:{real[1]} dropped"
            )
            return

        packet.payload = payload[header_length:]
        packet.src_addr, packet.src_port = real
        self.forward[endpoint] = (real[0], real[1], rule)
        self.reverse[(rule["target_port"], real[0], real[1])] = (endpoint, rule)
        self._touch(endpoint)
        self.log(
            f"[{rule['name']}] header+payload endpoint={endpoint[0]}:{endpoint[1]} real={real[0]}:{real[1]}"
        )
        wd.send(packet, recalculate_checksum=True)

    def _handle_outbound(self, wd: pydivert.WinDivert, packet) -> None:
        key = (packet.src_port, packet.dst_addr, packet.dst_port)
        session = self.reverse.get(key)
        if session is None:
            wd.send(packet)
            return
        endpoint, rule = session
        packet.dst_addr, packet.dst_port = endpoint
        packet.src_addr = rule["reply_source"]
        self._touch(endpoint)
        wd.send(packet, recalculate_checksum=True)

    def _cleanup(self) -> None:
        now = time.monotonic()
        expired = [
            endpoint
            for endpoint, seen in self.last_seen.items()
            if now - seen > self.timeout
        ]
        for endpoint in expired:
            session = self.forward.pop(endpoint, None)
            if session is not None:
                real_ip, real_port, rule = session
                self.reverse.pop((rule["target_port"], real_ip, real_port), None)
            self.last_seen.pop(endpoint, None)
            self.log(f"expired endpoint={endpoint[0]}:{endpoint[1]}")

    def run(self) -> None:
        if not self.rules:
            self.log("no enabled NAT rules; exiting")
            return
        self.log(f"WinDivert NAT filter: {self.filter}")
        driver_ok, driver_message = ensure_windivert_driver()
        self.log(driver_message)
        if not driver_ok:
            return
        try:
            with pydivert.WinDivert(self.filter) as wd:
                self.log("WinDivert opened; waiting for packets")
                last_cleanup = time.monotonic()
                while True:
                    packet = wd.recv()
                    try:
                        rule = self._inbound_rule(packet)
                        if rule is not None:
                            self._handle_inbound(wd, packet, rule)
                        else:
                            self._handle_outbound(wd, packet)
                    except Exception as exc:
                        self.log(f"packet error: {type(exc).__name__}: {exc}")
                    if time.monotonic() - last_cleanup > 5:
                        last_cleanup = time.monotonic()
                        self._cleanup()
        except FileNotFoundError as exc:
            self.log(f"WinDivert 驱动加载失败：{exc}")
            self.log("请检查杀毒软件是否拦截或删除了 WinDivert64.sys")
        except KeyboardInterrupt:
            self.log("stopped")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=CONFIG_PATH)
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"invalid config: {exc}", file=sys.stderr)
        return 2

    if not acquire_instance_lock():
        print("another proxy_nat.py instance is already running", file=sys.stderr)
        return 3

    log_path = config["log"]
    if not os.path.isabs(log_path):
        log_path = os.path.join(APP_DIR, log_path)

    relay = ProxyNat(config["rules"], log_path, config["timeout"])
    relay.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
