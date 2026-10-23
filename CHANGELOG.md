# Changelog

## 0.1.0 (2026-10-22)

- Every graphics adapter (NVIDIA, Intel Arc, AMD and integrated): name, VRAM in use and total, how busy it is (by engine: 3D, compute, copy, video), and a 3-minute load graph.
- The programs on each GPU with their VRAM, GPU share and engines, from Windows' own performance counters (the numbers Task Manager uses). Protected processes such as the desktop window manager are named too.
- Local AI servers: asks each Ollama server what it has loaded (read-only) and places the model on the GPU its runner is using. This covers cards whose compute memory Windows can't count, such as an Intel Arc running a 20 GB model that the counters show as a few megabytes.
- Shows Windows' software renderer as its own adapter, so programs drawing with the CPU stand out.
- Window, `list` (text or JSON), `watch` and `who <program>`. Read-only: it never changes anything.
- Finds Ollama's ports on Windows in any language (listening rows are recognised by their empty remote address, not the word LISTENING), and talks to local servers directly, never through a proxy.
