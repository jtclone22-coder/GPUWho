"""What local AI servers say they've loaded, for the cases Windows can't count.

Some compute paths (for example Ollama on an Intel Arc card) allocate GPU memory that Windows' counters never
see, so a 20 GB model shows up as a few megabytes. GPU Who asks each local Ollama server (read-only:
GET /api/ps) what it has loaded, follows the server to its runner process, and puts the model on the GPU that
runner is using.
"""
from __future__ import annotations

import json
import subprocess
import urllib.request

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never send 127.0.0.1 requests to a proxy
SERVERS = {"ollama.exe"}
RUNNERS = {"llama-server.exe", "ollama_llama_server.exe", "ollama.exe"}


def listening_ports(pids: set[int], runner=subprocess.run) -> dict[int, list[int]]:
    """{pid: [ports]} for TCP ports these processes listen on, from netstat."""
    if not pids:
        return {}
    done = runner(["netstat", "-ano", "-p", "TCP"], capture_output=True, timeout=20, creationflags=NO_WINDOW)
    ports: dict[int, list[int]] = {}
    for line in done.stdout.decode("utf-8", "replace").splitlines():
        parts = line.split()
        # listening rows have no remote end ("LISTENING" itself is translated on non-English Windows)
        if len(parts) == 5 and parts[2] in ("0.0.0.0:0", "[::]:0") and parts[4].isdigit() and int(parts[4]) in pids:
            port = parts[1].rsplit(":", 1)[-1]
            if port.isdigit():
                ports.setdefault(int(parts[4]), []).append(int(port))
    return ports


def loaded_models(port: int, fetch=None, timeout: float = 2.0) -> list[dict]:
    """What an Ollama server has in memory: [{'name', 'bytes', 'vram_bytes'}]. [] if it isn't Ollama."""
    def default_fetch(url: str) -> bytes:
        with DIRECT.open(url, timeout=timeout) as response:
            return response.read()
    try:
        data = json.loads((fetch or default_fetch)(f"http://127.0.0.1:{port}/api/ps"))
    except (OSError, ValueError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    return [{"name": str(m.get("name", "?")), "bytes": int(m.get("size", 0) or 0),
             "vram_bytes": int(m.get("size_vram", 0) or 0)} for m in models or [] if isinstance(m, dict)]


def find(table: dict[int, tuple[str, int]], ports=listening_ports, models=loaded_models) -> list[dict]:
    """[{'server_pid', 'port', 'runner_pids', 'models'}] for every local Ollama server with something loaded."""
    servers = {pid for pid, (name, _) in table.items() if name.lower() in SERVERS}
    found = []
    for pid, port_list in sorted(ports(servers).items()):
        for port in sorted(set(port_list)):
            loaded = models(port)
            if not loaded:
                continue
            children = [child for child, (name, parent) in table.items()
                        if parent == pid and name.lower() in RUNNERS]
            found.append({"server_pid": pid, "port": port, "runner_pids": children or [pid], "models": loaded})
            break  # one answering port per server is enough
    return found
