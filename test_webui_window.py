"""Window lifecycle, lock recovery and delivery tested without native windows."""

import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import test_webui_app as fixtures
from test_agent_support import isolate_agent_data
from test_webui_launch import Hook
from webui import settings
from webui.app import main
from webui.bridge import Bridge
from webui.desktop import Desktop
from webui.instance import SingleInstance
from webui.window_state import saved_theme, visible_rectangle


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.directory = isolate_agent_data(self)

    def test_rectangles_on_connected_screens_only(self):
        screens = [SimpleNamespace(x=0, y=0, width=1920, height=1080),
                   SimpleNamespace(x=-1280, y=0, width=1280, height=1024)]
        value = dict(x=-1200, y=20, width=1000, height=800)
        self.assertEqual(visible_rectangle(value, screens), value)
        self.assertEqual(visible_rectangle(value, screens[:1]), {**value, "x": 0})
        for invalid in (None, {}, {**value, "x": True}):
            self.assertEqual(visible_rectangle(invalid, screens), {})
        self.assertEqual(visible_rectangle(value, []), {})

    def test_real_laptop_rectangle_is_clamped_to_the_work_area(self):
        screen = SimpleNamespace(x=0, y=0, width=1536, height=960,
            frame=SimpleNamespace(X=0, Y=0, Width=1536, Height=920))
        value = dict(x=455, y=160, width=1280, height=840)
        self.assertEqual(visible_rectangle(value, [screen]), dict(x=256, y=80, width=1280, height=840))
        self.assertEqual(visible_rectangle(dict(x=455, y=160, width=2000, height=1200), [screen]),
                         dict(x=0, y=0, width=1536, height=920))
        self.assertEqual(visible_rectangle(dict(x=-4000, y=-2000, width=800, height=600), [screen]),
                         dict(x=0, y=0, width=800, height=600))

    def test_second_process_signals_owner_and_lock_survives_owner_exit(self):
        owner = SingleInstance(self.directory, interval=0.01)
        self.addCleanup(owner.close)
        self.assertTrue(owner.acquire())
        signal = threading.Event()
        owner.start(signal.set)
        # Use a real second process: an in-process mutex is not sufficient.
        code = ("import sys; from webui.instance import SingleInstance; "
                "instance=SingleInstance(sys.argv[1]); "
                "print(instance.acquire()); instance.close()")
        result = subprocess.run([sys.executable, "-c", code, str(self.directory)],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False")
        self.assertTrue(signal.wait(2))
        owner.close()
        other = SingleInstance(self.directory)
        self.addCleanup(other.close)
        self.assertTrue(other.acquire())
        other.close()
        # Simulate a process that exits without explicitly unlocking.
        crashed = subprocess.run([sys.executable, "-c", code.replace("; instance.close()", ""), str(self.directory)],
            cwd=Path(__file__).parent, capture_output=True, text=True, timeout=10)
        self.assertEqual(crashed.stdout.strip(), "True", crashed.stderr)
        self.assertTrue(other.acquire())

    def test_signals_and_theme_are_delivered_by_bridge_only(self):
        window = SimpleNamespace(restore=Mock(), show=Mock(), evaluate_js=Mock())
        titlebar = Mock()
        desktop = Desktop(titlebar=titlebar)
        desktop.main_window = window
        bridge = Bridge(desktop.evaluate, on_flush=desktop.flush, autostart=False)
        self.addCleanup(bridge.close)
        worker = threading.Thread(target=desktop.request_activation)
        worker.start()
        worker.join()
        desktop.set_theme("Night")
        window.show.assert_not_called()
        titlebar.assert_not_called()
        bridge.flush()
        window.restore.assert_called_once_with()
        window.show.assert_called_once_with()
        titlebar.assert_called_once_with(window, "Night")
        # Native actions are processed even when the JS event queue is empty.
        window.evaluate_js.assert_not_called()
        bridge.flush()
        window.show.assert_called_once()
        bridge.close()
        desktop.request_activation()
        bridge.flush()
        window.show.assert_called_once()

    def test_launch_restores_saves_and_secondary_does_not_construct_ui(self):
        value = dict(x=50, y=60, width=900, height=700)
        settings.save(self.directory, {"window": value, "idle_unload_minutes": 2})
        events = SimpleNamespace(**{name: Hook() for name in ("loaded", "moved", "resized", "closing")})
        window = SimpleNamespace(events=events, evaluate_js=Mock(), **value)
        api = SimpleNamespace(_ensure_engine=Mock(), _close=Mock(), _start_status=Mock(), set_window_state=Mock())
        view = SimpleNamespace(create_window=Mock(return_value=window),
            screens=[SimpleNamespace(x=0, y=0, width=1920, height=1080)])
        def start(callback, **options):
            events.loaded.callback()
            window.x = 80
            events.moved.callback(80, 60)
            callback()
        view.start = start
        with patch("webui.app.Api", return_value=api), patch("webui.app.importlib.import_module", return_value=view):
            self.assertEqual(main(), 0)
            for key, item in value.items():
                self.assertEqual(view.create_window.call_args.kwargs[key], item)
            api.set_window_state.assert_called_once_with({**value, "x": 80})
            owner = SingleInstance(self.directory)
            self.addCleanup(owner.close)
            self.assertTrue(owner.acquire())
            view.create_window.reset_mock()
            self.assertEqual(main(), 0)
            view.create_window.assert_not_called()

    def test_theme_reads_existing_appearance_format(self):
        self.assertEqual(saved_theme(self.directory), "Night")
        path = self.directory / "appearance.settings"
        path.write_text(json.dumps({"theme": "Day", "style": "Modern"}), encoding="utf-8")
        self.assertEqual(saved_theme(self.directory), "Day")
        path.write_text("broken", encoding="utf-8")
        self.assertEqual(saved_theme(self.directory), "Night")

    def test_closing_main_window_closes_preview_before_native_loop_exits(self):
        preview = SimpleNamespace(destroy=Mock())
        desktop = Desktop(create_window=Mock(return_value=preview))
        desktop.open_preview("file:///example.html")
        events = SimpleNamespace(loaded=Hook(), closing=Hook())
        window = SimpleNamespace(events=events, x=10, y=20, width=800, height=600)
        api = SimpleNamespace(_ensure_engine=Mock(), _close=Mock(), _start_status=Mock(), set_window_state=Mock())
        view = SimpleNamespace(create_window=Mock(return_value=window))
        def start(*args, **kwargs):
            events.closing.callback()
            preview.destroy.assert_called_once_with()
        view.start = start
        with patch("webui.app.Api", return_value=api), patch("webui.app.Desktop", return_value=desktop), \
                patch("webui.app.importlib.import_module", return_value=view):
            self.assertEqual(main(), 0)
        with self.assertRaises(RuntimeError):
            desktop.open_preview("file:///later.html")


    def test_compute_window_is_single_remembers_normal_size_and_closes_with_main(self):
        hooks = SimpleNamespace(moved=Hook(), resized=Hook(), closing=Hook(), closed=Hook())
        compute = SimpleNamespace(events=hooks, x=1600, y=40, width=960.0, height=540.0,
                                  destroy=Mock(), restore=Mock(), show=Mock(), toggle_fullscreen=Mock())
        create = Mock(return_value=compute)
        desktop = Desktop(create_window=create)
        saved, closed = [], Mock()
        self.assertTrue(desktop.open_compute("file:///compute-window.html", "api", {"width": 800, "height": 480}, saved.append, closed))
        self.assertEqual(create.call_args.kwargs["min_size"], (480, 320))
        self.assertEqual(create.call_args.kwargs["width"], 800)
        self.assertFalse(desktop.open_compute("file:///compute-window.html", "api", {}, saved.append, closed))
        compute.show.assert_called_once_with()
        hooks.moved.callback()
        self.assertEqual(saved, [dict(x=1600, y=40, width=960, height=540)])
        self.assertTrue(desktop.toggle_compute_fullscreen())
        hooks.resized.callback()
        self.assertEqual(len(saved), 1)
        self.assertFalse(desktop.toggle_compute_fullscreen())
        hooks.closed.callback()
        closed.assert_called_once_with()
        with self.assertRaises(RuntimeError):
            desktop.toggle_compute_fullscreen()
        desktop.open_compute("file:///compute-window.html", "api", {}, saved.append, closed)
        desktop.close()
        compute.destroy.assert_called_once_with()
        with self.assertRaises(RuntimeError):
            desktop.open_compute("file:///compute-window.html", "api", {}, saved.append, closed)

class WindowApiTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_geometry_is_saved_without_losing_other_settings_and_is_validated(self):
        self.api.set_idle_unload_minutes(3)
        value = dict(x=40, y=50, width=900, height=700)
        self.assertTrue(self.api.set_window_state(value)["ok"])
        stored = settings.load(self.store.directory)
        self.assertEqual(stored["window"], value)
        self.assertEqual(stored["idle_unload_minutes"], 3)
        self.assertEqual(self.api.state()["settings"]["window"], value)
        self.assertFalse(self.api.set_window_state({**value, "x": False})["ok"])
        self.assertFalse(self.api.set_window_state({**value, "width": -1})["ok"])


if __name__ == "__main__":
    unittest.main()
