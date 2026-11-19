# Compute network dashboard — compact second-screen view

Branch: `architecture-modernization`

## Available now

Open **Compute dashboard...** from the AI Assist sidebar or Settings menu. The window has a **fixed, no-scroll, responsive 3-over-2 panel layout** designed for small external screens (default 800 × 480, minimum window size 480 × 320).

- **Top row:** live local CPU, GPU, and RAM gauges, 30-sample history traces and short hardware details.
- **Bottom row:** locally installed/loaded Ollama models, plus saved paired-device identities.
- **Header:** manual Refresh and Full screen; `F11` enters/exits fullscreen and `Esc` exits fullscreen. `Ctrl+R` refreshes.
- **Footer:** local-only privacy status and measurement age; metrics older than 15 seconds are marked stale.

The layout uses Tkinter Canvas rendering: all panels remain in the current viewport, and long names are shortened instead of creating scrolling regions. GPU load and memory stats come from `nvidia-smi` where available; otherwise some GPU capacity data may be available but utilization is marked unknown. CPU and RAM readings are collected per platform. The Ollama model endpoint is queried over **loopback only**. Polling occurs on a worker thread approximately every 5 seconds.

## Security boundaries

The UI **does not** start LAN discovery, expose a listener, enable network sharing or poll remote nodes. Paired devices are shown as **saved, telemetry unavailable**, never guessed as online. No tokens, certificate secrets, prompts, or file contents appear in device rows. The manual-sharing policy remains in force: sharing is **off by default** and must be activated explicitly in a later integration.

## Files

- `compute_dashboard.py`: graphical fixed-grid Canvas view and pure `dashboard_layout` geometry.
- `compute_telemetry.py`: read-only local hardware/model sampler.
- `workspace_ui.py`: sidebar and menu entry points, plus shutdown cleanup.
- `test_compute_dashboard.py`: formatting, privacy, and local-only sampling tests.
- `test_compute_dashboard_layout.py`: headless layout, rendering, missing-data, and no-scroll regression tests.

## Validate on your machine

From the project folder on this branch:

```sh
python -m unittest -v test_compute_dashboard test_compute_dashboard_layout
python -m unittest discover -v
python AIassist.py
```

In AI Assist, click **Compute dashboard...** and use `F11` to view it on the second screen. Verify at 480 × 320, 800 × 480, and fullscreen resolution, including font scaling on Windows/macOS/Linux.

**Verification status:** Source and regression tests are committed. Full suite execution and cross-platform visual validation have **not** been performed in this session.

## Still missing

- Authenticated live remote telemetry, session throughput and cross-device load.
- Actual distributed inference and hardware routing controls in the GUI.
- More than the first GPU's graphical gauge (other GPUs can be added later via selection).
- True GPU utilization on systems that lack `nvidia-smi`, including Apple Metal-specific GPU usage.
- Hardware-specific fullscreen checks, readable text at 480 × 320 on HiDPI scaled displays, and deployment/testing on the actual second screen.

The graphical view deliberately avoids fake readings and remains useful while the network features are unfinished.
