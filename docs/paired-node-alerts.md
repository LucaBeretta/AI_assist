# Mac notification when paired Windows compute node is ready

The Mac's graphical compute dashboard has an **ALERTS OFF** button (macOS only).
Click it to enable notifications for that session. Click **ALERTS ON** to stop.
Alerts also stop when the dashboard closes. Nothing launches automatically.

## What the alert means

When the Windows RTX workstation's **manually activated inference service**
becomes available again, AI Assist checks the **entire pinned TLS certificate**
fingerprint and the original pairing token over HTTPS before considering it online.

After two successful 15-second checks, macOS displays a notification:
**"AI Assist · PC disponibile"**. No repeated notifications while it remains
online. After two failed checks it is considered offline; another notification
is permitted only after it returns.

No unpaired device, broadcast packet, ARP response or plain Wi-Fi join is
enough to trigger an alert. The Mac must be running the dashboard with alerts
enabled, and the Windows inference node must be actively sharing. No
internet account, firewall exceptions beyond the specifically configured
private LAN listener, remote shell or file-sharing feature is involved.

This watches the **inference service**, not merely whether Windows has Wi-Fi.
A Windows PC that is powered on but has *sharing off* is intentionally
not announced as ready for AI inference.

## First-time setup

1. Pair Windows and the Mac with \`peer_pairing.py\`, using the same per-device
   private configuration directory used by the inference node. Never copy
   the host's private key or credential file to the Mac.
2. Run the inference host on Windows only when you want to share it, for example
   (PowerShell, from the repository on the Windows computer):

   \`\`\`powershell
   python lan_inference.py --bind 192.168.1.3 --data-dir "$env:LOCALAPPDATA\AI Assist\pairing"
   \`\`\`

   Change the IP if your PC now has a different LAN address. The port is
   **49382** by default; configure narrow inbound firewall rules only for
   your trusted local subnet. No public router port forwarding.
3. On the Mac, open the standalone compute dashboard via
   \`Start Compute Dashboard.command\`. Click **ALERTS OFF**, and enter the
   **client ID used for pairing this Mac** (example \`mac_1\`).
   Paired servers are read from \`~/.aiassist/pairing\` unless the
   \`AI_ASSIST_DATA_DIR\` environment variable configures the app data root.
4. On first use, macOS may ask whether Terminal (or your launcher app) can
   show notifications. Allow it if you want native alerts.

Alternatively, without opening a GUI, in Mac Terminal:

\`\`\`bash
python3 paired_node_watch.py --watch --client-id mac_1
\`\`\`

The program stops on Ctrl+C. It intentionally does **not** install a login
agent, service, daemon, or system-wide networking component.

## Authentication boundaries

- Host \`GET /v1/health\` requires the same authenticated, encrypted
  pairing identity as inference; it reveals only a node ID and
  inference-sharing status.
- Client pins the entire certificate SHA-256 fingerprint *before*
  transmitting its bearer token, rejects public IPs and redirects,
  and checks both the node ID and \`sharing=True\`.
- The client does not call model generation endpoints as part of the check.
- Node credentials are not included in notifications or saved status history.
- The existing browser workspace remains local-only and does not expose
  a remote-control port.

## Tests and limitations

Run:

\`\`\`bash
python -m unittest -v test_paired_node_watch test_peer_pairing test_sharing_control
\`\`\`

The watcher tests use fake HTTPS clients and do not start LAN sockets.
Availability, local notification permission, OS firewall access and a
real two-machine deployment **still need physical validation**.
If Windows' LAN address changes, the original saved pairing record needs an
explicit trusted update/re-pair; discovery never silently changes it.

This feature does not prove Windows can generate a response until an
actual authenticated inference request is tested.
