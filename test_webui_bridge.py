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
        attempts = []
        def fail(script):
            attempts.append(script)
            if len(attempts) == 1:
                raise RuntimeError("window unavailable")
            self.scripts.append(script)
        bridge = Bridge(fail, autostart=False)
        self.addCleanup(bridge.close)
        bridge.emit("done")
        with self.assertLogs("webui.bridge", level="ERROR"):
            bridge.flush()
        bridge.emit("done", stream_id="second")
        bridge.flush()
        self.assertEqual(events(self.scripts[0])[0]["stream_id"], "second")

    def test_invalid_token_cannot_kill_delivery_of_valid_events(self):
        with self.assertLogs("webui.bridge", level="ERROR"):
            self.assertFalse(self.bridge.emit("token", text=None))
            self.assertFalse(self.bridge.emit("metrics", value=float("nan")))
        self.bridge.emit("token", stream_id="a", text="valid")
        self.bridge.flush()
        self.assertEqual(events(self.scripts[0])[0]["text"], "valid")

    def test_close_does_not_wait_forever_for_an_in_flight_evaluation(self):
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def evaluate(script):
            entered.set()
            release.wait(3)
        bridge = Bridge(evaluate)
        self.addCleanup(bridge.close)
        bridge.emit("done")
        self.assertTrue(entered.wait(2))
        with self.assertLogs("webui.bridge", level="WARNING"):
            bridge.close(timeout=0)
        self.assertFalse(bridge.emit("done"))
        release.set()
        bridge.close()

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
