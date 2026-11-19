"""Injected desktop operations; pywebview is imported only on actual use."""

import importlib
import threading


class Desktop:
    def __init__(self, create_window=None, folder_dialog=None):
        self._create_window = create_window
        self._preview = None
        self._lock = threading.RLock()
        self._folder_dialog = folder_dialog
        self.main_window = None

    def choose_folder(self):
        if self._folder_dialog is not None:
            return self._folder_dialog()
        if self.main_window is None:
            raise RuntimeError("The main window is not ready for a folder dialog.")
        webview = importlib.import_module("webview")
        return self.main_window.create_file_dialog(webview.FOLDER_DIALOG)

    def open_preview(self, uri):
        with self._lock:
            if self._preview is None:
                create = self._create_window or importlib.import_module("webview").create_window
                window = create("HTML preview", uri, js_api=None, text_select=True)
                self._preview = window
                if hasattr(window, "events"):
                    def closed():
                        with self._lock:
                            if self._preview is window:
                                self._preview = None
                    window.events.closed += closed
            else:
                self._preview.load_url(uri)

    def close(self):
        with self._lock:
            window, self._preview = self._preview, None
        if window is not None:
            window.destroy()
