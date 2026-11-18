import json
import threading
import unittest

from webui.bridge import Bridge


def events(script):
    return json.loads(script[len("app.onEvent("):-1])


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.scripts = []
        self.bridge = Bridge(self.scripts.append, autostart=False)
        self.addCleanup(self.bridge.close)

    def test_consecutive_tokens_merge_without_crossing_streams_or_types(self):
        for type, stream, text in (("token", "a", "one"), ("token", "a", " two"),
                                   ("metrics", "a", ""), ("token", "a", "three"),
                                   ("token", "b", "four"), ("done", "b", "")):
            self.bridge.emit(type, stream_id=stream, text=text)
        self.bridge.flush()
        batch = events(self.scripts[0])
        self.assertEqual([e["type"] for e in batch], ["token", "metrics", "token", "token", "done"])
        self.assertEqual([e["text"] for e in batch], ["one two", "", "three", "four", ""])
        self.bridge.flush()
        self.assertEqual(len(self.scripts), 1)

    def test_payload_is_safe_javascript_and_round_trips_unicode(self):
        value = '</script> "\\\n caffè \u2028 \u2029'
        self.bridge.emit("token", stream_id="a", text=value)
        self.bridge.flush()
        self.assertNotIn("</", self.scripts[0])
        self.assertNotIn("\u2028", self.scripts[0])
        self.assertNotIn("\u2029", self.scripts[0])
        self.assertIn("caffè", self.scripts[0])
        self.assertEqual(events(self.scripts[0])[0]["text"], value)

    def test_close_drops_pending_and_future_events(self):
        self.bridge.emit("done")
        self.bridge.close()
        self.assertFalse(self.bridge.emit("error", message="late"))
        self.bridge.flush()
        self.assertEqual(self.scripts, [])

    def test_evaluate_errors_are_logged_and_next_batch_can_be_delivered(self):
        def fail(script):
            raise RuntimeError("window unavailable")
        bridge = Bridge(fail, autostart=False)
        self.addCleanup(bridge.close)
        bridge.emit("done")
        with self.assertLogs("webui.bridge", level="ERROR"):
            bridge.flush()

    def test_workers_only_enqueue_and_one_flusher_delivers(self):
        delivered = threading.Event()
        owners = []
        def evaluate(script):
            owners.append(threading.current_thread().name)
            self.assertEqual(sum(len(e["text"]) for e in events(script)), 200)
            delivered.set()
        bridge = Bridge(evaluate, autostart=False)
        self.addCleanup(bridge.close)
        workers = [threading.Thread(target=lambda: [bridge.emit("token", stream_id="a", text="x")
                                                   for _ in range(50)]) for _ in range(4)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(owners, [])
        bridge.start()
        self.assertTrue(delivered.wait(2))
        bridge.close()
        self.assertEqual(owners, ["webui-events"])


if __name__ == "__main__":
    unittest.main()
