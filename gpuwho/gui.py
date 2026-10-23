"""Window: one row per GPU (VRAM bar, load bar, a 3-minute load graph) and the programs on the selected GPU."""
from __future__ import annotations

from . import __version__
from .monitor import gib, report

BAR_W, GRAPH_W, ROW_H = 220, 180, 54


def main(monitor) -> int:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title(f"GPU Who {__version__}")
    root.geometry("1040x640")
    state = {"gpus": [], "selected": None}
    show_all = tk.BooleanVar(value=False)
    interval = tk.StringVar(value="2")

    bar = ttk.Frame(root, padding=(10, 8))
    bar.pack(fill="x")
    ttk.Label(bar, text="Every").pack(side="left")
    ttk.Combobox(bar, textvariable=interval, values=("1", "2", "5", "10"), width=4, state="readonly").pack(side="left", padx=4)
    ttk.Label(bar, text="seconds").pack(side="left")
    ttk.Checkbutton(bar, text="Show programs using under 1 MB", variable=show_all).pack(side="left", padx=16)
    copy_button = ttk.Button(bar, text="Copy report")
    copy_button.pack(side="right")

    overview = tk.Canvas(root, height=ROW_H, highlightthickness=0, background=root.cget("background"))
    overview.pack(fill="x", padx=10)
    note = ttk.Label(root, text="", foreground="#92400e", padding=(10, 2), wraplength=1000)
    note.pack(fill="x")
    columns = ("program", "what", "pid", "vram", "load", "engines")
    tree = ttk.Treeview(root, columns=columns, show="headings")
    for column, title, width, anchor in (("program", "Program", 230, "w"), ("what", "Usually", 160, "w"),
                                         ("pid", "PID", 70, "e"), ("vram", "VRAM", 90, "e"), ("load", "GPU %", 70, "e"),
                                         ("engines", "Engines", 300, "w")):
        tree.heading(column, text=title)
        tree.column(column, width=width, anchor=anchor, stretch=column == "engines")
    tree.pack(fill="both", expand=True, padx=10, pady=(4, 10))
    status = ttk.Label(root, text="Reading the GPUs...", anchor="w", padding=(10, 0, 10, 6), foreground="#6b7280")
    status.pack(fill="x")

    def bar_color(share: float) -> str:
        return "#15803d" if share < 0.7 else "#b45309" if share < 0.9 else "#b91c1c"

    def draw() -> None:
        overview.delete("all")
        gpus = state["gpus"]
        overview.configure(height=max(ROW_H, ROW_H * len(gpus)))
        for row, g in enumerate(gpus):
            y = row * ROW_H
            selected = g.luid == state["selected"]
            if selected:
                overview.create_rectangle(0, y, 2000, y + ROW_H - 4, fill="#dbeafe", width=0)
            overview.create_text(8, y + 14, text=g.name, anchor="w", font=("Segoe UI", 10, "bold"))
            total = f" of {gib(g.vram)}" if g.vram else ""
            overview.create_text(8, y + 34, anchor="w", font=("Segoe UI", 9), fill="#374151",
                                 text=f"{gib(g.in_use)}{total} VRAM" + ("  (from Ollama)" if g.uncounted else ""))
            x = 430
            overview.create_rectangle(x, y + 8, x + BAR_W, y + 20, outline="#9ca3af")
            overview.create_rectangle(x, y + 8, x + BAR_W * g.vram_share, y + 20, fill=bar_color(g.vram_share), width=0)
            overview.create_text(x + BAR_W + 6, y + 14, text=f"VRAM {g.vram_share:.0%}", anchor="w", font=("Segoe UI", 8))
            overview.create_rectangle(x, y + 28, x + BAR_W, y + 40, outline="#9ca3af")
            overview.create_rectangle(x, y + 28, x + BAR_W * g.load / 100, y + 40, fill="#1d4ed8", width=0)
            overview.create_text(x + BAR_W + 6, y + 34, text=f"busy {g.load:.0f}%", anchor="w", font=("Segoe UI", 8))
            points = monitor.history.get(g.luid, [])
            gx = x + BAR_W + 90
            overview.create_rectangle(gx, y + 6, gx + GRAPH_W, y + 42, outline="#d1d5db")
            if len(points) > 1:
                step = GRAPH_W / max(1, monitor.keep - 1)
                start = gx + GRAPH_W - step * (len(points) - 1)
                line = []
                for i, (_, load, _) in enumerate(points):
                    line += [start + i * step, y + 42 - 36 * load / 100]
                overview.create_line(*line, fill="#1d4ed8")

    def fill_tree() -> None:
        tree.delete(*tree.get_children())
        g = next((g for g in state["gpus"] if g.luid == state["selected"]), None)
        notes = []
        if g:
            for r in g.reported:
                notes.append(f"{r['app']} reports {r['name']} using {gib(r['vram_bytes'])} on this GPU.")
            if g.uncounted:
                notes.append("Windows' counters don't include that memory, so the VRAM total uses the server's figure.")
            if g.software:
                notes.append("This is Windows' software renderer: programs listed here are drawing with the CPU.")
            for p in g.processes:
                engines = ", ".join(f"{k} {v:.0f}%" for k, v in sorted(p.engines.items(), key=lambda kv: -kv[1])
                                    if v >= 0.5)
                tree.insert("", "end", values=(p.name, p.label, p.pid, gib(p.dedicated), f"{p.load:.0f}", engines))
        note.configure(text="  ".join(notes))

    def pick(event) -> None:
        row = int(event.y // ROW_H)
        if 0 <= row < len(state["gpus"]):
            state["selected"] = state["gpus"][row].luid
            draw()
            fill_tree()

    def copy() -> None:
        root.clipboard_clear()
        root.clipboard_append(report(state["gpus"]))
        status.configure(text="Report copied.")

    def refresh() -> None:
        try:
            state["gpus"] = monitor.snapshot(show_all.get())
            if state["gpus"] and state["selected"] not in {g.luid for g in state["gpus"]}:
                state["selected"] = state["gpus"][0].luid
            draw()
            fill_tree()
            status.configure(text="Click a GPU to see the programs on it. Nothing is changed; GPU Who only reads.")
        except OSError as exc:
            status.configure(text=f"Couldn't read the GPUs: {exc}")
        root.after(int(float(interval.get()) * 1000), refresh)

    overview.bind("<Button-1>", pick)
    copy_button.configure(command=copy)
    root.after(1000, refresh)
    root.mainloop()
    return 0
