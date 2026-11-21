"""Real PDF text extraction with bounded, local, windowless workers."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

try:
    from pypdf import PdfWriter
    from pypdf.actions import JavaScript
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
except ModuleNotFoundError as error:
    if error.name != "pypdf":
        raise
    PdfWriter = None

import pdf_text
import test_webui_app as fixtures
from test_webui_attachments import encoded


def document(texts, *, encrypted=False, compressed=False, padding=0):
    writer = PdfWriter()
    for text in texts:
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
            DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        literal = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("ascii")
        stream.set_data(b"BT /F1 12 Tf 10 100 Td (" + literal + b") Tj ET\n" + b" " * padding)
        page[NameObject("/Contents")] = writer._add_object(stream.flate_encode() if compressed else stream)
    if encrypted:
        writer.encrypt("secret")
    writer.add_open_action(JavaScript("app.alert('This must never execute');"))
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


@unittest.skipIf(PdfWriter is None, "Optional PDF support needs pypdf")
class PdfAttachmentTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_text_pdf_is_saved_used_as_context_and_exported_without_vision(self):
        data = document(["First page", "Second page"], compressed=True)
        chat = self.api.new_chat()["chat"]["id"]
        result = self.api.attach(chat, "report.pdf", encoded(data))
        self.assertTrue(result["ok"], result)
        item = result["attachment"]
        self.assertEqual(item["kind"], "text")
        self.assertIn("[Page 1]\nFirst page", item["text"])
        self.assertIn("[Page 2]\nSecond page", item["text"])
        with patch("webui.app.fetch_model_capabilities", side_effect=AssertionError("PDF does not need vision")):
            stream = self.api.send(chat, "Summarize", "text-model", None)["stream_id"]
            self.events.wait("done", stream)
        self.assertIn("First page", self.calls[-1][0][-1]["content"])
        self.assertNotIn("images", self.calls[-1][0][-1])
        archive_path = self.store.directory / "backup.zip"
        self.store.export_all(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertEqual(archive.read(f"chats/{chat}/attachments/{item['id']}"), data)

    def test_private_pdf_uses_no_attachment_or_temporary_files(self):
        chat = self.api.new_chat(incognito=True)["chat"]["id"]
        with patch("tempfile.NamedTemporaryFile", side_effect=AssertionError("No PDF temporary file")):
            result = self.api.attach(chat, "private.pdf", encoded(document(["Private text"])))
        self.assertTrue(result["ok"], result)
        self.assertEqual(list(self.store.directory.iterdir()), [])
        self.api._close()
        self.assertEqual(self.api._chats._incognito, {})

    def test_encrypted_blank_malformed_and_excessive_page_documents_are_clear_errors(self):
        chat = self.api.new_chat()["chat"]["id"]
        cases = [(document(["Encrypted"], encrypted=True), "Password-protected"),
                 (document([""]), "OCR"), (b"%PDF-1.7\nbroken", "damaged"),
                 (document([""] * 101), "at most 100 pages")]
        for data, reason in cases:
            with self.subTest(reason=reason):
                result = self.api.attach(chat, "invalid.pdf", encoded(data))
                self.assertFalse(result["ok"], result)
                self.assertIn(reason, result["error"])
        self.assertFalse((self.store.directory / chat).exists())

    def test_expanded_content_and_extracted_text_are_bounded(self):
        chat = self.api.new_chat()["chat"]["id"]
        for data, reason in ((document(["x" * 65000], compressed=True), "text exceeds"),
                             (document(["small"], compressed=True, padding=600000), "resource limits")):
            result = self.api.attach(chat, "large.pdf", encoded(data))
            self.assertFalse(result["ok"], result)
            self.assertIn(reason, result["error"])
        self.assertFalse((self.store.directory / chat).exists())

    def test_private_keys_and_pdf_path_traversal_are_rejected(self):
        chat = self.api.new_chat()["chat"]["id"]
        data = encoded(document(["-----BEGIN PRIVATE KEY-----"]))
        self.assertFalse(self.api.attach(chat, "key.pdf", data)["ok"])
        self.assertFalse(self.api.attach(chat, "../report.pdf", data)["ok"])

    def test_timeout_and_missing_dependency_are_diagnostics(self):
        data = document(["text"])
        with patch("pdf_text.subprocess.run", side_effect=subprocess.TimeoutExpired("pdf", 15)):
            with self.assertRaisesRegex(ValueError, "took too long"):
                pdf_text.extract_pdf(data, 64000)
        result = subprocess.run([sys.executable, "-I", "-S", str(Path(pdf_text.__file__)), "64000"],
            input=data, capture_output=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pip install -r requirements.txt", json.loads(result.stdout)["error"])


class OptionalPdfTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApiTests.setUp(self)

    def test_missing_reader_returns_a_clear_api_error_without_saving_an_attachment(self):
        chat = self.api.new_chat()["chat"]["id"]
        original_run = subprocess.run
        def without_site_packages(args, **options):
            # Hide every site folder, including the user's own one the app may ask for.
            return original_run([args[0], "-S", *(arg for arg in args[1:] if arg != "--user-site")], **options)
        with patch("pdf_text.subprocess.run", side_effect=without_site_packages):
            result = self.api.attach(chat, "document.pdf", encoded(b"%PDF-1.4\n%%EOF"))
        self.assertFalse(result["ok"])
        self.assertIn("PDF support needs pypdf", result["error"])
        self.assertFalse((self.store.directory / chat).exists())
        self.assertNotIn("attachments", self.store.load(chat))

    def test_worker_is_asked_for_the_user_site_folder_only_when_the_app_uses_it(self):
        commands = []

        def fake_run(args, **options):
            commands.append(args)
            return subprocess.CompletedProcess(args, 0, stdout=b'{"ok": true, "text": "text"}')

        user_site = str(Path(tempfile.gettempdir()) / "user-site")
        for enabled, on_path, asked in ((True, True, True), (True, False, False), (False, True, False)):
            with self.subTest(enabled=enabled, on_path=on_path), \
                    patch("pdf_text.site.ENABLE_USER_SITE", enabled), \
                    patch("pdf_text.site.getusersitepackages", return_value=user_site), \
                    patch("pdf_text.sys.path", [user_site] if on_path else []), \
                    patch("pdf_text.subprocess.run", side_effect=fake_run):
                self.assertEqual(pdf_text.extract_pdf(b"%PDF-1.4", 64000), "text")
                self.assertEqual(commands[-1][1], "-I")
                self.assertEqual("--user-site" in commands[-1], asked)

    def test_worker_imports_from_the_user_site_folder_when_asked(self):
        # pip installs into the user's own folder when Python's folder is not writable or with --user.
        with tempfile.TemporaryDirectory() as base:
            environment = dict(os.environ, PYTHONUSERBASE=base)
            folder = subprocess.run([sys.executable, "-I", "-c", "import site; print(site.getusersitepackages())"],
                capture_output=True, text=True, env=environment, check=True).stdout.strip()
            Path(folder, "pypdf").mkdir(parents=True)
            Path(folder, "pypdf", "__init__.py").write_text('raise ValueError("user-site pypdf loaded")\n')

            def worker(*extra):
                # -S hides Python's own site-packages, so only the user site folder can supply pypdf.
                result = subprocess.run([sys.executable, "-I", "-S", str(Path(pdf_text.__file__)), "64000", *extra],
                    input=b"%PDF-1.4\n%%EOF", capture_output=True, timeout=15, env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return json.loads(result.stdout)["error"]

            self.assertIn("PDF support needs pypdf", worker())
            self.assertEqual(worker("--user-site"), "user-site pypdf loaded")


if __name__ == "__main__":
    unittest.main()
