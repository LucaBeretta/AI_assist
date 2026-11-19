"""Extract bounded PDF text in a short-lived, windowless local process.

Raw bytes travel through pipes, so incognito attachments never need temporary
files. Parsing has a deadline and pypdf's per-context resource limits.
"""

import io
import json
import logging
from pathlib import Path
import subprocess
import sys


_TIMEOUT = 15
_MAX_PAGES = 100


def extract_pdf(data, limit):
    if not data.startswith(b"%PDF-"):
        raise ValueError("This file does not have a valid PDF header.")
    executable = Path(sys.executable)
    if executable.name.lower() == "pythonw.exe":
        executable = executable.with_name("python.exe")
    try:
        result = subprocess.run([str(executable), "-I", str(Path(__file__).resolve()), str(limit)],
            input=data, capture_output=True, timeout=_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as error:
        raise ValueError("PDF text extraction took too long; use a smaller or simpler document.") from error
    try:
        response = json.loads(result.stdout.decode("utf-8"))
    except (ValueError, UnicodeError) as error:
        raise ValueError("The PDF reader could not process this document.") from error
    if result.returncode != 0 or not isinstance(response, dict) or not response.get("ok"):
        raise ValueError(response.get("error", "The PDF reader failed.") if isinstance(response, dict) else "The PDF reader failed.")
    text = response.get("text")
    if not isinstance(text, str) or len(text.encode("utf-8")) > limit:
        raise ValueError("The PDF text exceeds the agent file limit.")
    return text


def _extract(data, limit):
    try:
        from pypdf import PdfReader, apply_configuration
        from pypdf.errors import PyPdfError
    except ImportError as error:
        raise ValueError("PDF support needs pypdf. Run: python -m pip install -r requirements.txt") from error
    stream_limit = min(4_000_000, max(64000, limit * 8))
    pieces, used = [], 0

    def append(text):
        nonlocal used
        used += len(text.encode("utf-8"))
        if used > limit:
            raise ValueError("The PDF text exceeds the agent file limit; split the document or choose a larger limit.")
        pieces.append(text)

    try:
        with apply_configuration(maximum_declared_stream_length=stream_limit,
                zlib_maximum_output_length=stream_limit, lzw_maximum_output_length=stream_limit,
                run_length_maximum_output_length=stream_limit, brotli_maximum_output_length=stream_limit,
                array_based_stream_maximum_output_length=stream_limit,
                stream_decoding_work_maximum_length=stream_limit * 2,
                stream_parsing_work_maximum_count=100000, array_maximum_element_count=10000,
                dictionary_maximum_element_count=10000, page_tree_maximum_entries=1000,
                xform_maximum_invocations_per_extraction=100, image_maximum_buffer_size=stream_limit,
                jbig2dec_binary=None):
            reader = PdfReader(io.BytesIO(data), strict=True, root_object_recovery_limit=1000,
                               xref_stream_maximum_entries=10000)
            if reader.is_encrypted:
                raise ValueError("Password-protected PDFs are not supported; attach an unencrypted copy.")
            if len(reader.pages) > _MAX_PAGES:
                raise ValueError(f"PDF attachments support at most {_MAX_PAGES} pages; split the document.")
            readable = False
            for index, page in enumerate(reader.pages, start=1):
                append(f"[Page {index}]\n")
                def visitor(text, *args):
                    nonlocal readable
                    readable = readable or bool(text.strip())
                    append(text)
                page.extract_text(visitor_text=visitor)
                append("\n")
            if not readable:
                raise ValueError("This PDF has no extractable text. Scanned pages need OCR, which is not available.")
    except PyPdfError as error:
        raise ValueError("The PDF is damaged or exceeds the parser's resource limits; use a simpler document.") from error
    return "".join(pieces).strip()


def _main():
    logging.disable(logging.CRITICAL)
    try:
        limit = int(sys.argv[1])
        if not 1000 <= limit <= 4000000:
            raise ValueError("Invalid PDF text limit.")
        data = sys.stdin.buffer.read(limit + 1)
        if not data or len(data) > limit:
            raise ValueError("The PDF exceeds the agent file limit.")
        response = {"ok": True, "text": _extract(data, limit)}
        code = 0
    except ValueError as error:
        response, code = {"ok": False, "error": str(error)}, 1
    except Exception:
        response, code = {"ok": False, "error": "The PDF is damaged or cannot be read."}, 1
    sys.stdout.buffer.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
    return code


if __name__ == "__main__":
    raise SystemExit(_main())
