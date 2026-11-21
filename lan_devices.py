"""On-demand local network scan for Mission Control.

Runs only when a person clicks Scan; nothing here starts at import or on a timer.
Every packet stays on this computer's own private subnet (at most a /24) and
passes through the internet gate like any other request. Devices are found four
independent ways, because each can be hidden: macOS may hide the neighbour
table from apps it did not sign, and many phones ignore unsolicited packets.

- neighbour (ARP) table, read with ``arp -n``: addresses only, no name lookups;
- a TCP "knock" on a few common ports: any answer, even "refused", proves a
  device is there, and the port hints at its kind (no data is sent);
- Bonjour (mDNS) questions on the local multicast group 224.0.0.251, with the
  "unicast response" bit: names come from the devices, never a DNS server;
- the standard AI Assist discovery query (see peer_pairing), whose answers stay
  UNVERIFIED and are never turned into a paired identity.

Ollama servers are spotted with ``GET /api/version`` on port 11434. No socket is
bound to a network interface and nothing listens: replies arrive on the
ephemeral port each query was sent from, exactly like a DNS lookup.
"""

import concurrent.futures
import errno
import ipaddress
import json
import platform
import re
import shutil
import socket
import struct
import subprocess
import time
import urllib.request

import peer_pairing

OLLAMA_PORT = 11434
MDNS = ("224.0.0.251", 5353)
MAX_HOSTS = 254
# Port -> what an open (or refusing) port suggests. Only a TCP handshake is made; nothing is sent.
KNOCK_PORTS = {80: "", 443: "", 22: "computer", 445: "computer", 548: "computer",
               62078: "apple-mobile", 7000: "airplay", 8009: "cast", 631: "printer", 9100: "printer",
               OLLAMA_PORT: "ollama"}
_PRIVATE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
_MAC = re.compile(r"^[0-9a-f]{1,2}([:-][0-9a-f]{1,2}){5}$", re.I)
_BONJOUR_SERVICES = ("_services._dns-sd._udp.local", "_device-info._tcp.local", "_airplay._tcp.local",
                     "_companion-link._tcp.local", "_googlecast._tcp.local", "_ipp._tcp.local",
                     "_printer._tcp.local", "_smb._tcp.local", "_ssh._tcp.local", "_raop._tcp.local")


def _run(command, timeout=3):
    # arp, route and ifconfig live in /usr/sbin or /sbin, often missing from an app's PATH.
    program = shutil.which(command[0]) or shutil.which(command[0], path="/usr/sbin:/sbin:/usr/bin:/bin")
    if program is None:
        return ""
    try:
        result = subprocess.run([program, *command[1:]], capture_output=True, text=True, timeout=timeout, check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def private(address):
    try:
        ip = ipaddress.IPv4Address(address)
    except (ipaddress.AddressValueError, ValueError):
        return False
    return any(ip in network for network in _PRIVATE)


# ------------------------------------------------------------- this computer


def interfaces(system=None, run=_run):
    """This computer's private IPv4 interfaces as [{"name", "address", "network"}]."""
    system = system or platform.system()
    found = []
    if system == "Windows":
        address = None
        for line in run(["ipconfig"]).splitlines():
            match = re.search(r"IPv4[^:]*:\s*([\d.]+)", line)
            if match:
                address = match.group(1)
            match = re.search(r"Subnet Mask[^:]*:\s*([\d.]+)", line)
            if match and address:
                found.append(("", address, match.group(1)))
                address = None
    else:
        name = ""
        text = run(["ifconfig"])
        if not text.strip() and system == "Linux":
            # Raspberry Pi OS and other recent Linux systems ship `ip` without `ifconfig`.
            for line in run(["ip", "-4", "-o", "addr", "show"]).splitlines():
                match = re.search(r"^\d+:\s+(\S+)\s+inet\s+([\d.]+)/(\d+)", line)
                if match:
                    found.append((match.group(1), match.group(2), str(ipaddress.IPv4Network(f"0.0.0.0/{match.group(3)}").netmask)))
        for line in text.splitlines():
            if line and not line[0].isspace():
                name = line.split(":", 1)[0]
                continue
            match = re.search(r"inet (?:addr:)?([\d.]+)\s+(?:netmask|Mask:)\s*(0x[0-9a-f]+|[\d.]+)", line, re.I)
            if match:
                mask = match.group(2)
                if mask.lower().startswith("0x"):
                    mask = str(ipaddress.IPv4Address(int(mask, 16)))
                found.append((name, match.group(1), mask))
    result = []
    for name, address, mask in found:
        if not private(address):
            continue
        try:
            network = ipaddress.IPv4Network(f"{address}/{mask}", strict=False)
        except ValueError:
            continue
        result.append({"name": name, "address": address, "network": network})
    return result


def gateway(system=None, run=_run):
    system = system or platform.system()
    if system == "Darwin":
        match = re.search(r"gateway:\s*([\d.]+)", run(["route", "-n", "get", "default"]))
    elif system == "Windows":
        match = re.search(r"Default Gateway[ .]*:\s*([\d.]+)", run(["ipconfig"]))
    else:
        match = re.search(r"default via ([\d.]+)", run(["ip", "-4", "route", "show", "default"]))
    return match.group(1) if match and private(match.group(1)) else None


def main_interface(found, router):
    """The interface on the router's network; else the first ordinary private one."""
    for item in found:
        if router and ipaddress.IPv4Address(router) in item["network"]:
            return item
    return found[0] if found else None


def scan_targets(network, own):
    """At most the /24 around this computer: never sweep a large corporate network."""
    if network.prefixlen < 24:
        network = ipaddress.IPv4Network(f"{own}/24", strict=False)
    hosts = [str(ip) for ip in network.hosts()][:MAX_HOSTS]
    return [ip for ip in hosts if ip != own], network


# ------------------------------------------------------------- finding devices


def neighbors(system=None, run=_run):
    """{ip: hardware address} from the neighbour table, without name lookups."""
    system = system or platform.system()
    table = {}
    text = run(["arp", "-a"] if system == "Windows" else ["arp", "-an"])
    for line in text.splitlines():
        if system == "Windows":
            parts = line.split()
            if len(parts) >= 2 and private(parts[0]) and _MAC.match(parts[1]):
                table[parts[0]] = parts[1].replace("-", ":").lower()
            continue
        match = re.search(r"\(([\d.]+)\) at ([0-9a-f:]+)", line, re.I)
        if match and private(match.group(1)) and _MAC.match(match.group(2)):
            mac = ":".join(part.zfill(2) for part in match.group(2).lower().split(":"))
            if mac != "ff:ff:ff:ff:ff:ff" and not mac.startswith("01:00:5e"):
                table[match.group(1)] = mac
    return table


def knock(ip, port, timeout=.45):
    """True when something at ip answers on port (open, or actively refused)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            result = sock.connect_ex((ip, port))
        except OSError:
            return False
    return result in (0, errno.ECONNREFUSED)


def knock_all(targets, pool, timeout=.45):
    """{ip: [ports that answered]} for every target that answered at least once."""
    jobs = {pool.submit(knock, ip, port, timeout): (ip, port) for ip in targets for port in KNOCK_PORTS}
    found = {}
    for job, (ip, port) in jobs.items():
        if job.result():
            found.setdefault(ip, []).append(port)
    return found


def _encode_name(name):
    return b"".join(bytes([len(part)]) + part.encode("utf-8") for part in name.split(".") if part) + b"\0"


def mdns_query(names):
    """One mDNS packet asking PTR for each name, with the unicast-response bit."""
    packet = struct.pack("!HHHHHH", 0, 0, len(names), 0, 0, 0)
    for name in names:
        packet += _encode_name(name) + struct.pack("!HH", 12, 0x8001)
    return packet


def _read_name(packet, offset, depth=0):
    labels = []
    while depth < 20:
        length = packet[offset]
        if length == 0:
            return ".".join(labels), offset + 1
        if length & 0xC0 == 0xC0:
            pointer = struct.unpack("!H", packet[offset:offset + 2])[0] & 0x3FFF
            name, _ = _read_name(packet, pointer, depth + 1)
            return ".".join(labels + ([name] if name else [])), offset + 2
        labels.append(packet[offset + 1:offset + 1 + length].decode("utf-8", "replace"))
        offset += 1 + length
    raise ValueError("Name compression loop")


def parse_records(packet):
    """[(owner name, type, value)] for PTR, A and SRV-free records in every section."""
    records = []
    try:
        _ident, _flags, questions, answers, authority, additional = struct.unpack("!HHHHHH", packet[:12])
        offset = 12
        for _ in range(questions):
            _, offset = _read_name(packet, offset)
            offset += 4
        for _ in range(answers + authority + additional):
            owner, offset = _read_name(packet, offset)
            kind, _klass, _ttl, length = struct.unpack("!HHIH", packet[offset:offset + 10])
            offset += 10
            if kind == 12:
                records.append((owner, 12, _read_name(packet, offset)[0]))
            elif kind == 1 and length == 4:
                records.append((owner, 1, socket.inet_ntoa(packet[offset:offset + 4])))
            offset += length
    except (struct.error, IndexError, ValueError, OSError):
        pass
    return records


def _reverse_name(ip):
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


def bonjour(own, network, known, timeout=1.6):
    """({ip: name}, {ips that answered}) from Bonjour answers on the local network."""
    names, answered = {}, set()
    reverse = {_reverse_name(ip): ip for ip in known}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(own))
        except OSError:
            pass
        questions = list(_BONJOUR_SERVICES) + list(reverse)
        for start in range(0, len(questions), 20):
            try:
                sock.sendto(mdns_query(questions[start:start + 20]), MDNS)
            except OSError:
                break
        stop = time.monotonic() + timeout
        while time.monotonic() < stop:
            sock.settimeout(max(.01, stop - time.monotonic()))
            try:
                packet, (source, _) = sock.recvfrom(9000)
            except (socket.timeout, OSError):
                break
            if not private(source) or ipaddress.IPv4Address(source) not in network:
                continue
            answered.add(source)
            for owner, kind, value in parse_records(packet):
                if kind == 1 and value in known | answered | {source} and owner.endswith(".local"):
                    names.setdefault(value, owner)
                elif kind == 12 and owner in reverse:
                    names[reverse[owner]] = value
    return {ip: name.rstrip(".")[:80] for ip, name in names.items()}, answered


def ai_assist_peers(broadcast, timeout=1.2):
    """Answers to one AI Assist discovery query. UNVERIFIED: shown, never trusted."""
    found = {}
    query = json.dumps({"magic": peer_pairing.MAGIC, "version": peer_pairing.PROTOCOL, "discover": True},
                       separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            sock.sendto(query, (broadcast, peer_pairing.DISCOVERY_PORT))
        except OSError:
            return {}
        stop = time.monotonic() + timeout
        while time.monotonic() < stop:
            sock.settimeout(max(.01, stop - time.monotonic()))
            try:
                payload, (address, _) = sock.recvfrom(peer_pairing.MAX_PACKET + 1)
            except (socket.timeout, OSError):
                break
            candidate = peer_pairing.parse_advertisement(payload, address)
            if candidate:
                found[address] = candidate
    return found


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ollama_version(ip, timeout=.8, opener=_DIRECT):
    try:
        with opener.open(f"http://{ip}:{OLLAMA_PORT}/api/version", timeout=timeout) as response:
            value = json.loads(response.read(4096))
        version = value.get("version") if isinstance(value, dict) else None
        return str(version)[:32] if version else "unknown"
    except Exception:
        return None


# ------------------------------------------------------------- describing them


def private_hardware_address(mac):
    """Phones and tablets use rotating 'private' Wi-Fi addresses (locally administered bit)."""
    try:
        return bool(int(mac.split(":")[0], 16) & 2)
    except (ValueError, AttributeError, IndexError):
        return False


def guess_kind(name, mac, router, ports=()):
    if router:
        return "router"
    text = (name or "").lower()
    for kind, words in (("phone", ("iphone", "android", "pixel", "galaxy", "phone")),
                        ("tablet", ("ipad", "tablet")),
                        ("computer", ("macbook", "imac", "mac-mini", "macmini", "mac-studio", "desktop", "laptop", "-pc", "mbp")),
                        ("tv", ("tv", "chromecast", "roku", "firetv", "shield", "bravia")),
                        ("printer", ("printer", "epson", "brother", "canon", "laserjet", "officejet")),
                        ("speaker", ("homepod", "sonos", "echo", "speaker"))):
        if any(word in text for word in words):
            return kind
    hints = {KNOCK_PORTS.get(port) for port in ports}
    if "printer" in hints:
        return "printer"
    if "apple-mobile" in hints:
        return "phone"
    if "computer" in hints or "ollama" in hints:
        return "computer"
    if hints & {"airplay", "cast"}:
        return "tv"
    if private_hardware_address(mac):
        return "phone"  # a rotating Wi-Fi address is almost always a phone, tablet or watch
    return "device"


def scan(system=None, run=_run, pause=.6):
    """One scan of this computer's local network. Returns plain JSON-safe data."""
    started = time.time()
    found = interfaces(system, run)
    router = gateway(system, run)
    main = main_interface(found, router)
    if main is None:
        return {"ok": True, "time": started, "seconds": 0, "network": "", "devices": [],
                "note": "This computer has no private network address, so there is nothing to scan."}
    own = main["address"]
    targets, network = scan_targets(main["network"], own)
    with concurrent.futures.ThreadPoolExecutor(max_workers=192) as pool:
        knocked_job = pool.submit(knock_all, targets, pool)
        peers_job = pool.submit(ai_assist_peers, str(network.broadcast_address))
        table = {ip: mac for ip, mac in neighbors(system, run).items()
                 if ipaddress.IPv4Address(ip) in network and ip != str(network.broadcast_address)}
        names, answered = bonjour(own, network, set(table) | ({router} if router else set()))
        knocked, peers = knocked_job.result(), peers_job.result()
        present = (set(table) | set(knocked) | answered | set(peers) | {own} | ({router} if router else set()))
        present = {ip for ip in present if ipaddress.IPv4Address(ip) in network and ip != str(network.broadcast_address)}
        missing = sorted(present - set(names) - {own})
        if missing:  # a second, targeted round for devices found only by knocking
            more, _ = bonjour(own, network, set(missing), timeout=.8)
            names.update(more)
        ollama_hosts = [ip for ip in present if ip == own or OLLAMA_PORT in knocked.get(ip, ())]
        ollama = dict(zip(ollama_hosts, pool.map(lambda ip: ollama_version("127.0.0.1" if ip == own else ip), ollama_hosts)))
    devices = []
    for ip in sorted(present, key=ipaddress.IPv4Address):
        mac, this, peer = table.get(ip, ""), ip == own, peers.get(ip)
        name = socket.gethostname().split(".")[0] if this else names.get(ip, "")
        if peer and peer.get("name") and not name:
            name = peer["name"]
        devices.append({
            "address": ip, "name": name[:80], "this": this, "router": ip == router,
            "kind": "computer" if this else guess_kind(name, mac, ip == router, knocked.get(ip, ())),
            "ollama": ollama.get(ip), "ai_assist": bool(peer), "ai": bool(ollama.get(ip) or peer),
        })
    return {"ok": True, "time": started, "seconds": round(time.time() - started, 1),
            "network": str(network), "devices": devices,
            "note": "" if len(devices) > 2 else "Few devices answered. Phones often stay silent until they are in use."}


if __name__ == "__main__":
    print(json.dumps(scan(), indent=2))
