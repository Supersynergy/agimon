"""Network monitoring — SSH tunnels, services, external connections."""
from __future__ import annotations
import subprocess
from dataclasses import dataclass

# Known port labels for identification
PORT_LABELS: dict[int, str] = {
    22: "SSH", 53: "DNS", 80: "HTTP", 443: "HTTPS",
    993: "IMAP/SSL", 143: "IMAP", 587: "SMTP",
    3000: "Gitea/Dev", 3030: "Grafana",
    4222: "NATS", 5432: "PostgreSQL", 5433: "PostgreSQL-Alt",
    5434: "PostgreSQL-Alt2", 6333: "Qdrant-HTTP", 6334: "Qdrant-gRPC",
    6379: "Redis", 6380: "Redis-Alt", 6381: "Redis-Alt2",
    6390: "Redis-Alt3", 7265: "Raycast", 7777: "Dev-Server",
    8100: "API-Service", 8108: "Typesense", 8222: "NATS-Monitor",
    9001: "Minio-Console", 9002: "Minio-S3", 9222: "Chrome-Debug",
    11434: "Ollama", 14000: "Custom-Tunnel", 15432: "PG-Tunnel",
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
    direction: str = ""  # "listen", "out", "in"
    label: str = ""


def _label_port(port: int) -> str:
    """Get human-readable label for a port."""
    return PORT_LABELS.get(port, "")


def get_listening_services() -> list[NetworkConnection]:
    """Get all listening services via lsof."""
    conns = []
    try:
        out = subprocess.run(
            ["lsof", "-i", "-nP", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return conns

    seen: set[tuple[str, int]] = set()
    for line in out.splitlines()[1:]:  # skip header
        cols = line.split()
        if len(cols) < 9:
            continue
        process = cols[0]
        pid = int(cols[1]) if cols[1].isdigit() else 0
        addr_part = cols[8]

        # Parse address:port
        if ":" in addr_part:
            parts = addr_part.rsplit(":", 1)
            local_addr = parts[0]
            local_port = int(parts[1]) if parts[1].isdigit() else 0
        else:
            continue

        key = (process, local_port)
        if key in seen:
            continue
        seen.add(key)

        label = _label_port(local_port)
        conns.append(NetworkConnection(
            process=process, pid=pid,
            local_addr=local_addr, local_port=local_port,
            state="LISTEN", direction="listen",
            label=label,
        ))

    conns.sort(key=lambda c: c.local_port)
    return conns


def get_ssh_tunnels() -> list[NetworkConnection]:
    """Parse SSH tunnel forwards from lsof on SSH process."""
    tunnels = []
    try:
        out = subprocess.run(
            ["lsof", "-i", "-nP", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return tunnels

    seen: set[int] = set()
    for line in out.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 9:
            continue
        if cols[0] != "ssh":
            continue

        addr_part = cols[8]
        if ":" not in addr_part:
            continue
        parts = addr_part.rsplit(":", 1)
        port = int(parts[1]) if parts[1].isdigit() else 0
        if port in seen:
            continue
        seen.add(port)

        label = _label_port(port)
        tunnels.append(NetworkConnection(
            process="ssh", pid=int(cols[1]) if cols[1].isdigit() else 0,
            local_addr=parts[0], local_port=port,
            state="TUNNEL", direction="listen",
            label=label or f"SSH-Tunnel:{port}",
        ))

    tunnels.sort(key=lambda c: c.local_port)
    return tunnels


def get_external_connections() -> list[NetworkConnection]:
    """Get outbound connections to external IPs."""
    conns = []
    try:
        out = subprocess.run(
            ["lsof", "-i", "-nP", "-sTCP:ESTABLISHED"],
            capture_output=True, text=True, timeout=10
        ).stdout
    except Exception:
        return conns

    seen: set[tuple[str, str]] = set()
    for line in out.splitlines()[1:]:
        cols = line.split()
        if len(cols) < 9:
            continue

        process = cols[0]
        pid = int(cols[1]) if cols[1].isdigit() else 0
        addr_part = cols[8]

        if "->" not in addr_part:
            continue

        local, remote = addr_part.split("->")
        # Skip localhost connections
        if remote.startswith("127.") or remote.startswith("[::1]"):
            continue

        key = (process, remote)
        if key in seen:
            continue
        seen.add(key)

        remote_parts = remote.rsplit(":", 1)
        remote_addr = remote_parts[0]
        remote_port = int(remote_parts[1]) if len(remote_parts) > 1 and remote_parts[1].isdigit() else 0

        label = _label_port(remote_port)
        conns.append(NetworkConnection(
            process=process, pid=pid,
            local_addr=local,
            remote_addr=remote_addr, remote_port=remote_port,
            state="ESTABLISHED", direction="out",
            label=label,
        ))

    return conns


def get_network_summary() -> dict:
    """Full network overview."""
    listeners = get_listening_services()
    tunnels = get_ssh_tunnels()
    external = get_external_connections()
    return {
        "listeners": listeners,
        "tunnels": tunnels,
        "external": external,
        "total_listeners": len(listeners),
        "total_tunnels": len(tunnels),
        "total_external": len(external),
    }
