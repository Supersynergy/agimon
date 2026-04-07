"""Network monitoring — single lsof call, parsed efficiently."""
from __future__ import annotations
import subprocess
from dataclasses import dataclass

PORT_LABELS: dict[int, str] = {
    22: "SSH", 53: "DNS", 80: "HTTP", 443: "HTTPS",
    993: "IMAP/SSL", 143: "IMAP", 587: "SMTP",
    3000: "Gitea", 3030: "Grafana",
    4222: "NATS", 5432: "PostgreSQL", 5433: "PG-Alt",
    5434: "PG-Alt2", 6333: "Qdrant-HTTP", 6334: "Qdrant-gRPC",
    6379: "Redis", 6380: "Redis-Alt", 6381: "Redis-Alt2",
    6390: "Redis-Alt3", 7265: "Raycast", 7777: "SuperJarvis",
    8100: "API", 8108: "Typesense", 8222: "NATS-Mon",
    9001: "Minio", 9002: "Minio-S3", 9222: "Chrome-Debug",
    11434: "Ollama", 14000: "Custom", 15432: "PG-Tunnel",
    16379: "Redis-Tunnel", 49998: "Dolt",
}


@dataclass
class NetworkConnection:
    process: str = ""
    pid: int = 0
    local_addr: str = ""
    local_port: int = 0
    remote_addr: str = ""
    remote_port: int = 0
    state: str = ""
    direction: str = ""
    label: str = ""


def _parse_port(addr_str: str) -> tuple[str, int]:
    """Parse 'addr:port' or '*:port'."""
    if ":" not in addr_str:
        return addr_str, 0
    parts = addr_str.rsplit(":", 1)
    port = int(parts[1]) if parts[1].isdigit() else 0
    return parts[0], port


# Single lsof call, parse everything at once
_cache: dict | None = None
_cache_time: float = 0


def _get_raw_lsof() -> str:
    """Single lsof call — cached for 2 seconds."""
    import time
    global _cache, _cache_time
    now = time.monotonic()
    if _cache is not None and now - _cache_time < 2.0:
        return _cache
    try:
        result = subprocess.run(
            ["lsof", "-i", "-nP"],
            capture_output=True, text=True, timeout=10,
        )
        _cache = result.stdout
        _cache_time = now
        return _cache
    except Exception:
        return ""


def _parse_all() -> tuple[list[NetworkConnection], list[NetworkConnection], list[NetworkConnection]]:
    """Parse single lsof output into tunnels, listeners, external."""
    raw = _get_raw_lsof()
    tunnels, listeners, external = [], [], []
    seen_listen: set[tuple[str, int]] = set()
    seen_ext: set[tuple[str, str]] = set()

    for line in raw.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 9:
            continue

        process = cols[0]
        pid = int(cols[1]) if cols[1].isdigit() else 0
        addr_part = cols[8]
        state = cols[9] if len(cols) > 9 else ""

        if "LISTEN" in state or "(LISTEN)" in addr_part:
            addr, port = _parse_port(addr_part.replace("(LISTEN)", ""))
            key = (process, port)
            if key in seen_listen:
                continue
            seen_listen.add(key)
            label = PORT_LABELS.get(port, "")
            conn = NetworkConnection(
                process=process, pid=pid,
                local_addr=addr, local_port=port,
                state="LISTEN", direction="listen", label=label,
            )
            if process == "ssh":
                conn.state = "TUNNEL"
                conn.label = label or f"SSH-Tunnel:{port}"
                tunnels.append(conn)
            else:
                listeners.append(conn)

        elif "->" in addr_part and ("ESTABLISHED" in state or "ESTABLISHED" in addr_part):
            local, remote = addr_part.split("->")
            if remote.startswith("127.") or remote.startswith("[::1]"):
                continue
            key = (process, remote)
            if key in seen_ext:
                continue
            seen_ext.add(key)
            r_addr, r_port = _parse_port(remote)
            external.append(NetworkConnection(
                process=process, pid=pid,
                local_addr=local, remote_addr=r_addr,
                remote_port=r_port, state="ESTABLISHED",
                direction="out", label=PORT_LABELS.get(r_port, ""),
            ))

    tunnels.sort(key=lambda c: c.local_port)
    listeners.sort(key=lambda c: c.local_port)
    return tunnels, listeners, external


def get_ssh_tunnels() -> list[NetworkConnection]:
    return _parse_all()[0]


def get_listening_services() -> list[NetworkConnection]:
    t, l, _ = _parse_all()
    return t + l  # tunnels are also listeners


def get_external_connections() -> list[NetworkConnection]:
    return _parse_all()[2]


def get_network_summary() -> dict:
    tunnels, listeners, external = _parse_all()
    return {
        "listeners": listeners,
        "tunnels": tunnels,
        "external": external,
        "total_listeners": len(listeners),
        "total_tunnels": len(tunnels),
        "total_external": len(external),
    }
