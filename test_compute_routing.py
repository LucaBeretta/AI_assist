"""Deterministic, network-free tests for local-first inference routing."""

from dataclasses import replace
import unittest

from compute_routing import ComputeNode, RoutingError, RoutingRequest, choose_route


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.local = ComputeNode("my-pc", True, True, frozenset({"small"}),
                                 features=frozenset({"streaming"}),
                                 max_context_tokens=8192, free_memory_bytes=8_000,
                                 tokens_per_second=20)
        self.peer = ComputeNode("paired-pc", False, True, frozenset({"small", "large"}),
                                features=frozenset({"streaming", "tool_calls"}),
                                max_context_tokens=32768, free_memory_bytes=24_000,
                                paired=True, trusted_lan=True, peer_inference_allowed=True,
                                tokens_per_second=80, latency_ms=5)

    def route(self, **changes):
        return choose_route(RoutingRequest("small", **changes), [self.local, self.peer])

    def test_default_is_always_local(self):
        self.assertEqual(self.route().node_id, "my-pc")
        self.assertEqual(self.route(mode="local", allow_peer_inference=True).node_id, "my-pc")

    def test_auto_requires_explicit_request_consent(self):
        self.assertEqual(self.route(mode="auto").node_id, "my-pc")
        self.assertEqual(self.route(mode="auto", allow_peer_inference=True).node_id, "paired-pc")

    def test_auto_rejects_untrusted_unpaired_or_nonconsenting_peer(self):
        for change in ({"paired": False}, {"trusted_lan": False},
                       {"peer_inference_allowed": False}, {"online": False},
                       {"model_backend_local": False}, {"accepts_inference": False}):
            with self.subTest(change=change):
                decision = choose_route(RoutingRequest("small", mode="auto", allow_peer_inference=True),
                                        [self.local, replace(self.peer, **change)])
                self.assertTrue(decision.is_local)

    def test_prefer_local_when_speed_advantage_is_small_or_unknown(self):
        for change in ({"tokens_per_second": 21}, {"tokens_per_second": None}):
            with self.subTest(change=change):
                decision = choose_route(RoutingRequest("small", mode="auto", allow_peer_inference=True),
                                        [self.local, replace(self.peer, **change)])
                self.assertTrue(decision.is_local)

    def test_pinned_never_falls_back(self):
        with self.assertRaises(RoutingError):
            self.route(mode="pinned", pinned_node_id="paired-pc")
        self.assertEqual(self.route(mode="pinned", pinned_node_id="paired-pc",
                                    allow_peer_inference=True).node_id, "paired-pc")
        with self.assertRaises(RoutingError):
            self.route(mode="pinned", pinned_node_id="missing", allow_peer_inference=True)
        with self.assertRaises(RoutingError):
            choose_route(RoutingRequest("small", mode="pinned", pinned_node_id="paired-pc",
                                        allow_peer_inference=True),
                         [self.local, replace(self.peer, online=False)])

    def test_missing_local_model_requires_peer_consent(self):
        with self.assertRaises(RoutingError):
            choose_route(RoutingRequest("large", mode="auto"), [self.local, self.peer])

    def test_larger_model_auto_uses_peer_if_local_incompatible(self):
        decision = choose_route(RoutingRequest("large", mode="auto", allow_peer_inference=True),
                                [self.local, self.peer])
        self.assertEqual(decision.node_id, "paired-pc")

    def test_compatibility_filters_memory_context_and_features(self):
        for request in (
            RoutingRequest("small", mode="auto", allow_peer_inference=True, memory_required_bytes=32_000),
            RoutingRequest("small", mode="auto", allow_peer_inference=True, context_tokens=50_000),
            RoutingRequest("small", mode="auto", allow_peer_inference=True,
                           required_features=frozenset({"unavailable_feature"})),
        ):
            with self.subTest(request=request), self.assertRaises(RoutingError):
                choose_route(request, [self.local, self.peer])
        decision = choose_route(RoutingRequest("small", mode="auto", allow_peer_inference=True,
                                               required_features=frozenset({"tool_calls"})),
                                [self.local, self.peer])
        self.assertEqual(decision.node_id, "paired-pc")

    def test_reject_cloud_backends_and_duplicate_nodes(self):
        with self.assertRaises(RoutingError):
            choose_route(RoutingRequest("small"), [replace(self.local, model_backend_local=False), self.peer])
        with self.assertRaises(RoutingError):
            choose_route(RoutingRequest("small"), [self.local, self.local])

    def test_invalid_request_rejected(self):
        for request in (RoutingRequest("", mode="auto"), RoutingRequest("small", mode="unknown"),
                        RoutingRequest("small", context_tokens=-1),
                        RoutingRequest("small", mode="pinned"),
                        RoutingRequest("small", pinned_node_id="paired-pc")):
            with self.subTest(request=request), self.assertRaises(RoutingError):
                choose_route(request, [self.local, self.peer])


if __name__ == "__main__":
    unittest.main()
