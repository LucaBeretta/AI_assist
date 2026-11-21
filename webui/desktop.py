"""Injected desktop operations; pywebview is imported only on actual use."""

import importlib
import logging
import sys
import threading


log = logging.getLogger(__name__)


def native_titlebar(window, theme):
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        handle = window.native.Handle.ToInt64()
        function = ctypes.WinDLL("dwmapi").DwmSetWindowAttribute
        function.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        function.restype = ctypes.c_long
        value = ctypes.c_int(theme in {"Night", "Dark"})
        return function(handle, 20, ctypes.byref(value), ctypes.sizeof(value)) == 0
    except Exception:
        log.debug("Native title bar appearance is unavailable", exc_info=True)
        return False


def window_rectangle(window):
    """A native window's rectangle as integers: macOS pywebview reports width/height as floats."""
    return {key: int(round(getattr(window, key))) for key in ("x", "y", "width", "height")}


class Desktop:
    def __init__(self, create_window=None, folder_dialog=None, titlebar=None, save_dialog=None):
        self._create_window = create_window
        self._preview = None
        self._compute = None
        self._compute_fullscreen = False
        self._lock = threading.RLock()
        self._folder_dialog = folder_dialog
        self._save_dialog = save_dialog
        self.main_window = None
        self._activation = threading.Event()
        self._quit = threading.Event()
        self._theme = None
        self._titlebar = titlebar or native_titlebar
        self._closed = False

    def request_quit(self):
        self._quit.set()

    def request_activation(self):
        self._activation.set()

    def set_theme(self, theme):
        with self._lock:
            self._theme = theme

    def flush(self):
        """Native actions share the bridge's delivery owner, even without JS events."""
        if self.main_window is None:
            return
        if self._quit.is_set():
            self._quit.clear()
            self.main_window.destroy()
            return
        with self._lock:
            theme, self._theme = self._theme, None
        if theme is not None:
            self._titlebar(self.main_window, theme)
        if self._activation.is_set():
            self._activation.clear()
            self.main_window.restore()
            self.main_window.show()
            # Windows does not raise a background window on show(); a brief on-top toggle brings it forward.
            try:
                self.main_window.on_top = True
                self.main_window.on_top = False
            except Exception:
                pass

    def evaluate(self, script):
        return self.main_window.evaluate_js(script)

    def choose_folder(self):
        if self._folder_dialog is not None:
            return self._folder_dialog()
        if self.main_window is None:
            raise RuntimeError("The main window is not ready for a folder dialog.")
        webview = importlib.import_module("webview")
        return self.main_window.create_file_dialog(webview.FOLDER_DIALOG)

    def save_file(self, suggested_name):
        if self._save_dialog is not None:
            return self._save_dialog(suggested_name)
        if self.main_window is None:
            raise RuntimeError("The main window is not ready for a save dialog.")
        webview = importlib.import_module("webview")
        return self.main_window.create_file_dialog(webview.SAVE_DIALOG, save_filename=suggested_name)

    def open_preview(self, uri):
        with self._lock:
            if self._closed:
                raise RuntimeError("The desktop is closing.")
            window = self._preview
        # Native methods can dispatch callbacks on another thread. Do not hold
        # the callback lock while calling into the native event loop.
        if window is None:
            create = self._create_window or importlib.import_module("webview").create_window
            window = create("HTML preview", uri, js_api=None, text_select=True)
            with self._lock:
                closed = self._closed
                if not closed:
                    self._preview = window
            if closed:
                window.destroy()
                raise RuntimeError("The desktop closed while opening the preview.")
            if hasattr(window, "events"):
                def closed():
                    with self._lock:
                        if self._preview is window:
                            self._preview = None
                window.events.closed += closed
        else:
            window.load_url(uri)

    def screens(self):
        try:
            return list(getattr(importlib.import_module("webview"), "screens", []) or [])
        except Exception:
            log.debug("Screen list is unavailable", exc_info=True)
            return []

    def open_compute(self, uri, js_api, geometry, remember, closed):
        """Open (or raise) the separate Compute window for a second screen.

        ``remember(rectangle)`` receives the normal-window geometry after moves
        and resizes, never the fullscreen size. ``closed()`` runs once the
        native window is gone. Returns True when a new window was created.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("The desktop is closing.")
            window = self._compute
        if window is not None:
            try:
                window.restore()
                window.show()
            except Exception:
                log.debug("Could not raise the Compute window", exc_info=True)
            return False
        create = self._create_window or importlib.import_module("webview").create_window
        window = create("AI Assist · Mission Control", uri, js_api=js_api, text_select=True,
                        min_size=(480, 320), background_color="#0D0F12", **geometry)
        with self._lock:
            closing = self._closed
            if not closing:
                self._compute, self._compute_fullscreen = window, False
        if closing:
            window.destroy()
            raise RuntimeError("The desktop closed while opening the Compute window.")
        events = getattr(window, "events", None)
        if events is None:
            return True
        def save(*args):
            with self._lock:
                if self._compute is not window or self._compute_fullscreen:
                    return
            try:
                remember(window_rectangle(window))
            except Exception:
                log.debug("Could not remember the Compute window rectangle", exc_info=True)
        def gone(*args):
            with self._lock:
                if self._compute is not window:
                    return
                self._compute, self._compute_fullscreen = None, False
            closed()
        for name in ("moved", "resized", "closing"):
            hook = getattr(events, name, None)
            if hook is not None:
                hook += save
        if getattr(events, "closed", None) is not None:
            events.closed += gone
        return True

    def toggle_compute_fullscreen(self):
        with self._lock:
            window = self._compute
            if window is None:
                raise RuntimeError("The Compute window is not open.")
            self._compute_fullscreen = not self._compute_fullscreen
            fullscreen = self._compute_fullscreen
        window.toggle_fullscreen()
        return fullscreen

    def close(self):
        with self._lock:
            self._closed = True
            window, self._preview = self._preview, None
            compute, self._compute = self._compute, None
        for item in (window, compute):
            if item is not None:
                item.destroy()
