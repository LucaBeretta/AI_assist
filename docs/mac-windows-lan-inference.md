# Mac -> Windows RTX inference (explicit opt-in)

This is an experimental **inference-only** link for AI Assist, using a paired
Windows Ollama node and the local-only web workspace on the Mac.

- Windows serves text generation, local model names and read-only telemetry.
- The Mac verifies the complete TLS certificate fingerprint before sending
  the per-device pairing token or any conversation content.
- No remote files, shell, browser automation, developer-agent commands, or
  public internet listeners are supported.
- Nothing is enabled automatically after restart. Stop the Windows inference
  command to end sharing.
- Prompts and generated text **travel over the trusted LAN to Windows**;
  they do not stay exclusively on the Mac. Both devices remain on your LAN.
- The Mac web UI remains bound to `127.0.0.1`, never the Windows IP.

## Prerequisites

Both computers need a checkout of the same integration branch of AI Assist,
Python 3, and **cryptography** for the first pairing certificate on Windows.
Windows must be running local Ollama, with your selected model already
downloaded (e.g. `ollama pull qwen3-coder:30b`). Both devices must be on the
same private network. Do not forward router ports.

Choose a *private* Windows folder **outside your Git repository** for pairing
credentials. The example below uses `$env:LOCALAPPDATA\AI Assist\pairing`.
Never commit this folder, send its contents over chat, or copy the Windows
private key to the Mac.

## 1. Windows: open a short-lived pairing window

Run in Windows PowerShell from the AI Assist repository:

```powershell
python -m pip install cryptography
python peer_pairing.py host --bind 192.168.1.3 --data-dir "$env:LOCALAPPDATA\AI Assist\pairing" --name "RTX 4090"
```

Replace `192.168.1.3` with your Windows computer's current private IPv4 if
it changed. This command prints a **pairing TCP port**, a **16-character
certificate verification code**, and a **six-digit one-time PIN** (120 seconds).
Keep this window open while pairing. On Windows Firewall, if prompted, grant
only on the **private** network for this application and pairing session.

## 2. Mac: pair exactly that node

In Terminal from the Mac AI Assist checkout, fill in the port and verification
code shown **on the Windows terminal**:

```bash
python3 peer_pairing.py pair --address 192.168.1.3 --port PAIRING_PORT --verify VERIFICATION_CODE --client-id mac_1 --data-dir "$HOME/.aiassist/pairing"
```

The Mac prompts securely for the PIN (not echoed). The command prints a
`node_id` and saves its own token + certificate fingerprint in the private
Mac pairing directory. **Keep the node ID; never share the PIN or token.**

After successful pairing, you may stop the Windows pairing command with Ctrl+C.
Do not delete the Windows credentials directory: the inference server uses the
same TLS identity and authorization records.

If the default data folder has been changed using `AI_ASSIST_DATA_DIR`, use
its parent `pairing` directory on the Mac instead.

## 3. Windows: explicitly enable inference

Open a fresh PowerShell terminal at the Windows repository:

```powershell
python lan_inference.py --bind 192.168.1.3 --data-dir "$env:LOCALAPPDATA\AI Assist\pairing"
```

Default TCP port: **49382**. Permit inbound traffic to that port **only** on
the private network and, preferably, only from the Mac's local IP. If Windows
Firewall blocks it, configure the narrow rule manually. **Do not disable the
firewall or forward the port through the router.**

If the service fails to start, check that the original pairing was completed
and that local Ollama is running. The Windows terminal must remain open;
Ctrl+C immediately ends the service.

## 4. Mac: confirm model access

In the Mac repository directory, substitute the node ID printed by pairing:

```bash
python3 lan_client.py --peer-id NODE_ID --client-id mac_1 --models
```

If Qwen is installed and local on Windows, it should appear in the model list.
For a one-off test:

```bash
python3 lan_client.py --peer-id NODE_ID --client-id mac_1 --model qwen3-coder:30b --prompt "Reply with READY"
```

No special permission or SSH session is needed for inference. Certificate
changes, wrong client IDs, missing pairing and disabled sharing all fail closed.

## 5. Mac: start the full local web chat with the paired Windows provider

Close other AI Assist windows using the same saved-chat data folder, then:

```bash
python3 web_workspace.py --lan-peer-id NODE_ID --lan-client-id mac_1
```

Open the one-time **localhost** link printed by the Mac. In the chat model
selector, choose a Windows-hosted model, then send a message. The web workspace
streams responses and saves chats on the **Mac**, and the RTX 4090 performs
inference on **Windows**.

This mode remains in effect only for this web workspace process. Stop it with
Ctrl+C; the next default launch (without LAN flags) uses your original
Ollama/Colibri provider. Existing chats with a different backend may require
switching back to their original backend or starting a new chat.

The standalone Tk compute dashboard is separate from the web workspace; its
paired-node notifications continue to be opt-in and do not send prompts.

## Security and test status

- All LAN destinations must be literal private IPv4 addresses: no DNS or
  public IPs.
- TLS peer certificates are pinned before sending authorization or content.
  Browser Origin headers are refused by the inference server.
- Server accepts only an allowlisted JSON structure with role/content text.
- Model name must be a locally installed, non-cloud Ollama model.
- Message size, response size, response-event and model limits are bounded.
- No file/tool execution path exists in the LAN API.
- Loopback integration tests live in `test_lan_client.py`.
- **Real Mac-to-Windows validation is still required** before this PR is
  suitable for merging.
