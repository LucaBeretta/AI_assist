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


class Desktop:
    def __init__(self, create_window=None, folder_dialog=None, titlebar=None):
        self._create_window = create_window
        self._preview = None
        self._lock = threading.RLock()
        self._folder_dialog = folder_dialog
        self.main_window = None
        self._activation = threading.Event()
        self._theme = None
        self._titlebar = titlebar or native_titlebar
        self._closed = False

    def request_activation(self):
        self._activation.set()

    def set_theme(self, theme):
        with self._lock:
            self._theme = theme

    def flush(self):
        """Native actions share the bridge's delivery owner, even without JS events."""
        if self.main_window is None:
            return
        with self._lock:
            theme, self._theme = self._theme, None
        if theme is not None:
            self._titlebar(self.main_window, theme)
        if self._activation.is_set():
            self._activation.clear()
            self.main_window.restore()
            self.main_window.show()

    def evaluate(self, script):
        return self.main_window.evaluate_js(script)

    def choose_folder(self):
        if self._folder_dialog is not None:
            return self._folder_dialog()
        if self.main_window is None:
            raise RuntimeError("The main window is not ready for a folder dialog.")
        webview = importlib.import_module("webview")
        return self.main_window.create_file_dialog(webview.FOLDER_DIALOG)

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

    def close(self):
        with self._lock:
            self._closed = True
            window, self._preview = self._preview, None
        if window is not None:
            window.destroy()
