"""Checks for safe, bounded project context and Ollama model metadata."""

import io
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import developer_tools
from developer_tools import (
    build_project_context, extract_code_blocks, fetch_model_capabilities,
    list_project_files, read_project_file,
)


class ProjectToolsTests(unittest.TestCase):
    def test_binary_control_detection_preserves_allowed_whitespace(self):
        for code in range(32):
            data = ("before" + chr(code) + "after").encode("utf-8")
            with self.subTest(code=code):
                if chr(code) in "\t\n\r\f":
                    self.assertEqual(developer_tools._decode_text(data), data.decode("utf-8"))
                else:
                    with self.assertRaisesRegex(ValueError, "Binary"):
                        developer_tools._decode_text(data)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        # The real path: macOS temporary folders sit behind the /var link and Windows can report short (8.3) names.
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / "project"
        self.root.mkdir()

    def write(self, name, contents="print('hello')\n"):
        target = self.root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(contents, bytes):
            target.write_bytes(contents)
        else:
            target.write_text(contents, encoding="utf-8", newline="")
        return target

    def symlink(self, target, name, directory=False):
        link = self.root / name
        try:
            link.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Creating symbolic links is unavailable: {error}")
        return link

    def test_lists_text_with_relative_paths_sizes_and_stable_sorting(self):
        self.write("src/z.py", "pass\n")
        self.write("readme.md", "Read me\n")
        self.write("A.py", "# a\n")
        files = list_project_files(self.root)
        self.assertEqual([item["path"] for item in files], ["A.py", "readme.md", "src/z.py"])
        self.assertEqual(files[0]["size_bytes"], 4)
        self.assertEqual(files, list_project_files(self.root))

    def test_skips_dependencies_caches_generated_files_and_secrets(self):
        excluded = [
            ".git/config", "node_modules/module/index.js", ".venv/main.py",
            "venv/main.py", "__pycache__/cache.py", "build/output.js", "dist/output.js",
            ".env", ".env.local", ".env.production", "credentials.json",
            "credentials/aws.json", "secret.json", "private_key.txt", "id_rsa",
            ".ssh/config", "key.pem", "my.key", ".npmrc", "keys/account.pfx",
        ]
        for name in excluded:
            self.write(name, "sensitive information\n")
        self.write(".env.example", "API_TOKEN=replace-me\n")
        self.write("app.py")
        self.assertEqual([item["path"] for item in list_project_files(self.root)], [".env.example", "app.py"])
        for name in excluded:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "excluded"):
                read_project_file(self.root, name)

    def test_private_key_contents_are_excluded_even_under_an_innocent_name(self):
        for marker in ("RSA PRIVATE KEY", "OPENSSH PRIVATE KEY", "PGP PRIVATE KEY BLOCK"):
            self.write("notes.txt", f"-----BEGIN {marker}-----\nabcdef\n-----END {marker}-----\n")
            self.assertEqual(list_project_files(self.root), [])
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "Private-key"):
                read_project_file(self.root, "notes.txt")

    def test_reads_bom_utf8_and_preserves_line_endings(self):
        self.write("unicode.py", b"\xef\xbb\xbf# caf\xc3\xa9\r\nprint(1)\r\n")
        self.assertEqual(read_project_file(self.root, "unicode.py"), "# caf\u00e9\r\nprint(1)\r\n")

    def test_rejects_binaries_invalid_encoding_and_size_overflow(self):
        for name, contents in (("binary.txt", b"a\x00b"), ("control.txt", b"a\x01b"),
                               ("invalid.txt", b"\xff\xfe\x00a"), ("image.png", b"PNG image")):
            self.write(name, contents)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "binary|Binary|UTF-8"):
                read_project_file(self.root, name)
        self.assertEqual(list_project_files(self.root), [])
        self.write("large.txt", "12345")
        self.assertEqual(read_project_file(self.root, "large.txt", max_bytes=5), "12345")
        with self.assertRaisesRegex(ValueError, "4-byte limit"):
            read_project_file(self.root, "large.txt", max_bytes=4)

    def test_listing_probe_does_not_reject_a_split_multibyte_character(self):
        self.write("unicode.txt", "a" * 8191 + "\u00e9" + "\n")
        self.assertEqual([item["path"] for item in list_project_files(self.root)], ["unicode.txt"])

    def test_rejects_traversal_absolute_paths_and_missing_files(self):
        outside = self.base / "outside.py"
        outside.write_text("private\n", encoding="utf-8")
        for path in ("../outside.py", "sub/../../outside.py", "..\\outside.py",
                     str(outside), "C:\\outside.py", "C:outside.py", "\\\\server\\file.py"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "inside"):
                read_project_file(self.root, path)
        with self.assertRaisesRegex(ValueError, "missing|inaccessible"):
            read_project_file(self.root, "missing.py")
        with self.assertRaisesRegex(ValueError, "regular text file"):
            read_project_file(self.root, ".")

    def test_symlink_escaping_project_is_not_listed_or_read(self):
        outside = self.base / "outside.py"
        outside.write_text("secret\n", encoding="utf-8")
        self.symlink(outside, "linked.py")
        self.assertEqual(list_project_files(self.root), [])
        with self.assertRaisesRegex(ValueError, "inside"):
            read_project_file(self.root, "linked.py")

    def test_resolved_targets_are_checked_without_requiring_symlink_privileges(self):
        outside = self.base / "outside.py"
        outside.write_text("secret\n", encoding="utf-8")
        link = self.write("linked.py", "placeholder\n")
        secret = self.write(".env", "TOKEN=value\n")
        disguised_secret = self.write("ordinary.txt", "placeholder\n")
        original_resolve = Path.resolve

        def resolve_target(path, *args, **kwargs):
            if path == link:
                return original_resolve(outside)
            if path == disguised_secret:
                return original_resolve(secret)
            return original_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve_target):
            self.assertEqual(list_project_files(self.root), [])
            with self.assertRaisesRegex(ValueError, "inside"):
                read_project_file(self.root, "linked.py")
            with self.assertRaisesRegex(ValueError, "excluded"):
                read_project_file(self.root, "ordinary.txt")

    def test_symlink_to_secret_cannot_bypass_filename_exclusion(self):
        target = self.write(".env", "SECRET=value\n")
        self.symlink(target, "ordinary.txt")
        self.assertEqual(list_project_files(self.root), [])
        with self.assertRaisesRegex(ValueError, "excluded"):
            read_project_file(self.root, "ordinary.txt")

    def test_symlink_directories_are_not_traversed(self):
        target = self.write("src/main.py")
        self.symlink(target.parent, "alias", directory=True)
        self.assertEqual([item["path"] for item in list_project_files(self.root)], ["src/main.py"])

    def test_limits_bound_files_traversal_depth_and_probe_bytes(self):
        for index in range(12):
            self.write(f"file{index:02d}.py")
        self.assertEqual(len(list_project_files(self.root, limit=3)), 3)
        with patch.object(developer_tools, "_MAX_SCAN_ENTRIES", 4):
            self.assertLessEqual(len(list_project_files(self.root)), 4)
        with patch.object(developer_tools, "_MAX_PROBE_BYTES", 1):
            self.assertEqual(list_project_files(self.root), [])
        nested = "nested/" * 5 + "last.py"
        self.write(nested)
        with patch.object(developer_tools, "_MAX_SCAN_DEPTH", 2):
            self.assertNotIn(nested, [item["path"] for item in list_project_files(self.root)])

    def test_context_contains_whole_labeled_files_and_explains_skips(self):
        self.write("a.py", "print(1)\n")
        self.write("b.py", "b" * 150)
        self.write("c.py", "pass")
        self.write(".env", "SECRET=do-not-include")
        result = build_project_context(self.root, ["a.py", "./a.py", "b.py", ".env", "c.py"], max_total_chars=100)
        self.assertEqual(result["included"], ["a.py", "c.py"])
        self.assertEqual([item["path"] for item in result["skipped"]], ["b.py", ".env"])
        self.assertIn("context limit", result["skipped"][0]["reason"])
        self.assertIn("--- Project file: a.py ---\nprint(1)\n", result["text"])
        self.assertIn("--- Project file: c.py ---\npass\n", result["text"])
        self.assertLessEqual(len(result["text"]), 100)
        self.assertNotIn("do-not-include", result["text"])

    def test_context_accounts_for_labels_and_default_file_size_limit(self):
        self.write("a.txt", "1")
        expected = "--- Project file: a.txt ---\n1\n"
        self.assertEqual(build_project_context(self.root, ["a.txt"], len(expected))["text"], expected)
        self.assertEqual(build_project_context(self.root, ["a.txt"], len(expected) - 1)["included"], [])
        self.write("large.txt", "x" * 64001)
        result = build_project_context(self.root, ["large.txt"], 100000)
        self.assertEqual(result["included"], [])
        self.assertIn("64,000-byte limit", result["skipped"][0]["reason"])

    def test_invalid_limits_and_roots_are_reported(self):
        for invalid in (0, -1, True, "10"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                list_project_files(self.root, invalid)
            with self.assertRaises(ValueError):
                read_project_file(self.root, "a.py", invalid)
            with self.assertRaises(ValueError):
                build_project_context(self.root, [], invalid)
            with self.assertRaises(ValueError):
                build_project_context(self.root, [], max_file_bytes=invalid)
        with self.assertRaisesRegex(ValueError, "project folder"):
            list_project_files(self.root / "missing")
        with self.assertRaisesRegex(ValueError, "collection"):
            build_project_context(self.root, "a.py")

    def test_pinned_file_limit_and_total_context_budget_are_independent(self):
        self.write("large.txt", "x" * 70000)
        self.assertEqual(build_project_context(self.root, ["large.txt"], 200000)["included"], [])
        context = build_project_context(self.root, ["large.txt"], 200000, max_file_bytes=128000)
        self.assertEqual(context["included"], ["large.txt"])
        self.assertIn("x" * 70000, context["text"])
        context = build_project_context(self.root, ["large.txt"], 8000, max_file_bytes=128000)
        self.assertEqual(context["included"], [])
        self.assertIn("context limit", context["skipped"][0]["reason"])


class CodeBlockTests(unittest.TestCase):
    def test_multiple_fences_preserve_code_and_detect_filename_hints(self):
        text = ('Explanation\n```Python filename="src/main.py"\n'
                'def run():\n    return 1\n```\nMore\n'
                '~~~json title=config.json\r\n{"a": 1}\r\n~~~\r\n')
        blocks = extract_code_blocks(text)
        self.assertEqual(blocks, [
            {"language": "python", "code": "def run():\n    return 1\n", "filename": "src/main.py", "complete": True},
            {"language": "json", "code": '{"a": 1}\r\n', "filename": "config.json", "complete": True},
        ])

    def test_short_or_wrong_fences_inside_block_do_not_close_it(self):
        text = "````markdown\n```python\npass\n```\n~~~\n````\n"
        blocks = extract_code_blocks(text)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["code"], "```python\npass\n```\n~~~\n")
        self.assertTrue(blocks[0]["complete"])

    def test_unclosed_block_is_marked_incomplete_and_retains_final_newline(self):
        for code in ("print(1)", "print(1)\n"):
            with self.subTest(code=code):
                blocks = extract_code_blocks("```python\n" + code)
                self.assertEqual(blocks[0]["code"], code)
                self.assertFalse(blocks[0]["complete"])
        self.assertEqual(extract_code_blocks("````"), [
            {"language": "", "code": "", "filename": None, "complete": False},
        ])

    def test_unlabeled_empty_indented_and_path_labeled_blocks(self):
        self.assertEqual(extract_code_blocks("inline `code` and no fence"), [])
        self.assertEqual(extract_code_blocks("    ```py\n    pass\n    ```\n"), [])
        for label, language, filename in (("", "", None), ("src/main.py", "", "src/main.py"),
                                          ("js app.js", "js", "app.js"), ("{.python}", "python", None)):
            with self.subTest(label=label):
                block = extract_code_blocks(f"  ```{label}\n  ```\n")[0]
                self.assertEqual(block, {"language": language, "filename": filename, "code": "", "complete": True})


class ModelCapabilitiesTests(unittest.TestCase):
    def response(self, value):
        return io.BytesIO(json.dumps(value).encode("utf-8"))

    def test_reads_capabilities_and_architecture_context_with_post_timeout(self):
        payload = {
            "capabilities": ["completion", "tools", "thinking", "tools"],
            "details": {"family": "qwen3", "parameter_size": "8.2B", "quantization_level": "Q4_K_M"},
            "model_info": {"general.architecture": "qwen3", "qwen3.context_length": 40960},
        }
        with patch("developer_tools.urllib.request.urlopen", return_value=self.response(payload)) as opened:
            result = fetch_model_capabilities("http://localhost:11434/", "qwen3:8b")
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:11434/api/show")
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), {"model": "qwen3:8b"})
        self.assertEqual(opened.call_args.kwargs["timeout"], 20)
        self.assertEqual(result, {
            "name": "qwen3:8b", "capabilities": ["completion", "tools", "thinking"],
            "context_length": 40960, "family": "qwen3", "parameter_size": "8.2B", "quantization": "Q4_K_M",
        })

    def test_missing_optional_metadata_is_unknown_without_invented_capabilities(self):
        with patch("developer_tools.urllib.request.urlopen", return_value=self.response({})):
            result = fetch_model_capabilities("http://localhost:11434", "old:7b")
        self.assertEqual(result, {"name": "old:7b", "capabilities": [], "context_length": None,
                                  "family": None, "parameter_size": None, "quantization": None})

    def test_fallback_context_ignores_invalid_values_and_preserves_unknown_fields(self):
        payload = {"capabilities": ["future-feature", None, 2],
                   "details": "invalid", "model_info": {"general.architecture": "llama",
                   "llama.context_length": True, "mistral.context_length": 32768}}
        with patch("developer_tools.urllib.request.urlopen", return_value=self.response(payload)):
            result = fetch_model_capabilities("http://localhost:11434", "model")
        self.assertEqual(result["context_length"], 32768)
        self.assertEqual(result["family"], "llama")
        self.assertEqual(result["capabilities"], ["future-feature"])

    def test_reports_network_http_api_and_unreadable_response_errors(self):
        failures = [urllib.error.URLError("refused"), TimeoutError("timeout"),
                    urllib.error.HTTPError("http://localhost", 404, "Not found", {}, None)]
        for failure in failures:
            with self.subTest(failure=failure), patch("developer_tools.urllib.request.urlopen", side_effect=failure):
                with self.assertRaises(RuntimeError):
                    fetch_model_capabilities("http://localhost:11434", "missing")
        for payload in (b"not json", b"[]", b"\xff", b'{"error":"model not found"}'):
            with self.subTest(payload=payload), patch("developer_tools.urllib.request.urlopen", return_value=io.BytesIO(payload)):
                with self.assertRaises(RuntimeError):
                    fetch_model_capabilities("http://localhost:11434", "missing")

    def test_limits_metadata_size_and_rejects_invalid_inputs_before_network(self):
        with patch.object(developer_tools, "_MAX_SHOW_BYTES", 10), patch(
                "developer_tools.urllib.request.urlopen", return_value=io.BytesIO(b" " * 11)):
            with self.assertRaisesRegex(RuntimeError, "too much"):
                fetch_model_capabilities("http://localhost:11434", "model")
        with patch("developer_tools.urllib.request.urlopen") as opened:
            for host, model in (("file:///tmp/api", "model"), ("localhost:11434", "model"),
                                ("http://localhost?query=x", "model"), ("http://localhost", "")):
                with self.subTest(host=host, model=model), self.assertRaises(ValueError):
                    fetch_model_capabilities(host, model)
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
