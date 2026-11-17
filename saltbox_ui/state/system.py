"""What the server is doing: resource use, Tailscale, backups.

Everything here reads state; nothing changes the host. Commands run through a
replaceable runner so tests (and the e2e box) can stand in for docker and
tailscale.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

Runner = Callable[[list[str]], tuple[int, str]]

UNITS = {
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
    "tb": 1000**4,
    "tib": 1024**4,
}


def run(args: list[str], timeout: float = 20) -> tuple[int, str]:
    if shutil.which(args[0]) is None:
        return 127, f"{args[0]} is not installed"
    try:
        done = subprocess.run(args, capture_output=True, text=True, check=False, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        return 127, str(error)
    return done.returncode, done.stdout if done.returncode == 0 else done.stderr


def parse_size(text: str) -> int:
    """``"120.5MiB"`` → bytes; 0 for anything unreadable."""
    match = re.fullmatch(r"\s*([\d.]+)\s*([A-Za-z]*)\s*", text)
    if match is None:
        return 0
    number, unit = match.groups()
    try:
        return int(float(number) * UNITS.get(unit.lower() or "b", 1))
    except ValueError:
        return 0


def parse_percent(text: str) -> float:
    try:
        return round(float(text.strip().rstrip("%")), 2)
    except ValueError:
        return 0.0


# -- resources ---------------------------------------------------------------------------
def container_stats(runner: Runner | None = None) -> list[dict[str, Any]]:
    """CPU and memory per running container, busiest first."""
    code, output = (runner or run)(["docker", "stats", "--no-stream", "--format", "{{json .}}"])
    if code != 0:
        raise RuntimeError(output.strip() or "docker stats failed")
    stats = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
        except ValueError:
            continue
        used, _, limit = str(item.get("MemUsage", "")).partition("/")
        stats.append(
            {
                "name": str(item.get("Name", "")),
                "cpu_percent": parse_percent(str(item.get("CPUPerc", "0"))),
                "memory": parse_size(used),
                "memory_limit": parse_size(limit),
                "memory_percent": parse_percent(str(item.get("MemPerc", "0"))),
                "pids": int(str(item.get("PIDs", "0")) or 0)
                if str(item.get("PIDs", "0")).isdigit()
                else 0,
            }
        )
    return sorted(stats, key=lambda s: (-s["cpu_percent"], -s["memory"]))


# -- CPU -----------------------------------------------------------------------------------
# How busy the processors are comes from /proc/stat, as the share of time between two readings
# spent on work. The load average is something else: it also counts processes waiting on disks
# and network mounts, so a server with a slow drive or a busy rclone mount shows a load far
# above its core count while its CPUs sit idle. Time spent waiting on disks (iowait) is given
# on its own, because that is what such a load is made of.
CPU_SAMPLE_SECONDS = 0.25
CPU_SAMPLE_MAX_AGE = 60.0  # an older reading is too stale to measure from; take two fresh ones
_cpu_lock = threading.Lock()
_cpu_last: dict[Path, tuple[float, list[int]]] = {}


def cpu_times(proc: Path = Path("/proc")) -> list[int]:
    """user, nice, system, idle, iowait, irq, softirq, steal (in clock ticks, since boot)."""
    for line in (proc / "stat").read_text().splitlines():
        fields = line.split()
        if fields and fields[0] == "cpu":
            ticks = [int(x) for x in fields[1:9]]
            return ticks + [0] * (8 - len(ticks))
    raise ValueError("no cpu line in /proc/stat")


def cpu_use(before: list[int], after: list[int]) -> tuple[float, float] | None:
    """Percent of the time between two readings spent working, and spent waiting on disks."""
    delta = [max(0, a - b) for a, b in zip(after, before, strict=True)]
    total = sum(delta)
    if total == 0:
        return None
    idle, iowait = delta[3], delta[4]
    busy = total - idle - iowait
    return round(busy / total * 100, 1), round(iowait / total * 100, 1)


def cpu_percent(
    proc: Path = Path("/proc"),
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[float, float] | None:
    """CPU use since the last time this was asked (or over a quarter second, the first time)."""
    with _cpu_lock:
        now = cpu_times(proc)
        last = _cpu_last.get(proc)
        if last is None or clock() - last[0] > CPU_SAMPLE_MAX_AGE:
            sleep(CPU_SAMPLE_SECONDS)
            before, now = now, cpu_times(proc)
        else:
            before = last[1]
        _cpu_last[proc] = (clock(), now)
        return cpu_use(before, now)


def host_stats(
    proc: Path = Path("/proc"), sleep: Callable[[float], None] = time.sleep
) -> dict[str, Any]:
    """CPU use, load, memory and uptime from /proc."""
    cores = os.cpu_count() or 1
    result: dict[str, Any] = {"cores": cores}
    try:
        use = cpu_percent(proc, sleep)
    except (OSError, ValueError):
        use = None
    result["cpu_percent"], result["iowait_percent"] = use if use is not None else (None, None)
    try:
        load = [float(x) for x in (proc / "loadavg").read_text().split()[:3]]
        result["load"] = load
        # Load per core, for reference; it is not how busy the CPUs are (see above).
        result["load_percent"] = round(min(100.0, load[0] / cores * 100), 1)
    except (OSError, ValueError, IndexError):
        result["load"], result["load_percent"] = [], None
    try:
        meminfo = dict(
            (line.split(":")[0], int(line.split()[1]) * 1024)
            for line in (proc / "meminfo").read_text().splitlines()
            if len(line.split()) >= 2 and line.split()[1].isdigit()
        )
        total, available = meminfo.get("MemTotal", 0), meminfo.get("MemAvailable", 0)
        result["memory_total"] = total
        result["memory_used"] = total - available
        result["memory_percent"] = round((total - available) / total * 100, 1) if total else None
        swap_total = meminfo.get("SwapTotal", 0)
        result["swap_used"] = swap_total - meminfo.get("SwapFree", 0)
        result["swap_total"] = swap_total
    except (OSError, ValueError):
        result["memory_total"] = result["memory_used"] = 0
        result["memory_percent"] = None
    result["uptime"] = uptime_seconds(proc)
    return result


def uptime_seconds(proc: Path = Path("/proc")) -> int | None:
    """Seconds since the server booted."""
    try:
        return int(float((proc / "uptime").read_text().split()[0]))
    except (OSError, ValueError, IndexError):
        return None


# -- tailscale ---------------------------------------------------------------------------------
def tailscale_status(runner: Runner | None = None) -> dict[str, Any]:
    """This node on the tailnet: whether it is up, its name and addresses, who owns it."""
    code, output = (runner or run)(["tailscale", "status", "--json"])
    if code == 127:
        return {"installed": False}
    if code != 0 and not output.strip().startswith("{"):
        return {"installed": True, "running": False, "error": output.strip()[:300]}
    try:
        data = json.loads(output)
    except ValueError:
        return {"installed": True, "running": False, "error": "unreadable tailscale status"}
    me = data.get("Self") or {}
    users = data.get("User") or {}
    owner = users.get(str(me.get("UserID"))) or {}
    peers = list((data.get("Peer") or {}).values())
    dns_name = str(me.get("DNSName") or "").rstrip(".")
    return {
        "installed": True,
        "running": data.get("BackendState") == "Running",
        "state": data.get("BackendState", ""),
        "hostname": me.get("HostName", ""),
        "dns_name": dns_name,
        "addresses": list(me.get("TailscaleIPs") or []),
        "online": bool(me.get("Online")),
        "owner": owner.get("LoginName", ""),
        "peers": len(peers),
        "peers_online": sum(1 for p in peers if p.get("Online")),
        "version": data.get("Version", ""),
        "magic_dns": bool((data.get("CurrentTailnet") or {}).get("MagicDNSEnabled")),
    }


SERVE_PORT = 8443


def tailscale_serve_status(socket_path: str, runner: Runner | None = None) -> dict[str, Any]:
    """Whether ``tailscale serve`` proxies to the daemon, and the address it serves on."""
    code, output = (runner or run)(["tailscale", "serve", "status", "--json"])
    if code != 0:
        return {"enabled": False, "error": output.strip()[:300]}
    try:
        data = json.loads(output or "{}") or {}
    except ValueError:
        return {"enabled": False, "error": "unreadable tailscale serve status"}
    for host_port, config in (data.get("Web") or {}).items():
        for handler in ((config or {}).get("Handlers") or {}).values():
            if (handler or {}).get("Proxy") == f"unix:{socket_path}":
                address = host_port[:-4] if host_port.endswith(":443") else host_port
                return {"enabled": True, "url": f"https://{address}/"}
    return {"enabled": False}


def tailscale_serve(
    socket_path: str, enable: bool, runner: Runner | None = None
) -> tuple[bool, str]:
    """Turn the tailnet address for the web app on or off (HTTPS on port 8443)."""
    if enable:
        args = ["tailscale", "serve", "--bg", f"--https={SERVE_PORT}", f"unix:{socket_path}"]
    else:
        args = ["tailscale", "serve", f"--https={SERVE_PORT}", "off"]
    code, output = (runner or run)(args)
    return code == 0, output.strip()[-500:]


# -- backups ------------------------------------------------------------------------------------
def backup_summary(config: dict[str, Any], user: str) -> dict[str, Any]:
    """What backup_config.yml asks for, and the newest backup in the local destination."""
    backup = (config or {}).get("backup") or {}

    def section(name: str) -> dict[str, Any]:
        value = backup.get(name) or {}
        return value if isinstance(value, dict) else {}

    local = section("local")
    destination = str(local.get("destination") or "").replace("{{ user.name }}", user)
    latest = newest_file(Path(destination)) if destination and _enabled(local) else None
    return {
        "configured": bool(backup),
        "schedule": str(section("cron").get("cron_time") or ""),
        "destinations": [
            {
                "kind": kind,
                "enabled": _enabled(section(kind)),
                "destination": str(section(kind).get("destination") or "").replace(
                    "{{ user.name }}", user
                ),
            }
            for kind in ("local", "rclone", "rsync")
            if section(kind)
        ],
        "latest": latest,
    }


def _enabled(section: dict[str, Any]) -> bool:
    value = section.get("enable")
    return value is True or str(value).lower() in {"true", "yes", "1"}


def newest_file(directory: Path, depth: int = 2) -> dict[str, Any] | None:
    """The most recently modified file under ``directory`` (two levels down)."""
    best: tuple[float, Path, int] | None = None
    try:
        candidates = [p for p in directory.glob("*") if p.is_file()]
        if depth > 1:
            candidates += [p for p in directory.glob("*/*") if p.is_file()]
    except OSError:
        return None
    for path in candidates:
        try:
            info = path.stat()
        except OSError:
            continue
        if best is None or info.st_mtime > best[0]:
            best = (info.st_mtime, path, info.st_size)
    if best is None:
        return None
    return {
        "path": str(best[1]),
        "modified": best[0],
        "size": best[2],
        "age_seconds": int(time.time() - best[0]),
    }
