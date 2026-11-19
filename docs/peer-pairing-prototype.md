# Opt-in LAN discovery and pairing — prototype

This is **experimental** and must not be enabled automatically in the normal AI Assist application. It has no inference endpoints, does not change the existing UI, and does not allow file access or remote commands.

## Scope and safety

- The user must explicitly start sharing on the host device and explicitly request discovery on the client.
- Discovery uses a UDP broadcast on port `49381` and reports **UNVERIFIED** device candidates. Advertisements are never trusted identities.
- A host runs an HTTPS pairing endpoint on a randomly selected port, bound to an explicitly selected private IPv4 interface.
- The host displays (a) a 16-character certificate verification code (64-bit fingerprint prefix) and (b) a six-digit PIN, which expires after two minutes or three failed attempts.
- On the client, compare the **certificate code visible on the host screen** and enter the host's PIN. The client checks the TLS certificate code before transmitting the PIN.
- Successful pairing saves a per-client secret, tied on the client to the host certificate fingerprint. The host stores only its hash. Clients can be revoked.
- An ordinary LAN IP or a response to a broadcast **does not** establish trust.
- This version establishes *initial enrollment* only: it does not yet implement authenticated inference sessions, stream transport, mutual client-certificate authentication, key rotation, or secure UI management of revocation.
- The `cryptography` Python package is an **optional dependency** required to create an ECDSA TLS host identity. The original app's no-pip-dependencies runtime is unchanged.

## Demo on two desktop machines

Use a dedicated application-data directory **outside your Git checkout**, kept private and excluded from backup sharing.

On a server computer, with Python and the optional `cryptography` package available:

```bash
python -m pip install cryptography
python peer_pairing.py host --bind 192.168.1.3 --data-dir /private/path/ai-assist-node --name "AI Assist workstation"
```

Replace `192.168.1.3` with the real private IPv4 address assigned to the machine and the data path with an appropriate private writable folder. On Windows, a sensible example is `%LOCALAPPDATA%\AI Assist\pairing` but expand environment variables using your shell.

On a second machine:

```bash
python peer_pairing.py discover
```

A discovered candidate is **not verified**. To pair, obtain the target's certificate verification code and six-digit PIN directly from the target machine, then run (using a private directory on the second machine):

```bash
python peer_pairing.py pair --address 192.168.1.3 --port <DISCOVERED_PORT> --data-dir /private/path/ai-assist-client --client-id laptop_1 --verify <TARGET_CERT_CODE>
```

The pairing client prompts for the PIN without echoing it. **Never send this code through an untrusted discovery response.**

If Windows Firewall, router isolation or network controls block UDP/TCP, do not disable your security software wholesale. Apply narrowly scoped, local-subnet-only rules only after reviewing the actual interfaces and ports.

## Tests

```bash
python -m unittest -v test_peer_pairing test_compute_routing
```

The test suite contains pure unit tests and optional loopback HTTPS integration tests; the latter require `cryptography`. These files were added to the isolated architecture branch, but full cross-OS and real two-machine validation are **not yet claimed**.

## Before production integration

1. Validate the prototype with the complete Python test suite on Windows, macOS and Linux, including the TLS loopback tests and a real two-computer run.
2. Add a real versioned, authenticated inference API with authorization checks on every request, strict message-size limits, cancellation and streaming.
3. Ensure node tokens are never accepted over plaintext HTTP, are bound to the pinned TLS identity on every session, and are never logged.
4. Design safe trust-store management and filesystem permissions on Windows as well as Unix; ensure access-control lists protect private keys and tokens.
5. Evaluate port binding, spoofed LAN discovery, DoS, peer revocation, broadcast restrictions, link-local/IPv6 support and concurrent pairing attempts.
6. Gate any launch behind explicit UI permission; provide off-by-default sharing and a prominent active-sharing indicator.
7. Integrate with `compute_routing.py` only after the transport can prove the connected peer is authenticated and authorized. Never mark an unverified discovery candidate as `paired=True`.
8. No project-file sync, shell execution, browser exposure, or cloud service in phase 1.
