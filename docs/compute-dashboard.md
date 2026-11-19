# Compute network dashboard — initial implementation

Branch: \`architecture-modernization\`.

## Delivered

The existing Tkinter app now exposes **Compute dashboard...** from the left sidebar and Settings menu. The independent window refreshes on a background thread, with a five-second polling target and a manual refresh button. It closes on application shutdown.

The UI reports:
- Local CPU name, logical cores, and utilization where OS counters are accessible.
- Installed and available system RAM.
- NVIDIA GPU load and used/total VRAM via nvidia-smi where available; other GPU detections provide capacity estimates only, with utilization explicitly **unavailable**.
- Installed and loaded models from a **loopback-only** Ollama server. It never probes configured remote Ollama servers in the local telemetry sampler.
- Locally stored paired-device metadata, with each peer explicitly labeled **paired · live telemetry unavailable**, never falsely shown as online or producing actual performance data.

It never starts LAN sharing, initiates discovery, or downloads a model. It does not transmit prompts or files. No secrets are shown in the paired-device inventory.

**Security note:** The enrolled-peer metadata is loaded only from the default \`pairing\` directory next to the chat data directory. A user who chose a different \`--data-dir\` for the pairing CLI won't see those peers until a proper device-registry configuration is added. A pairing record does not establish online presence or authenticated telemetry permission.

## Architecture

- \`compute_telemetry.py\`: read-only metrics sampler independent of Tkinter.
- \`compute_dashboard.py\`: separate Tkinter view, worker-thread polling and pure formatting.
- \`workspace_ui.py\`: small sidebar and settings hooks, one dashboard instance per application, safe close handling.
- \`test_compute_dashboard.py\`: headless unit tests for formatting, privacy boundaries and local-only model polling.

The existing \`status_bar.py\`, \`model_advisor.py\`, and \`hardware_panel.py\` are reused or left intact; this does not alter the inference path.

## Run

From the architecture branch:

\`\`\`bash
python -m unittest -v test_compute_dashboard test_compute_routing test_peer_pairing test_sharing_control
python -m unittest discover -v
python AIassist.py
\`\`\`

Open **Compute dashboard...** in the left sidebar.

**Validation status:** The code is committed but the full test suite and GUI have not been run on supported operating systems in this development session. Unit tests in \`test_peer_pairing.py\` include optional cryptography-dependent tests.

## Still required for a fully live network dashboard

1. Add an inference-only **authenticated telemetry endpoint** to the explicitly enabled paired-node service. Require TLS certificate pinning, token verification, size limits, read-only permission and user opt-in.
2. Define versioned, minimal telemetry JSON with timestamp, CPU, RAM, GPU, loaded models, queue load and measured inference throughput. Never include prompts, local paths, private keys, access tokens or arbitrary shell/file capabilities.
3. Display sampled metrics only after verifying node identity and authorization. Mark disconnected/stale nodes accurately. Add timeouts, authentication failure handling and revocation.
4. Build responsive charts for histories and per-model benchmarks; current view is live text metrics, not a high-frequency time-series visualization.
5. Add real two-device validation on Windows/macOS/Linux, with explicit consent controls and a local-only baseline.

Keep LAN inference hosting **manually activated** and **off by default** throughout.

## Open product question

Should the next milestone prioritize authenticated remote telemetry, or an enhanced, no-scroll graphical dashboard for a small second screen? Neither should create an automatic sharing listener.
