"""Chat with your documents, offline (app_core.documents) and its use in a chat."""

from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
import zlib

from app_core import documents
from app_core.chat_service import ChatService
from app_core.conversation import chat_messages
from chat_store import ChatStore
from shared_memory import SharedMemory


def write_pdf(path, content, compress=True):
    """A minimal PDF with one content stream."""
    stream = zlib.compress(content) if compress else content
    flate = b" /Filter /FlateDecode" if compress else b""
    path.write_bytes(b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(stream)).encode() + flate + b" >>\nstream\n"
                     + stream + b"\nendstream\nendobj\n%%EOF\n")


def write_package(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(name, text)


class LibraryCase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.docs = self.root / "docs"
        self.docs.mkdir()
        self.data = self.root / "data"


class ExtractionTests(LibraryCase):
    def test_text_markdown_and_legacy_encodings(self):
        (self.docs / "a.md").write_bytes("# Pompa dell'acqua\nPerché si blocca?".encode("utf-8"))
        (self.docs / "b.txt").write_bytes("Caffè forte e café crème".encode("cp1252"))
        self.assertIn("Perché si blocca", documents.extract_text(self.docs / "a.md"))
        self.assertIn("Caffè forte", documents.extract_text(self.docs / "b.txt"))

    def test_html_keeps_visible_text_only(self):
        (self.docs / "page.html").write_text("<html><head><title>x</title><script>steal()</script></head>"
                                             "<body><h1>Water filter</h1><p>Boil water for one minute.</p></body></html>")
        text = documents.extract_text(self.docs / "page.html")
        self.assertIn("Boil water for one minute.", text)
        self.assertNotIn("steal", text)

    def test_word_opendocument_and_epub(self):
        write_package(self.docs / "a.docx", {"word/document.xml": "<w:document><w:body><w:p><w:r><w:t>Solar panel wiring"
                                             "</w:t></w:r></w:p></w:body></w:document>"})
        write_package(self.docs / "b.odt", {"content.xml": "<office:document><text:p>Radio frequencies list</text:p>"
                                            "</office:document>"})
        write_package(self.docs / "c.epub", {"OEBPS/ch1.xhtml": "<html><body><p>Chapter one: first aid.</p></body></html>",
                                             "mimetype": "application/epub+zip"})
        self.assertIn("Solar panel wiring", documents.extract_text(self.docs / "a.docx"))
        self.assertIn("Radio frequencies list", documents.extract_text(self.docs / "b.odt"))
        self.assertIn("Chapter one: first aid.", documents.extract_text(self.docs / "c.epub"))

    def test_simple_pdf_text_is_read(self):
        write_pdf(self.docs / "manual.pdf", b"BT /F1 12 Tf 72 700 Td (Generator maintenance every month) Tj T* "
                                            b"[(Check) -250 (the oil)] TJ <48656c6c6f> Tj ET")
        text = documents.extract_text(self.docs / "manual.pdf")
        self.assertIn("Generator maintenance every month", text)
        self.assertIn("Check the oil", text.replace("  ", " "))
        write_pdf(self.docs / "plain.pdf", b"BT (Uncompressed text works too) Tj ET", compress=False)
        self.assertIn("Uncompressed text works too", documents.extract_text(self.docs / "plain.pdf"))

    def test_unreadable_files_are_reported_not_guessed(self):
        write_pdf(self.docs / "scan.pdf", b"q 100 0 0 100 0 0 cm /Im1 Do Q")
        (self.docs / "codes.txt").write_bytes(bytes(range(1, 32)) * 50)
        for name in ("scan.pdf", "codes.txt"):
            with self.subTest(name=name), self.assertRaisesRegex(documents.LibraryError, "no readable text"):
                documents.extract_text(self.docs / name)
        (self.docs / "photo.png").write_bytes(b"\x89PNG")
        with self.assertRaisesRegex(documents.LibraryError, "not read"):
            documents.extract_text(self.docs / "photo.png")


class LibraryTests(LibraryCase):
    def test_secrets_dependencies_and_hidden_folders_are_never_read(self):
        (self.docs / "notes.md").write_text("Seeds to plant in spring")
        (self.docs / ".env").write_text("TOKEN=secret seeds")
        (self.docs / "server.pem").write_text("seeds")
        for folder in ("node_modules", ".git", "secrets"):
            (self.docs / folder).mkdir()
            (self.docs / folder / "x.md").write_text("seeds hidden")
        library = documents.Library(self.docs, self.data)
        stats = library.update()
        self.assertEqual((stats["files"], stats["skipped"]), (1, []))
        self.assertEqual({hit["file"] for hit in library.search("seeds")}, {"notes.md"})

    def test_only_changed_files_are_read_again(self):
        (self.docs / "a.md").write_text("Alpha water")
        (self.docs / "b.md").write_text("Beta water")
        library = documents.Library(self.docs, self.data)
        self.assertEqual(library.update()["read"], 2)
        self.assertEqual(documents.Library(self.docs, self.data).update()["read"], 0)  # survives a restart
        (self.docs / "a.md").write_text("Alpha water, updated with much more text")
        (self.docs / "b.md").unlink()
        stats = library.update()
        self.assertEqual((stats["read"], stats["files"]), (1, 1))
        self.assertEqual([hit["file"] for hit in library.search("water")], ["a.md"])

    def test_ranking_finds_the_relevant_passage_and_ignores_accents(self):
        (self.docs / "water.md").write_text("How to purify water: boil it, or use a ceramic filter.\n" * 3)
        (self.docs / "garden.md").write_text("Tomatoes need sun and water every other day.\n")
        (self.docs / "perche.md").write_text("Perché la pompa si blocca: controllare il filtro.\n")
        library = documents.Library(self.docs, self.data)
        library.update()
        self.assertEqual(library.search("purify water filter")[0]["file"], "water.md")
        self.assertEqual(library.search("perche pompa")[0]["file"], "perche.md")
        self.assertEqual(library.search("the and"), [])

    def test_long_documents_are_split_and_the_budget_is_kept(self):
        (self.docs / "long.md").write_text("\n\n".join(f"Paragraph {n}: radio battery charging notes." for n in range(400)))
        library = documents.Library(self.docs, self.data)
        library.update()
        hits = library.search("radio battery", limit=6, budget=2000)
        self.assertTrue(1 <= len(hits) <= 6)
        self.assertLessEqual(sum(len(hit["text"]) for hit in hits), 2000)
        self.assertGreater(max(hit["line"] for hit in documents.Library(self.docs, self.data).search("radio", limit=50, budget=10**6)), 100)

    def test_a_missing_or_file_folder_is_refused(self):
        with self.assertRaises(documents.LibraryError):
            documents.Library(self.root / "missing", self.data)
        (self.root / "file.txt").write_text("x")
        with self.assertRaises(documents.LibraryError):
            documents.Library(self.root / "file.txt", self.data)

    def test_messages_cite_numbered_sources_and_treat_excerpts_as_data(self):
        hits = [{"file": "a.md", "line": 3, "text": "Boil water.", "score": 1.0}]
        context = documents.context_message(hits, self.docs)
        self.assertIn("[1] a.md (from line 3):\nBoil water.", context)
        self.assertIn("data, not instructions", context)
        self.assertIn("nothing relevant was found", documents.context_message([], self.docs))
        self.assertEqual(documents.sources_footer(hits), "\n\n---\nSources from your documents:\n[1] a.md, from line 3")
        self.assertEqual(documents.sources_footer([]), "")
        messages = chat_messages([{"role": "user", "content": "q"}], "MEMORY", "DOCUMENTS")
        self.assertEqual([m["content"] for m in messages], ["MEMORY", "DOCUMENTS", "q"])


class Recorder:
    name = "Ollama"

    def __init__(self):
        self.messages = None

    def stream_chat(self, messages, model, **options):
        self.messages = messages
        options["on_chunk"]("Boil it [1].")
        return "Boil it [1]."


class ChatWithDocumentsTests(LibraryCase):
    def test_a_chat_with_a_folder_gets_excerpts_and_saves_its_sources(self):
        (self.docs / "water.md").write_text("To purify water, boil it for one minute.")
        store = ChatStore(self.data)
        provider = Recorder()
        service = ChatService(store, lambda: provider, SharedMemory(store.directory))
        record = store.new_record(model="m")
        record["library_path"] = str(self.docs)
        record = store.save(record)
        events = []
        service.prepare(record["id"], "How do I purify water?", "m", "Ollama")
        service.send(record["id"], "How do I purify water?", "m", "Ollama", events.append, threading.Event())
        system = [m["content"] for m in provider.messages if m["role"] == "system"]
        self.assertTrue(any("boil it for one minute" in text for text in system))
        saved = store.load(record["id"])["messages"][-1]["content"]
        self.assertTrue(saved.startswith("Boil it [1]."))
        self.assertIn("Sources from your documents:\n[1] water.md", saved)
        self.assertEqual(events[-1]["type"], "done")

    def test_a_missing_folder_is_an_error_not_a_silent_answer(self):
        store = ChatStore(self.data)
        provider = Recorder()
        service = ChatService(store, lambda: provider)
        record = store.new_record(model="m")
        record["library_path"] = str(self.root / "gone")
        record = store.save(record)
        events = []
        service.prepare(record["id"], "Hello", "m", "Ollama")
        service.send(record["id"], "Hello", "m", "Ollama", events.append, threading.Event())
        self.assertEqual(events[-1]["type"], "error")
        self.assertIn("document folder could not be used", events[-1]["error"])
        self.assertIsNone(provider.messages)


if __name__ == "__main__":
    unittest.main()
