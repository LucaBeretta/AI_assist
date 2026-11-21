"""Compute polling remains local, rate limited, read-only and safe after close."""
import threading
import unittest
from unittest.mock import Mock

from webui.compute import ComputePoller
import test_webui_app as fixtures


class ComputeTests(unittest.TestCase):
    def test_no_startup_sampling_and_visibility_cannot_bypass_five_seconds(self):
        now, events = [1], []
        sampler = Mock()
        sampler.sample.return_value = {"cpu": {"percent": None}, "ram": {}, "gpus": [], "models": {}}
        poller = ComputePoller(sampler, lambda kind, **value: events.append(value), clock=lambda: now[0], autostart=False)
        self.addCleanup(poller.close)
        self.assertFalse(poller.tick())
        sampler.sample.assert_not_called()
        poller.set_active(True)
        self.assertTrue(poller.tick())
        self.assertEqual(events[-1]["data"]["values"]["cpu_usage"], "unavailable")
        now[0] += 1
        poller.set_active(False); poller.set_active(True)
        self.assertFalse(poller.tick())
        now[0] += 4
        self.assertTrue(poller.tick())
        self.assertEqual(sampler.sample.call_count, 2)
        with self.assertRaises(ValueError): poller.set_active("true")

    def test_late_samples_after_close_are_not_emitted_and_peers_have_no_secrets(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def sample():
            entered.set(); release.wait(3)
            return {}
        emit = Mock()
        poller = ComputePoller(Mock(sample=sample), emit, peers=lambda: {"id": {"token": "secret"}}, autostart=False)
        poller.set_active(True)
        thread = threading.Thread(target=poller.tick); thread.start()
        self.assertTrue(entered.wait(1))
        poller.close(); release.set(); thread.join(2)
        emit.assert_not_called()
        poller = ComputePoller(Mock(sample=lambda: {}), emit, peers=lambda: {"id": {"token": "secret"}}, autostart=False)
        self.addCleanup(poller.close)
        poller.set_active(True); poller.tick()
        self.assertNotIn("secret", repr(poller.state()))
        self.assertIn("telemetry unavailable", repr(poller.state()))

    def test_api_has_no_startup_sampler_or_listener(self):
        case = fixtures.ApiTests()
        case.setUp()
        try:
            self.assertEqual(case.api.compute_state()["data"], None)
            self.assertIsNone(case.api._compute.thread)
            self.assertFalse(case.api.set_compute_active(1)["ok"])
        finally:
            case.doCleanups()

    def test_separate_window_keeps_polling_while_main_view_is_hidden(self):
        case = fixtures.ApiTests()
        case.setUp()
        try:
            api = case.api
            api._compute.autostart = False
            self.assertTrue(api.set_compute_active(True, "window")["active"])
            self.assertTrue(api.set_compute_active(False)["active"])
            self.assertFalse(api.set_compute_active(True, "elsewhere")["ok"])
            self.assertFalse(api.set_compute_active("yes", "window")["ok"])
            api._compute_window_closed()
            self.assertFalse(api.compute_state()["active"])
        finally:
            case.doCleanups()

    def test_compute_window_opens_on_its_remembered_screen(self):
        from types import SimpleNamespace
        case = fixtures.ApiTests()
        case.setUp()
        try:
            api, opened = case.api, []
            screens = [SimpleNamespace(x=0, y=0, width=1512, height=982), SimpleNamespace(x=1512, y=0, width=1366, height=1024)]
            api._desktop.screens = lambda: screens
            api._desktop.open_compute = lambda uri, js_api, geometry, remember, closed: opened.append((uri, js_api, geometry, remember)) or True
            self.assertTrue(api.open_compute_window()["opened"])
            uri, js_api, geometry, remember = opened[-1]
            self.assertTrue(uri.endswith("compute-window.html"))
            self.assertIs(js_api, api)
            self.assertEqual(geometry, {"width": 800, "height": 480})
            remember(dict(x=1600, y=40, width=960, height=540))
            api.open_compute_window()
            self.assertEqual(opened[-1][2], dict(x=1600, y=40, width=960, height=540))
            screens.pop()
            api.open_compute_window()
            self.assertEqual(opened[-1][2]["x"], 1512 - 960)
        finally:
            case.doCleanups()
