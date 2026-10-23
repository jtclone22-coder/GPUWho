"""Command line: gui (default), list, watch, who."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__
from .monitor import Monitor, as_json, gib, report


def first_snapshot(monitor: Monitor, show_all: bool = False):
    time.sleep(1.0)  # GPU load is a rate: it needs two readings a moment apart
    return monitor.snapshot(show_all, wait_for_ai=True)


def cmd_list(args, monitor: Monitor) -> int:
    gpus = first_snapshot(monitor, args.all)
    print(json.dumps(as_json(gpus), indent=2) if args.json else report(gpus))
    return 0


def cmd_watch(args, monitor: Monitor) -> int:
    first_snapshot(monitor)
    try:
        while True:
            text = report(monitor.snapshot(args.all))
            os.system("cls" if os.name == "nt" else "clear")
            print(time.strftime("%H:%M:%S") + "  (Ctrl+C to stop)\n\n" + text, flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_who(args, monitor: Monitor) -> int:
    wanted = args.program.lower()
    found, idle = False, set()
    for g in first_snapshot(monitor, show_all=True):
        for p in g.processes:
            if not (wanted == str(p.pid) or wanted in p.name.lower() or (p.label and wanted in p.label.lower())):
                continue
            if p.dedicated < (1 << 20) and p.load < 0.5:
                idle.add(f"{p.name} [pid {p.pid}]")
                continue
            found = True
            engines = ", ".join(f"{k} {v:.0f}%" for k, v in p.engines.items() if v >= 1) or "idle"
            print(f"{p.name} [pid {p.pid}] on {g.name}: {gib(p.dedicated)} VRAM, {engines}")
        for r in g.reported:
            if wanted in r["app"].lower() or wanted in r["name"].lower() or wanted in "ollama":
                found = True
                print(f"{r['app']}: {r['name']} on {g.name}, {gib(r['vram_bytes'])} (as the server reports it)")
    if not found:
        print(f"No GPU use found for {args.program!r}." + (
            f" Running but not using a GPU right now: {', '.join(sorted(idle))}." if idle else ""))
    return 0 if found else 1


def cmd_gui(args, monitor: Monitor) -> int:
    from .gui import main as gui_main
    return gui_main(monitor)


def main(argv=None, monitor_factory=Monitor) -> int:
    parser = argparse.ArgumentParser(prog="gpuwho", description="Which program is using which GPU, and how much.")
    parser.add_argument("--version", action="version", version=f"GPU Who {__version__}")
    sub = parser.add_subparsers(dest="command")
    p = sub.add_parser("list", help="one reading of every GPU and the programs on it")
    p.add_argument("--all", action="store_true", help="include programs using under 1 MB")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)
    p = sub.add_parser("watch", help="keep the list up to date in this window")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_watch)
    p = sub.add_parser("who", help="which GPU a program is on (name, part of a name, or PID)")
    p.add_argument("program")
    p.set_defaults(func=cmd_who)
    sub.add_parser("gui", help="open the window").set_defaults(func=cmd_gui)
    args = parser.parse_args(argv)
    try:
        monitor = monitor_factory()
    except OSError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        return (args.func if args.command else cmd_gui)(args, monitor)
    finally:
        monitor.close()
