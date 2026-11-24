# Compute dashboard

Open Compute in the desktop app to see local CPU, GPU, and RAM readings,
installed and loaded Ollama models, and paired computers. The separate Compute
window can stay open on a second display. Press `F11` to toggle fullscreen,
`Esc` to exit fullscreen, and `Ctrl+R` or `Cmd+R` to refresh the display.

The app samples local hardware approximately every five seconds while a
Compute view is active. Missing hardware readings appear as unavailable.
The Ollama telemetry sampler uses a loopback endpoint.
When `nvidia-smi` is available, the GPU panel shows NVIDIA utilization,
VRAM, temperature, and power draw as reported by the driver. The GPU face
of the cube shows utilization and temperature.

Use **Network → LAN pairing** in Mission Control to enable pairing on the
host or pair from the second computer. Paste the host's displayed address,
compare the certificate code on the host, then enter its PIN. Approve the
connection in Mission Control when prompted.

LAN discovery starts when you click Scan. Paired computers show live readings
when the peer has enabled usage sharing and the authenticated request succeeds.
Sharing starts off each time the app runs.

The app remembers the separate window's normal size and position when it
moves, resizes, or closes. Fullscreen geometry is ignored.

## Implementation

- `compute_telemetry.py` samples local hardware and model state.
- `app_core/compute_values.py` formats telemetry and paired computer rows.
- `webui/compute.py` publishes snapshots to active views.
- `webui/static/compute.js` renders the shared dashboard.
- `webui/static/compute-window.js` handles the separate window, including its
  network panel and fullscreen controls.

Run the headless Compute and frontend checks from the repository root.

```sh
python -m unittest test_compute_dashboard test_webui_compute test_webui_static
```
