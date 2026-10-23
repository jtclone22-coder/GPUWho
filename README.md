# GPU Who

**Which program is using which GPU, and how much VRAM?** Task Manager shows totals per GPU. GPU Who shows the programs behind them, on every card at once. It's made for PCs with more than one GPU, like a gaming card next to an AI card, or an NVIDIA card next to an Intel Arc.

- **Every adapter, any vendor:** NVIDIA, Intel Arc, AMD and integrated graphics, with VRAM used out of total, how busy each one is (3D, compute, copy, video), and a 3-minute load graph.
- **Every program on each GPU:** its VRAM, its share of the GPU and which engines it uses, from Windows' own performance counters (the same numbers Task Manager uses). Protected processes such as the desktop window manager are named too.
- **Local AI that Windows can't see:** some compute paths, for example Ollama on an Intel Arc card, allocate memory that Windows' counters never show. A 20 GB model can look like 1 MB. GPU Who asks your local Ollama servers what they've loaded (read-only), follows each server to its model runner, and puts the model on the right GPU with a clear note.
- **The software renderer is listed too,** so a program secretly drawing with your CPU stands out.
- **Read-only.** GPU Who never changes a setting, never stops a program and never loads a model.

## Download

Get **GPUWho-0.1.0-windows.zip** from [Releases](../../releases/latest), unzip it anywhere and double-click **GPUWho.exe** in the GPUWho folder. Nothing to install, and no Python needed.

Windows may say "Windows protected your PC" because the app is new: click **More info**, then **Run anyway**.

Prefer Python? With Python 3.9 or newer, download this repository and run `py -m gpuwho` in its folder.

## Command line

```
py -m gpuwho                 # the window
py -m gpuwho list            # one reading, as text
py -m gpuwho list --json     # ...or JSON for scripts
py -m gpuwho watch           # keeps updating in the console
py -m gpuwho who blender     # which GPU is Blender on?
py -m gpuwho who ollama
```

Example from a PC with an Arc Pro B70 for AI and an RTX 4070 for everything else:

```
Intel(R) Arc(TM) Pro B70 Graphics: 21.8 GB of 31.8 GB VRAM in use, 0% busy
  Ollama on port 11435 reports qwen3.5:35b-a3b using 21.8 GB here
  (Windows' counters don't include that memory; the total above uses the server's figure)
      1 MB    0%  llama-server.exe (Ollama model runner) [pid 44160]

NVIDIA GeForce RTX 4070: 1.5 GB of 11.7 GB VRAM in use, 0% busy
    0.7 GB    0%  dwm.exe (Windows desktop) [pid 2920]
    ...
```

Needs Windows 10 (1709) or newer. Nothing to install with the .exe; the Python version needs Python 3.9+ only.

## Tests

```
py -m unittest discover -s tests
```

The tests use counter readings shaped like real ones, fake AI servers, and one live read-only reading of the PC they run on.

MIT License.

## More tools

Made by [jtclone22-coder](https://github.com/jtclone22-coder): more free Windows tools are on that profile, and paid ones (DevSweep, Overnight, AssetAtlas) are on [Gumroad](https://clonemaster152.gumroad.com).
