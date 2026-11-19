"""Read-only project context and Ollama metadata helpers for the developer UI.

All filesystem access is explicit and confined to the selected project. These
helpers never execute generated code or write project files.
"""

import codecs
import http.client
import itertools
import json
import os
from pathlib import Path, PureWindowsPath
import re
import stat
import urllib.error
import urllib.parse
import urllib.request


_SKIP_DIRECTORIES = frozenset({
    ".git", ".hg", ".svn", "node_modules", "bower_components", "vendor",
    "venv", ".venv", "env", ".env", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".cache", ".next",
    ".nuxt", ".turbo", ".idea", ".vscode", "build", "dist", "target",
    "coverage", "htmlcov", ".eggs", ".ssh", ".aws", ".azure", ".secrets",
    ".aiassist-checkpoints",
    "secrets", "credentials",
})
_BINARY_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tif",
    ".tiff", ".pdf", ".zip", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".tar", ".exe", ".dll", ".so", ".dylib", ".obj", ".o", ".a",
    ".lib", ".pyc", ".pyo", ".class", ".jar", ".wasm", ".woff",
    ".woff2", ".ttf", ".otf", ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".sqlite", ".sqlite3", ".db", ".bin", ".gguf", ".safetensors",
})
_SECRET_SUFFIXES = frozenset({".pem", ".key", ".p8", ".p12", ".pfx", ".jks", ".keystore"})
_SECRET_NAMES = frozenset({
    ".npmrc", ".pypirc", ".netrc", "_netrc", "id_rsa", "id_dsa",
    "id_ecdsa", "id_ed25519", "authorized_keys", "known_hosts",
})
_MAX_SCAN_ENTRIES = 20000
_MAX_SCAN_DIRECTORIES = 1500
_MAX_SCAN_DEPTH = 20
_PROBE_BYTES = 8192
_MAX_PROBE_BYTES = 4 * 1024 * 1024
_MAX_FILE_BYTES = 64000
_MAX_SHOW_BYTES = 2 * 1024 * 1024
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN (?:[A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?)-----")
_BINARY_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0e-\x1f]")
_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)[\r\n]*$")
_FILENAME_RE = re.compile(
    r"(?:^|\s)(?:filename|file|title)\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s]+))",
    re.I,
)


def _positive_integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer.")
    return value


def _project_root(root):
    try:
        project = Path(root).expanduser().resolve(strict=True)
        if not project.is_dir():
            raise ValueError("Choose a project folder, rather than a file.")
        return project
    except (TypeError, OSError, RuntimeError) as error:
        raise ValueError("The project folder is missing or cannot be read.") from error


def _is_secret_name(name):
    lowered = name.lower()
    return (
        (lowered == ".env" or lowered.startswith(".env.")) and lowered != ".env.example"
        or lowered in _SECRET_NAMES
        or lowered.startswith(("credentials", "credential.", "secrets.", "secret."))
        or re.search(r"(?:^|[_.-])private[_.-]?key(?:[_.-]|$)", lowered) is not None
        or Path(lowered).suffix in _SECRET_SUFFIXES
    )


def _check_components(parts):
    for part in parts:
        if _is_secret_name(part):
            raise ValueError("Secret and private-key files are excluded from project context.")
    if any(part.lower() in _SKIP_DIRECTORIES for part in parts[:-1]):
        raise ValueError("Generated, dependency, and private folders are excluded from project context.")


def _project_file(project, relative_path):
    try:
        supplied = os.fspath(relative_path)
    except TypeError as error:
        raise ValueError("Choose a relative file path inside the project.") from error
    if not isinstance(supplied, str) or not supplied or "\x00" in supplied:
        raise ValueError("Choose a relative file path inside the project.")
    windows = PureWindowsPath(supplied)
    relative = Path(supplied.replace("\\", "/"))
    if relative.is_absolute() or windows.drive or windows.root or ".." in relative.parts:
        raise ValueError("File paths must stay inside the selected project.")
    _check_components(relative.parts)
    try:
        target = (project / relative).resolve(strict=True)
        resolved_relative = target.relative_to(project)
        _check_components(resolved_relative.parts)
    except ValueError as error:
        if "excluded" in str(error):
            raise
        raise ValueError("File paths must stay inside the selected project.") from error
    except (OSError, RuntimeError) as error:
        raise ValueError(f"Cannot read project file '{relative.as_posix()}': the file is missing or inaccessible.") from error
    return target, relative.as_posix()


def _decode_text(data, partial=False):
    # Strict UTF-8 keeps unreadable bytes out of prompts. The incremental
    # decoder permits a multibyte character to straddle a listing probe.
    try:
        decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
        text = decoder.decode(data, final=not partial)
    except UnicodeDecodeError as error:
        raise ValueError("This file is not readable UTF-8 text.") from error
    if _BINARY_CONTROL_RE.search(text):
        raise ValueError("Binary files cannot be included in project context.")
    if _PRIVATE_KEY_RE.search(text):
        raise ValueError("Private-key contents are excluded from project context.")
    return text


def list_project_files(root, limit=250):
    """List readable text files in deterministic path order with bounded work.

    Scanning stops after 20,000 entries, 1,500 directories, depth 20, or 4 MiB
    of text probes. Directory links are not followed, preventing cycles. A
    file's full UTF-8 validity and size are checked when it is actually read.
    """
    _positive_integer(limit, "The file limit")
    project = _project_root(root)
    pending = [(project, 0)]
    visited = set()
    files = []
    scanned = 0
    probed = 0
    directory_count = 0
    while pending and scanned < _MAX_SCAN_ENTRIES and directory_count < _MAX_SCAN_DIRECTORIES:
        directory, depth = pending.pop()
        directory_count += 1
        try:
            resolved_directory = directory.resolve(strict=True)
            resolved_directory.relative_to(project)
            if resolved_directory in visited:
                continue
            visited.add(resolved_directory)
            with os.scandir(directory) as entries:
                children = list(itertools.islice(entries, _MAX_SCAN_ENTRIES - scanned))
            scanned += len(children)
        except (OSError, ValueError, RuntimeError):
            continue
        child_directories = []
        for entry in sorted(children, key=lambda item: (item.name.casefold(), item.name)):
            name = entry.name
            if _is_secret_name(name):
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    if (depth < _MAX_SCAN_DEPTH and name.lower() not in _SKIP_DIRECTORIES
                            and not entry.is_symlink()):
                        child_directories.append((Path(entry.path), depth + 1))
                    continue
                if Path(name).suffix.lower() in _BINARY_SUFFIXES or not entry.is_file():
                    continue
                relative = Path(entry.path).relative_to(project).as_posix()
                target, relative = _project_file(project, relative)
                metadata = target.stat()
                if not stat.S_ISREG(metadata.st_mode):
                    continue
                probe_size = min(metadata.st_size, _PROBE_BYTES)
                if probed + probe_size > _MAX_PROBE_BYTES:
                    return sorted(files, key=lambda item: (item["path"].casefold(), item["path"]))[:limit]
                with target.open("rb") as source:
                    sample = source.read(probe_size)
                probed += len(sample)
                _decode_text(sample, partial=metadata.st_size > len(sample))
                files.append({"path": relative, "size_bytes": metadata.st_size})
                if len(files) >= limit:
                    return sorted(files, key=lambda item: (item["path"].casefold(), item["path"]))
            except (OSError, ValueError, RuntimeError):
                continue
        pending.extend(reversed(child_directories))
    return sorted(files, key=lambda item: (item["path"].casefold(), item["path"]))[:limit]


def read_project_file(root, relative_path, max_bytes=64000, start_line=None, end_line=None):
    """Read one project file as UTF-8, rejecting secrets, escapes, and binaries."""
    _positive_integer(max_bytes, "The file size limit")
    project = _project_root(root)
    target, label = _project_file(project, relative_path)
    if target.suffix.lower() in _BINARY_SUFFIXES:
        raise ValueError(f"Cannot read '{label}': binary files are excluded.")
    try:
        metadata = target.stat()
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"Cannot read '{label}': choose a regular text file.")
        if start_line is not None or end_line is not None:
            if (type(start_line) is not int or type(end_line) is not int
                    or not 1 <= start_line <= end_line):
                raise ValueError("Choose positive, inclusive start_line and end_line values.")
            chunks, selected, scanned = [], 0, 0
            with target.open("rb") as source:
                for number in range(1, end_line + 1):
                    data = source.readline(max_bytes + 1)
                    if not data:
                        break
                    scanned += len(data)
                    if len(data) > max_bytes or scanned > 64 * 1024 * 1024:
                        raise ValueError("The line or scan exceeds the bounded range reading limit.")
                    text = _decode_text(data)
                    if number >= start_line:
                        selected += len(data)
                        if selected > max_bytes:
                            raise ValueError(f"The selected range exceeds the {max_bytes:,}-byte limit; request fewer lines.")
                        chunks.append(text)
            return "".join(chunks)
        if metadata.st_size > max_bytes:
            raise ValueError(f"Cannot read '{label}': file exceeds the {max_bytes:,}-byte limit.")
        with target.open("rb") as source:
            data = source.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError(f"Cannot read '{label}': file exceeds the {max_bytes:,}-byte limit.")
    except OSError as error:
        raise ValueError(f"Cannot read '{label}': the file is inaccessible.") from error
    return _decode_text(data)


def build_project_context(root, paths, max_total_chars=24000, max_file_bytes=_MAX_FILE_BYTES):
    """Build labeled, whole-file context and report every skipped selection.

    The returned ``text`` (including labels) never exceeds ``max_total_chars``.
    ``included`` contains relative paths; ``skipped`` contains path/reason pairs.
    Files are included once, in the supplied selection order, up to 250 paths.
    Each file must fit ``max_file_bytes``, independently of the total context budget.
    """
    _positive_integer(max_total_chars, "The project context limit")
    _positive_integer(max_file_bytes, "The file size limit")
    project = _project_root(root)
    if isinstance(paths, (str, bytes)):
        raise ValueError("Choose a collection of relative project file paths.")
    result = {"text": "", "included": [], "skipped": []}
    sections = []
    total = 0
    seen = set()
    for path in itertools.islice(paths, 250):
        label = str(path).replace("\\", "/")
        try:
            _, label = _project_file(project, path)
            if label in seen:
                continue
            seen.add(label)
            contents = read_project_file(project, path, max_bytes=max_file_bytes)
            section = f"--- Project file: {label} ---\n{contents}"
            if not section.endswith("\n"):
                section += "\n"
            separator = "\n" if sections else ""
            if total + len(separator) + len(section) > max_total_chars:
                raise ValueError(f"Adding this file would exceed the {max_total_chars:,}-character context limit.")
            sections.append(separator + section)
            total += len(separator) + len(section)
            result["included"].append(label)
        except (TypeError, ValueError, OSError, RuntimeError) as error:
            result["skipped"].append({"path": label, "reason": str(error)})
    result["text"] = "".join(sections)
    return result


def _fence_label(info):
    info = info.strip()
    filename_match = _FILENAME_RE.search(info)
    filename = next((value for value in filename_match.groups() if value), None) if filename_match else None
    tokens = info.split()
    language = tokens[0].lower() if tokens else ""
    if language.startswith(("filename=", "file=", "title=")):
        language = ""
    elif language.startswith("{."):
        language = language[2:].rstrip("}")
    elif filename is None and ("/" in language or "\\" in language or "." in language):
        filename = tokens[0]
        language = ""
    if filename is None and len(tokens) > 1:
        candidate = tokens[1].strip("`\"'")
        if not candidate.startswith("{") and ("/" in candidate or "\\" in candidate or "." in candidate):
            filename = candidate
    # Filenames are display hints. The UI must always let the user choose the
    # export destination and must never write directly to this untrusted label.
    return language, filename


def extract_code_blocks(text):
    """Extract Markdown backtick/tilde fences without changing their code.

    Incomplete final fences are returned with ``complete=False`` so callers can
    display streaming output without treating it as a finished export.
    """
    if not isinstance(text, str):
        raise ValueError("The response must be text.")
    blocks = []
    opening = None
    contents = []
    language = ""
    filename = None
    for line in text.splitlines(keepends=True):
        match = _FENCE_RE.match(line)
        if opening is None:
            if match is None:
                continue
            fence, info = match.groups()
            if fence.startswith("`") and "`" in info:
                continue
            opening = fence
            language, filename = _fence_label(info)
            contents = []
        elif (match is not None and match.group(1)[0] == opening[0]
              and len(match.group(1)) >= len(opening) and not match.group(2).strip()):
            blocks.append({"language": language, "code": "".join(contents),
                           "filename": filename, "complete": True})
            opening = None
        else:
            contents.append(line)
    if opening is not None:
        blocks.append({"language": language, "code": "".join(contents),
                       "filename": filename, "complete": False})
    return blocks


def fetch_model_capabilities(host, model):
    """Fetch native model capabilities from Ollama's bounded ``POST /api/show``.

    ``context_length`` is the model's advertised maximum; active context size
    and its memory cost are configured separately by Ollama/chat options.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("Choose an installed Ollama model.")
    if not isinstance(host, str):
        raise ValueError("Ollama's host must be an HTTP or HTTPS URL.")
    parsed = urllib.parse.urlsplit(host.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Ollama's host must be an HTTP or HTTPS URL.")
    request = urllib.request.Request(
        host.strip().rstrip("/") + "/api/show",
        data=json.dumps({"model": model}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(_MAX_SHOW_BYTES + 1)
        if len(data) > _MAX_SHOW_BYTES:
            raise RuntimeError("Ollama returned too much model metadata.")
        metadata = json.loads(data.decode("utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError("Expected model metadata.")
        if metadata.get("error"):
            raise RuntimeError(f"Ollama could not inspect the model: {str(metadata['error'])[:250]}")
    except urllib.error.HTTPError as error:
        error.close()
        raise RuntimeError(f"Ollama could not inspect '{model}' (HTTP {error.code}). Check that the model is installed.") from error
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        raise RuntimeError("Could not reach Ollama to inspect this model. Check that Ollama is running and retry.") from error
    except (UnicodeDecodeError, ValueError) as error:
        raise RuntimeError("Ollama returned unreadable model metadata.") from error
    details = metadata.get("details")
    details = details if isinstance(details, dict) else {}
    model_info = metadata.get("model_info")
    model_info = model_info if isinstance(model_info, dict) else {}
    capabilities = metadata.get("capabilities")
    capabilities = list(dict.fromkeys(item for item in capabilities if isinstance(item, str))) if isinstance(capabilities, list) else []
    architecture = model_info.get("general.architecture")
    context = model_info.get(f"{architecture}.context_length") if isinstance(architecture, str) else None
    valid_context = lambda value: isinstance(value, int) and not isinstance(value, bool) and value > 0
    if not valid_context(context):
        context = next((value for key, value in sorted(model_info.items())
                        if key.endswith(".context_length") and valid_context(value)), None)
    value_or_none = lambda value: value if isinstance(value, str) and value else None
    return {
        "name": model,
        "capabilities": capabilities,
        "context_length": context,
        "family": value_or_none(details.get("family")) or value_or_none(architecture),
        "parameter_size": value_or_none(details.get("parameter_size")),
        "quantization": value_or_none(details.get("quantization_level")),
    }
