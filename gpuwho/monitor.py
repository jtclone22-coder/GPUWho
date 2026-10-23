"""Turn raw counters into a picture: each GPU, its VRAM and load, and the programs using it."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import asdict, dataclass, field

ENGINE = re.compile(r"pid_(\d+)_(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+_eng_(\d+)_engtype_(.*)")
PROCESS = re.compile(r"pid_(\d+)_(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+")
ADAPTER = re.compile(r"(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys_\d+")
LABELS = {
    "ollama.exe": "Ollama", "ollama_llama_server.exe": "Ollama model runner", "llama-server.exe": "llama.cpp server",
    "llama-cli.exe": "llama.cpp", "lm studio.exe": "LM Studio", "lms.exe": "LM Studio", "koboldcpp.exe": "KoboldCpp",
    "python.exe": "Python", "pythonw.exe": "Python", "blender.exe": "Blender", "unrealeditor.exe": "Unreal Editor",
    "unity.exe": "Unity", "godot.exe": "Godot", "dwm.exe": "Windows desktop", "explorer.exe": "Explorer",
    "wallpaper64.exe": "Wallpaper Engine", "wallpaper32.exe": "Wallpaper Engine", "chrome.exe": "Chrome",
    "msedge.exe": "Edge", "firefox.exe": "Firefox", "msedgewebview2.exe": "an app's web view", "code.exe": "VS Code",
    "obs64.exe": "OBS", "steamwebhelper.exe": "Steam", "discord.exe": "Discord", "claude.exe": "Claude",
    "rainmeter.exe": "Rainmeter", "icue.exe": "Corsair iCUE", "nvcontainer.exe": "NVIDIA service",
    "comfy desktop.exe": "ComfyUI", "invokeai.exe": "InvokeAI", "searchhost.exe": "Windows Search",
    "textinputhost.exe": "Windows text input", "shellexperiencehost.exe": "Windows shell",
    "startmenuexperiencehost.exe": "Start menu", "widgets.exe": "Widgets", "phoneexperiencehost.exe": "Phone Link",
}
AI_PROCESSES = {"ollama.exe", "ollama_llama_server.exe", "llama-server.exe", "llama-cli.exe", "koboldcpp.exe",
                "lm studio.exe", "lms.exe"}
MIN_BYTES = 1 << 20  # programs using under 1 MB and no GPU time are left out unless asked for (AI servers never)


def engine_family(engine_type: str) -> str:
    kind = engine_type.lower()
    for prefix, label in (("3d", "3D"), ("compute", "Compute"), ("copy", "Copy"), ("videodecode", "Video decode"),
                          ("videoencode", "Video encode"), ("videoprocessing", "Video"), ("video", "Video")):
        if kind.startswith(prefix):
            return label
    return engine_type.title() or "Other"


@dataclass
class ProcessUse:
    pid: int
    name: str
    label: str
    dedicated: float = 0.0
    shared: float = 0.0
    load: float = 0.0
    engines: dict = field(default_factory=dict)


@dataclass
class GpuUse:
    luid: str
    name: str
    vendor: str
    vram: float
    software: bool = False
    dedicated: float = 0.0
    shared: float = 0.0
    load: float = 0.0
    engines: dict = field(default_factory=dict)
    processes: list = field(default_factory=list)
    reported: list = field(default_factory=list)   # what AI servers say they've loaded on this GPU

    @property
    def reported_bytes(self) -> float:
        return float(sum(r["vram_bytes"] for r in self.reported))

    @property
    def uncounted(self) -> bool:
        """True when an AI server reports more memory here than Windows' counters show."""
        return self.reported_bytes > self.dedicated + (256 << 20)

    @property
    def in_use(self) -> float:
        return max(self.dedicated, self.reported_bytes)

    @property
    def vram_share(self) -> float:
        return min(1.0, self.in_use / self.vram) if self.vram else 0.0


def label_for(name: str, parent: str) -> str:
    lowered = name.lower()
    if lowered in ("llama-server.exe", "ollama_llama_server.exe") and parent.lower() == "ollama.exe":
        return "Ollama model runner"
    return LABELS.get(lowered, "")


def build(counters: dict, adapters: list[dict], table: dict[int, tuple[str, int]], reported: list[dict] = (),
          show_all: bool = False) -> list[GpuUse]:
    """counters: {counter: {instance: value}}; table: {pid: (name, parent pid)}; reported: ai.find() output."""
    gpus = {a["luid"]: GpuUse(a["luid"], a["name"], a["vendor"], float(a["vram"]), bool(a.get("software")))
            for a in adapters}
    processes: dict[tuple[str, int], ProcessUse] = {}

    def gpu(luid: str) -> GpuUse:
        if luid not in gpus:
            gpus[luid] = GpuUse(luid, "Other graphics adapter", "", 0.0)
        return gpus[luid]

    def process(luid: str, pid: int) -> ProcessUse:
        key = (luid, pid)
        if key not in processes:
            name, parent = table.get(pid, ("", 0))
            name = name or ("System" if pid in (0, 4) else f"process {pid}")
            processes[key] = ProcessUse(pid, name, label_for(name, table.get(parent, ("", 0))[0]))
        return processes[key]

    for kind, attribute in (("adapter_dedicated", "dedicated"), ("adapter_shared", "shared")):
        for instance, value in counters.get(kind, {}).items():
            match = ADAPTER.fullmatch(instance)
            if match:
                g = gpu(match.group(1))
                setattr(g, attribute, getattr(g, attribute) + value)
    for kind, attribute in (("process_dedicated", "dedicated"), ("process_shared", "shared")):
        for instance, value in counters.get(kind, {}).items():
            match = PROCESS.fullmatch(instance)
            if match:
                use = process(match.group(2), int(match.group(1)))
                setattr(use, attribute, getattr(use, attribute) + value)
    engine_totals: dict[tuple[str, str], list] = {}
    for instance, value in counters.get("engine", {}).items():
        match = ENGINE.fullmatch(instance)
        if not match:
            continue
        pid, luid, engine, family = int(match.group(1)), match.group(2), match.group(3), engine_family(match.group(4))
        total = engine_totals.setdefault((luid, engine), [family, 0.0])
        total[1] += value
        gpu(luid).engines.setdefault(family, 0.0)
        if value > 0:
            use = process(luid, pid)
            use.engines[family] = min(100.0, use.engines.get(family, 0.0) + value)
            use.load = max(use.load, use.engines[family])
    for (luid, _), (family, total) in engine_totals.items():  # like Task Manager: as busy as the busiest engine
        g = gpus[luid]
        g.engines[family] = min(100.0, max(g.engines.get(family, 0.0), total))
        g.load = max(g.load, min(100.0, total))
    holders = set()  # (luid, pid) of runners holding a model an AI server reports
    for server in reported:  # put each AI server's models on the GPU its runner process is using
        candidates = [(processes[(luid, pid)].dedicated + processes[(luid, pid)].shared + processes[(luid, pid)].load,
                       luid) for (luid, pid) in processes if pid in server["runner_pids"] and not gpus[luid].software]
        if not candidates:
            continue
        luid = max(candidates)[1]
        holders.update((luid, pid) for pid in server["runner_pids"])
        for model in server["models"]:
            gpus[luid].reported.append(dict(model, app=f"Ollama on port {server['port']}"))
    for (luid, pid), use in processes.items():
        # AI servers count even when tiny: their real memory may be invisible to Windows (see ai.py)
        ai_here = use.name.lower() in AI_PROCESSES and (use.dedicated + use.shared >= 512 << 10 or
                                                         (luid, pid) in holders)
        if show_all or use.dedicated >= MIN_BYTES or use.load >= 0.5 or ai_here:
            gpus[luid].processes.append(use)
    for g in gpus.values():
        g.processes.sort(key=lambda p: (-p.dedicated, -p.load, p.name.lower()))
    shown = [g for g in gpus.values() if g.vram or g.dedicated or g.processes or g.load]
    return sorted(shown, key=lambda g: (g.software, -g.vram))


class Monitor:
    """Keeps the counter query open between reads; asks AI servers every 15 s, in the background."""

    def __init__(self, source=None, adapters=None, table=None, find_ai=None, history: int = 90,
                 ai_every: float = 15.0) -> None:
        if source is None:
            from . import ai, winapi
            source = winapi.Counters()
            adapters = adapters if adapters is not None else winapi.adapters()
            table = table or winapi.process_table
            find_ai = find_ai or ai.find
        self.source, self.adapters = source, adapters or []
        self.table = table or (lambda: {})
        self.find_ai = find_ai
        self.reported: list[dict] = []
        self.ai_every, self.ai_checked, self.ai_running = ai_every, 0.0, False
        self.history: dict[str, list[tuple[float, float, float]]] = {}
        self.keep = history

    def _refresh_ai(self, table) -> None:
        try:
            self.reported = self.find_ai(table)
        except Exception:  # an AI server misbehaving must never break the GPU view
            self.reported = []
        finally:
            self.ai_running = False

    def snapshot(self, show_all: bool = False, wait_for_ai: bool = False) -> list[GpuUse]:
        table = self.table()
        if self.find_ai and not self.ai_running and time.time() - self.ai_checked >= self.ai_every:
            self.ai_checked, self.ai_running = time.time(), True
            if wait_for_ai:
                self._refresh_ai(table)
            else:
                threading.Thread(target=self._refresh_ai, args=(table,), daemon=True, name="gpuwho-ai").start()
        gpus = build(self.source.read(), self.adapters, table, self.reported, show_all)
        now = time.time()
        for g in gpus:
            points = self.history.setdefault(g.luid, [])
            points.append((now, g.load, g.vram_share))
            del points[:-self.keep]
        return gpus

    def close(self) -> None:
        close = getattr(self.source, "close", None)
        if close:
            close()


def gib(value: float) -> str:
    return f"{value / 2**30:.1f} GB" if value >= 2**30 * 0.1 else f"{value / 2**20:.0f} MB"


def report(gpus: list[GpuUse]) -> str:
    """Plain text, for the console or to paste into a forum post."""
    lines = []
    for g in gpus:
        size = f" of {gib(g.vram)}" if g.vram else ""
        lines.append(f"{g.name}: {gib(g.in_use)}{size} VRAM in use, {g.load:.0f}% busy")
        busy = ", ".join(f"{k} {v:.0f}%" for k, v in sorted(g.engines.items(), key=lambda kv: -kv[1]) if v >= 1)
        if busy:
            lines.append(f"  engines: {busy}")
        for r in g.reported:
            lines.append(f"  {r['app']} reports {r['name']} using {gib(r['vram_bytes'])} here")
        if g.uncounted:
            lines.append("  (Windows' counters don't include that memory; the total above uses the server's figure)")
        for p in g.processes:
            label = f" ({p.label})" if p.label and p.label.lower() not in p.name.lower() else ""
            engines = ", ".join(f"{k} {v:.0f}%" for k, v in p.engines.items() if v >= 1)
            lines.append(f"  {gib(p.dedicated):>8}  {p.load:3.0f}%  {p.name}{label} [pid {p.pid}]"
                         + (f"  {engines}" if engines else ""))
        lines.append("")
    return "\n".join(lines).rstrip()


def as_json(gpus: list[GpuUse]) -> list[dict]:
    return [dict(asdict(g), vram_share=g.vram_share, in_use=g.in_use, uncounted=g.uncounted) for g in gpus]
