from datetime import datetime, timezone
import unittest

from app_core.hourly_summary import fallback_feedback, hardware_summary, recent_chats, work_snapshot
from quiet_audio import is_quiet, rms_level


class HourlySummaryTests(unittest.TestCase):
    def test_recent_chats_uses_only_valid_updates_from_the_last_hour(self):
        now = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
        chats = [
            {"title": "Recent", "updated_at": "2026-11-19T11:30:00+00:00", "messages": []},
            {"title": "Old", "updated_at": "2026-11-19T10:59:00+00:00", "messages": []},
            {"title": "Future", "updated_at": "2026-11-19T12:01:00+00:00", "messages": []},
            {"title": "Invalid", "updated_at": "not-a-time", "messages": []},
        ]
        self.assertEqual([chat["title"] for chat in recent_chats(chats, now)], ["Recent"])

    def test_work_snapshot_uses_recent_titles_and_user_prompts(self):
        snapshot = work_snapshot([{
            "title": "Improve the sidebar",
            "messages": [
                {"role": "user", "content": "Plan it"},
                {"role": "assistant", "content": "Done"},
                {"role": "user", "content": "  Tighten   the layout\nplease "},
            ],
        }])
        self.assertEqual(snapshot, {
            "threads": 1,
            "topics": ["Improve the sidebar"],
            "prompts": ["Tighten the layout please"],
        })

    def test_fallback_feedback_is_short_and_grounded_in_recent_chat_titles(self):
        self.assertIn("No chats were updated", fallback_feedback({"topics": []}))
        self.assertIn("Improve the sidebar", fallback_feedback({"topics": ["Improve the sidebar"]}))

    def test_hardware_summary_reports_memory_and_gpu(self):
        text = hardware_summary({
            "ram_available_bytes": 8 * 1024 ** 3,
            "gpus": [{"name": "RTX 4060", "free_bytes": 4 * 1024 ** 3, "total_bytes": 8 * 1024 ** 3}],
            "disk_free_bytes": 50 * 1024 ** 3,
        })
        self.assertIn("RAM 8.0 GB free", text)
        self.assertIn("RTX 4060", text)
        self.assertIn("Disk 50 GB free", text)


class QuietAudioTests(unittest.TestCase):
    def test_rms_level_reads_signed_pcm_and_ignores_an_odd_trailing_byte(self):
        self.assertEqual(rms_level(b""), 0.0)
        self.assertEqual(rms_level(b"\x00\x00\x00"), 0.0)
        self.assertAlmostEqual(rms_level(b"\xe8\x03\xe8\x03"), 1000 / 32768)

    def test_quiet_threshold_is_based_on_normalized_rms(self):
        self.assertTrue(is_quiet(0.0))
        self.assertTrue(is_quiet(0.01))
        self.assertFalse(is_quiet(0.02))
        self.assertFalse(is_quiet(-0.01))


if __name__ == "__main__":
    unittest.main()
