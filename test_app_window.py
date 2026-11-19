"""Opening the web workspace in its own app window (webui/app_window.py)."""

import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from webui import app_window


class FindBrowserTests(unittest.TestCase):
    def test_windows_prefers_the_default_browser_then_edge(self):
        environ = {"PROGRAMFILES": r"C:\PF", "PROGRAMFILES(X86)": r"C:\PF86", "LOCALAPPDATA": r"C:\Local"}
        edge = os.path.join(r"C:\PF86", "Microsoft", "Edge", "Application", "msedge.exe")
        chrome = os.path.join(r"C:\PF", "Google", "Chrome", "Application", "chrome.exe")
        installed = {edge, chrome}.__contains__
        nothing = dict(glob=lambda pattern: [], app_path=lambda exe: None)
        self.assertEqual(app_window.find_app_browser("windows", environ, isfile=installed, **nothing), edge)
        self.assertEqual(app_window.find_app_browser("windows", environ, isfile=installed, preferred="chrome", **nothing), chrome)
        self.assertIsNone(app_window.find_app_browser("windows", environ, isfile=lambda path: False, **nothing))

    def test_newer_edge_layouts_and_app_paths_are_found_but_never_the_webview_runtime(self):
        environ = {"PROGRAMFILES(X86)": r"C:\PF86"}
        core = os.path.join(r"C:\PF86", "Microsoft", "EdgeCore", "{}", "msedge.exe")
        versions = [core.format("157.0.4341.46"), core.format("157.0.4341.60"), core.format("99.0.1.1")]
        found = app_window.find_app_browser("windows", environ, isfile=lambda path: True, preferred="edge",
                                            glob=lambda pattern: versions if "EdgeCore" in pattern else [],
                                            app_path=lambda exe: None)
        self.assertEqual(found, os.path.join(r"C:\PF86", "Microsoft", "Edge", "Application", "msedge.exe"))
        found = app_window.find_app_browser("windows", environ, isfile=lambda path: "EdgeCore" in path, preferred="edge",
                                            glob=lambda pattern: versions if "EdgeCore" in pattern else [],
                                            app_path=lambda exe: None)
        self.assertEqual(found, core.format("157.0.4341.60"))
        webview = r"C:\PF86\Microsoft\EdgeWebView\Application\157.0\msedge.exe"
        found = app_window.find_app_browser("windows", {}, isfile=lambda path: True, glob=lambda pattern: [],
                                            app_path=lambda exe: webview if exe == "msedge.exe" else None)
        self.assertIsNone(found)
        self.assertEqual(app_window.find_app_browser("windows", {}, isfile=lambda path: True, glob=lambda pattern: [],
                                                     app_path=lambda exe: r"D:\Apps\chrome.exe" if exe == "chrome.exe" else None),
                         r"D:\Apps\chrome.exe")

    def test_linux_and_mac_find_chromium_browsers(self):
        found = app_window.find_app_browser("linux", {}, which=lambda name: f"/usr/bin/{name}" if name == "chromium" else None,
                                            isfile=lambda path: True)
        self.assertEqual(found, "/usr/bin/chromium")
        mac = app_window.find_app_browser("mac", {}, isfile=lambda path: "Chrome" in path)
        self.assertEqual(mac, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


class OpenTests(unittest.TestCase):
    def test_opens_this_computers_workspace_as_an_app_window(self):
        started = []
        opened = app_window.open_app_window("http://127.0.0.1:8765/#launch=abc", browser="browser.exe",
                                            popen=lambda command, **options: started.append((command, options)))
        self.assertTrue(opened)
        command, options = started[0]
        self.assertEqual(command, ["browser.exe", "--app=http://127.0.0.1:8765/#launch=abc", "--new-window"])
        self.assertEqual(options["stdin"], subprocess.DEVNULL)

    def test_only_this_computers_workspace_can_be_opened(self):
        for url in ("https://example.com/", "http://192.168.1.5:8765/", "http://localhost.example:80/",
                    "file:///C:/x.html", "http://127.0.0.1/", "javascript:alert(1)", "http://[::1"):
            with self.assertRaises(ValueError, msg=url):
                app_window.open_app_window(url, browser="browser.exe", popen=lambda *a, **k: self.fail("started"))
        self.assertTrue(app_window.own_address("http://[::1]:8765/#launch=x"))

    def test_the_internet_gate_checks_the_link_like_any_other(self):
        seen = []

        def hook(event, args):
            if event == "webbrowser.open":
                seen.append(args[0])
        sys.addaudithook(hook)
        app_window.open_app_window("http://127.0.0.1:9/#launch=x", browser="b", popen=lambda *a, **k: None)
        self.assertIn("http://127.0.0.1:9/#launch=x", seen)

    def test_without_a_suitable_browser_the_caller_uses_a_tab(self):
        # Neither the default browser (Edge on GitHub's Windows machines) nor a search finds one.
        with patch.object(app_window, "default_browser_path", return_value=None), \
                patch.object(app_window, "find_app_browser", return_value=None):
            self.assertFalse(app_window.open_app_window("http://127.0.0.1:8765/", popen=lambda *a, **k: self.fail("started")))


if __name__ == "__main__":
    unittest.main()
