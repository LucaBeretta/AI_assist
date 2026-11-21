"""Local network scan: parsing, limits and merging, without touching the network."""
import ipaddress
import socket
import struct
import threading
import unittest
from unittest.mock import patch

import lan_devices
import test_webui_app as fixtures

IFCONFIG = """lo0: flags=8049<UP,LOOPBACK,RUNNING,MULTICAST> mtu 16384
\tinet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 192.168.1.7 netmask 0xffffff00 broadcast 192.168.1.255
en29: flags=8863<UP,BROADCAST,SMART,RUNNING,SIMPLEX,MULTICAST> mtu 1500
\tinet 169.254.53.116 netmask 0xffff0000 broadcast 169.254.255.255
"""
ARP = """? (169.254.255.255) at (incomplete) on en0 [ethernet]
? (192.168.1.1) at c0:49:43:9b:e5:34 on en0 ifscope [ethernet]
? (192.168.1.5) at 32:47:1a:f2:5d:50 on en0 ifscope [ethernet]
? (192.168.1.35) at (incomplete) on en0 ifscope [ethernet]
? (192.168.1.255) at ff:ff:ff:ff:ff:ff on en0 ifscope [ethernet]
? (224.0.0.251) at 1:0:5e:0:0:fb on en0 ifscope permanent [ethernet]
"""
ROUTE = "   route to: default\ndestination: default\n    gateway: 192.168.1.1\n  interface: en0\n"


def fake_run(command, timeout=3):
    return {"ifconfig": IFCONFIG, "arp": ARP, "route": ROUTE}.get(command[0], "")


class ParsingTests(unittest.TestCase):
    def test_interfaces_skip_loopback_and_link_local(self):
        found = lan_devices.interfaces("Darwin", fake_run)
        self.assertEqual([(i["name"], i["address"], str(i["network"])) for i in found], [("en0", "192.168.1.7", "192.168.1.0/24")])
        self.assertEqual(lan_devices.gateway("Darwin", fake_run), "192.168.1.1")

    def test_neighbor_table_keeps_only_real_private_hosts(self):
        self.assertEqual(lan_devices.neighbors("Darwin", fake_run),
                         {"192.168.1.1": "c0:49:43:9b:e5:34", "192.168.1.5": "32:47:1a:f2:5d:50"})
        windows = "  192.168.1.1          c0-49-43-9b-e5-34     dynamic\n  8.8.8.8   00-11-22-33-44-55  dynamic\n"
        self.assertEqual(lan_devices.neighbors("Windows", lambda *a, **k: windows), {"192.168.1.1": "c0:49:43:9b:e5:34"})

    def test_large_networks_are_limited_to_the_local_slash_24(self):
        targets, network = lan_devices.scan_targets(ipaddress.IPv4Network("10.0.0.0/8"), "10.4.5.6")
        self.assertEqual(str(network), "10.4.5.0/24")
        self.assertEqual(len(targets), 253)
        self.assertNotIn("10.4.5.6", targets)

    def test_mdns_answers_are_parsed_including_compression(self):
        name = lan_devices._encode_name("5.1.168.192.in-addr.arpa")
        target = lan_devices._encode_name("Lucas-iPhone.local")
        packet = struct.pack("!HHHHHH", 0, 0x8400, 0, 2, 0, 0)
        packet += name + struct.pack("!HHIH", 12, 0x8001, 120, len(target)) + target
        packet += b"\xc0" + bytes([12 + len(name) + 10]) + struct.pack("!HHIH", 1, 0x8001, 120, 4) + socket.inet_aton("192.168.1.5")
        self.assertEqual(lan_devices.parse_records(packet),
                         [("5.1.168.192.in-addr.arpa", 12, "Lucas-iPhone.local"), ("Lucas-iPhone.local", 1, "192.168.1.5")])
        self.assertEqual(lan_devices.parse_records(b"\x00\x01garbage"), [])

    def test_kind_guesses_use_names_ports_and_private_addresses(self):
        self.assertEqual(lan_devices.guess_kind("Lucas-iPhone.local", "", False), "phone")
        self.assertEqual(lan_devices.guess_kind("", "", True), "router")
        self.assertEqual(lan_devices.guess_kind("", "", False, (9100,)), "printer")
        self.assertEqual(lan_devices.guess_kind("", "", False, (11434,)), "computer")
        self.assertEqual(lan_devices.guess_kind("", "32:47:1a:f2:5d:50", False), "phone")
        self.assertEqual(lan_devices.guess_kind("", "c0:49:43:9b:e5:34", False), "device")


class ScanTests(unittest.TestCase):
    def test_scan_merges_every_source_and_marks_ai_computers(self):
        with patch.object(lan_devices, "knock_all", return_value={"192.168.1.20": [OLLAMA := lan_devices.OLLAMA_PORT]}), \
                patch.object(lan_devices, "bonjour", return_value=({"192.168.1.5": "Lucas-iPhone.local"}, {"192.168.1.5", "192.168.1.30"})), \
                patch.object(lan_devices, "ai_assist_peers", return_value={"192.168.1.40": {"name": "Studio", "port": 1}}), \
                patch.object(lan_devices, "ollama_version", side_effect=lambda ip: "0.40.17" if ip in ("127.0.0.1", "192.168.1.20") else None):
            result = lan_devices.scan("Darwin", fake_run)
        rows = {d["address"]: d for d in result["devices"]}
        self.assertEqual(sorted(rows), ["192.168.1.1", "192.168.1.20", "192.168.1.30", "192.168.1.40", "192.168.1.5", "192.168.1.7"])
        self.assertTrue(rows["192.168.1.7"]["this"] and rows["192.168.1.7"]["ai"])
        self.assertTrue(rows["192.168.1.1"]["router"])
        self.assertEqual(rows["192.168.1.20"]["ollama"], "0.40.17")
        self.assertEqual(rows["192.168.1.5"]["kind"], "phone")
        self.assertTrue(rows["192.168.1.40"]["ai_assist"])
        self.assertEqual(rows["192.168.1.40"]["name"], "Studio")
        self.assertEqual(result["network"], "192.168.1.0/24")

    def test_no_private_address_means_nothing_is_sent(self):
        with patch.object(lan_devices, "knock_all") as knock:
            result = lan_devices.scan("Darwin", lambda *a, **k: "")
        knock.assert_not_called()
        self.assertEqual(result["devices"], [])


class ApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_scan_runs_only_on_request_one_at_a_time_and_is_remembered(self):
        self.assertIsNone(self.api.network_state()["scan"])
        release, entered = threading.Event(), threading.Event()
        def slow():
            entered.set(); release.wait(2)
            return {"ok": True, "devices": [{"address": "192.168.1.7"}]}
        self.api._scan_network = slow
        worker = threading.Thread(target=self.api.scan_network); worker.start()
        entered.wait(2)
        self.assertTrue(self.api.network_state()["busy"])
        self.assertFalse(self.api.scan_network()["ok"])
        release.set(); worker.join(2)
        self.assertEqual(self.api.network_state()["scan"]["devices"][0]["address"], "192.168.1.7")


if __name__ == "__main__":
    unittest.main()
