"""GPU Who tests: counter readings shaped like the real ones (taken from a PC with an RTX 4070, an Arc Pro B70 and
the software renderer), fake AI servers, and the command line. One live read-only test runs on Windows."""
from __future__ import annotations

import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gpuwho import ai, cli  # noqa: E402
from gpuwho.monitor import Monitor, build, engine_family, report  # noqa: E402

NV, ARC, WARP = "luid_0x00000000_0x00012b9c", "luid_0x00000000_0x0001446f", "luid_0x00000000_0x00014093"
ADAPTERS = [{"luid": ARC, "name": "Intel(R) Arc(TM) Pro B70 Graphics", "vendor": "Intel", "vram": 32558 << 20},
            {"luid": NV, "name": "NVIDIA GeForce RTX 4070", "vendor": "NVIDIA", "vram": 12010 << 20},
            {"luid": WARP, "name": "Microsoft Basic Render Driver (software: drawn by the CPU)", "vendor": "Microsoft",
             "vram": 0, "software": True}]
TABLE = {2920: ("dwm.exe", 900), 46724: ("ollama.exe", 1), 44160: ("llama-server.exe", 46724),
         5000: ("blender.exe", 1), 40360: ("Rainmeter.exe", 1), 7000: ("notepad.exe", 1)}
MB = 1 << 20


def counters(blender_3d: float = 62.5) -> dict:
    return {
        "adapter_dedicated": {f"{NV}_phys_0": 4200 * MB, f"{ARC}_phys_0": 0.0, f"{WARP}_phys_0": 0.0},
        "adapter_shared": {f"{NV}_phys_0": 40 * MB, f"{ARC}_phys_0": 415 * MB},
        "process_dedicated": {f"pid_2920_{NV}_phys_0": 700 * MB, f"pid_5000_{NV}_phys_0": 3300 * MB,
                              f"pid_44160_{ARC}_phys_0": 0.8 * MB, f"pid_44160_{NV}_phys_0": 0.0,
                              f"pid_7000_{NV}_phys_0": 0.2 * MB},
        "process_shared": {f"pid_44160_{ARC}_phys_0": 2 * MB},
        "engine": {f"pid_5000_{NV}_phys_0_eng_0_engtype_3d": blender_3d,
                   f"pid_2920_{NV}_phys_0_eng_0_engtype_3d": 3.0,
                   f"pid_5000_{NV}_phys_0_eng_9_engtype_copy": 7.0,
                   f"pid_5000_{NV}_phys_0_eng_2_engtype_videodecode": 0.0,
                   f"pid_44160_{ARC}_phys_0_eng_5_engtype_compute_0": 0.0,
                   f"pid_40360_{WARP}_phys_0_eng_0_engtype_3d": 4.0,
                   "nonsense_instance": 99.0},
    }


SERVER = [{"server_pid": 46724, "port": 11435, "runner_pids": [44160],
           "models": [{"name": "qwen3.5:35b-a3b", "bytes": 23400 * MB, "vram_bytes": 22300 * MB}]}]


class BuildTests(unittest.TestCase):
    def test_gpus_programs_and_load(self) -> None:
        gpus = {g.luid: g for g in build(counters(), ADAPTERS, TABLE)}
        nv = gpus[NV]
        self.assertEqual([p.name for p in nv.processes], ["blender.exe", "dwm.exe"])  # notepad's 0.2 MB is left out
        self.assertEqual((nv.load, nv.engines["3D"], nv.engines["Copy"], nv.engines["Video decode"]), (65.5, 65.5, 7.0, 0.0))
        blender = nv.processes[0]
        self.assertEqual((blender.label, blender.dedicated, blender.load), ("Blender", 3300 * MB, 62.5))
        self.assertEqual(nv.processes[1].label, "Windows desktop")
        self.assertAlmostEqual(nv.vram_share, 4200 / 12010)
        warp = gpus[WARP]
        self.assertTrue(warp.software)
        self.assertEqual(([p.name for p in warp.processes], warp.load), (["Rainmeter.exe"], 4.0))
        self.assertEqual(list(gpus), [ARC, NV, WARP])  # biggest first, the software renderer last

    def test_ai_server_memory_windows_cannot_see(self) -> None:
        gpus = {g.luid: g for g in build(counters(), ADAPTERS, TABLE, SERVER)}
        arc = gpus[ARC]
        self.assertEqual([r["app"] for r in arc.reported], ["Ollama on port 11435"])
        self.assertTrue(arc.uncounted)
        self.assertEqual(arc.in_use, 22300 * MB)
        self.assertEqual([p.label for p in arc.processes], ["Ollama model runner"])  # shown despite 0.8 MB
        self.assertFalse(gpus[NV].reported)
        text = report(list(gpus.values()))
        self.assertIn("Intel(R) Arc(TM) Pro B70 Graphics: 21.8 GB of 31.8 GB VRAM in use", text)
        self.assertIn("Ollama on port 11435 reports qwen3.5:35b-a3b using 21.8 GB here", text)
        self.assertIn("Windows' counters don't include that memory", text)

    def test_show_all_and_engine_names(self) -> None:
        nv = {g.luid: g for g in build(counters(), ADAPTERS, TABLE, show_all=True)}[NV]
        self.assertIn("notepad.exe", [p.name for p in nv.processes])
        self.assertEqual([engine_family(x) for x in ("3D", "compute_1", "VideoEncode", "LegacyOverlay")],
                         ["3D", "Compute", "Video encode", "Legacyoverlay"])


class AiTests(unittest.TestCase):
    def test_find_follows_the_server_to_its_runner(self) -> None:
        netstat = ("  TCP    127.0.0.1:11435        0.0.0.0:0              LISTENING       46724\n"
                   "  TCP    127.0.0.1:11436        0.0.0.0:0              ABHÖREN         46724\n"  # German Windows
                   "  TCP    127.0.0.1:50000        127.0.0.1:11435        ESTABLISHED     46724\n"
                   "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       900\n").encode()
        ports = ai.listening_ports({46724}, runner=lambda *a, **k: mock.Mock(stdout=netstat))
        self.assertEqual(ports, {46724: [11435, 11436]})
        payload = json.dumps({"models": [{"name": "qwen", "size": 10, "size_vram": 8}]}).encode()
        self.assertEqual(ai.loaded_models(11435, fetch=lambda url: payload),
                         [{"name": "qwen", "bytes": 10, "vram_bytes": 8}])
        self.assertEqual(ai.loaded_models(1, fetch=lambda url: b"<html>not ollama</html>"), [])
        found = ai.find(TABLE, ports=lambda pids: {46724: [11435]},
                        models=lambda port: [{"name": "qwen", "bytes": 10, "vram_bytes": 8}])
        self.assertEqual(found[0]["runner_pids"], [44160])
        self.assertEqual(ai.find(TABLE, ports=lambda pids: {46724: [11435]}, models=lambda port: []), [])


class FakeSource:
    def __init__(self) -> None:
        self.readings = iter([counters(10.0), counters(40.0), counters(90.0)])
        self.closed = False

    def read(self):
        return next(self.readings)

    def close(self):
        self.closed = True


class MonitorAndCliTests(unittest.TestCase):
    def monitor(self) -> Monitor:
        return Monitor(FakeSource(), ADAPTERS, table=lambda: TABLE, find_ai=lambda table: SERVER)

    def test_history_and_ai_refresh(self) -> None:
        m = self.monitor()
        m.snapshot(wait_for_ai=True)
        m.snapshot()
        loads = [round(load, 1) for _, load, _ in m.history[NV]]
        self.assertEqual(loads, [13.0, 43.0])
        self.assertEqual(m.reported, SERVER)

    def test_cli(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("time.sleep"):
            self.assertEqual(cli.main(["list", "--json"], monitor_factory=self.monitor), 0)
        data = json.loads(out.getvalue())
        self.assertEqual((data[0]["name"], data[0]["uncounted"]), ("Intel(R) Arc(TM) Pro B70 Graphics", True))
        out = io.StringIO()
        with mock.patch("sys.stdout", out), mock.patch("time.sleep"):
            self.assertEqual(cli.main(["who", "blender"], monitor_factory=self.monitor), 0)
            self.assertEqual(cli.main(["who", "notepad"], monitor_factory=self.monitor), 1)
        self.assertIn("blender.exe [pid 5000] on NVIDIA GeForce RTX 4070: 3.2 GB VRAM, 3D 10%", out.getvalue())
        self.assertIn("Running but not using a GPU right now: notepad.exe [pid 7000]", out.getvalue())


@unittest.skipUnless(os.name == "nt", "reads Windows' own counters")
class LiveTests(unittest.TestCase):
    def test_reads_this_pc(self) -> None:
        from gpuwho import winapi
        adapters = winapi.adapters()
        self.assertTrue(adapters)
        self.assertTrue(all(a["luid"].startswith("luid_0x") for a in adapters))
        table = winapi.process_table()
        self.assertEqual(table[os.getpid()][0].lower(), Path(sys.executable).name.lower())
        source = winapi.Counters()
        try:
            data = source.read()
        finally:
            source.close()
        self.assertTrue(data["adapter_dedicated"])


if __name__ == "__main__":
    unittest.main()
