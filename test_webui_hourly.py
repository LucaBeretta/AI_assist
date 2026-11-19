"""Hourly scheduling preserves opt-in audio, quiet time, busy deferral and local-only collection."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app_core.hourly_status import HourlyStatusReport, HOUR_SECONDS


class HourlyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        self.now, self.events = [0], []
        self.context = dict(host="http://localhost:11434", model="local", backend="Ollama", local=True,
                            connection="ready", updates="Not checked", busy=False)
        self.detector = Mock()
        self.store = SimpleNamespace(directory=Path(folder.name), list_chats=lambda: [])
        self.report = HourlyStatusReport(self.store, lambda: self.context,
            lambda kind, **data: self.events.append((kind, data)), clock=lambda: self.now[0], detector_factory=Mock(return_value=self.detector))
        self.addCleanup(self.report.close)

    def enable(self):
        with patch("app_core.hourly_status.os", SimpleNamespace(name="nt")):
            self.assertTrue(self.report.set_enabled(True)["enabled"])

    def test_default_is_off_and_preview_does_not_start_audio(self):
        self.assertFalse(self.report.state()["enabled"])
        done = threading.Event()
        def collect(*args): done.set()
        with patch.object(self.report, "_collect", side_effect=collect):
            self.report.refresh()
            self.assertTrue(done.wait(1))
        self.report.detector_factory.assert_not_called()
        self.assertFalse(self.report.state()["enabled"])

    def test_one_hour_and_twenty_seconds_of_quiet_are_required_and_busy_defers(self):
        self.enable()
        self.report.events.put(("level", 0)); self.report.tick()
        self.now[0] = HOUR_SECONDS - 1
        with patch.object(self.report, "refresh") as refresh, patch.object(self.report, "_notify") as notify:
            self.report.tick(); refresh.assert_not_called()
            self.context["busy"] = True
            self.now[0] += 1; self.report.tick(); refresh.assert_not_called()
            self.context["busy"] = False
            self.report.tick(); refresh.assert_called_once(); notify.assert_called_once()
            self.report.tick(); self.assertEqual(refresh.call_count, 1)

    def test_sound_restarts_the_quiet_interval(self):
        self.enable()
        self.report.events.put(("level", 0)); self.report.tick()
        self.now[0] = HOUR_SECONDS - 1
        self.report.events.put(("level", .5)); self.report.tick()
        self.now[0] += 1; self.report.events.put(("level", 0)); self.report.tick()
        with patch.object(self.report, "refresh") as refresh, patch.object(self.report, "_notify"):
            self.now[0] += 19; self.report.tick(); refresh.assert_not_called()
            self.now[0] += 1; self.report.tick(); refresh.assert_called_once()

    def test_collection_does_not_send_recent_work_to_cloud_models(self):
        self.store.list_chats = lambda: [{"updated_at": datetime.now(timezone.utc).isoformat(),
            "title": "Private work", "messages": [{"role": "user", "content": "A private prompt"}]}]
        with patch("app_core.hourly_status.ollama.stream_chat") as stream, patch("app_core.hourly_status.detect_hardware", return_value={}):
            self.report._collect(datetime.now(timezone.utc), "Not checked", "http://localhost:11434", "remote:cloud", "Ollama", True, "ready")
        stream.assert_not_called()
        self.report.tick()
        self.assertEqual(self.events[-1][0], "hourly_report")
        self.assertIsInstance(self.events[-1][1]["report"]["generated_at"], str)
        self.report.update_updates("Checked")
        self.assertEqual(self.events[-1][0], "hourly_update")
        self.assertEqual(self.report.state()["report"]["updates"], "Checked")

    def test_feedback_persists_and_closed_reports_do_not_emit(self):
        self.report.rate("useful")
        other = HourlyStatusReport(self.store, lambda: self.context, Mock())
        self.addCleanup(other.close)
        self.assertEqual(other.state()["feedback"], "useful")
        self.report.close()
        self.report.events.put(("level", 0)); self.report.tick()
        self.assertEqual(self.events, [])
        with self.assertRaises(ValueError): self.report.set_enabled(True)

