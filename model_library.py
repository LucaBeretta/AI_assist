"""Live Ollama model discovery and streamed downloads, using the standard library."""

import contextlib
from html.parser import HTMLParser
import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request

import network_gate


POPULAR_MODELS_URL = "https://ollama.com/library?sort=popular"
_MAX_PAGE_BYTES = 4 * 1024 * 1024
_MAX_PROGRESS_BYTES = 1024 * 1024
_SIZE_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)(?!\w)", re.I)
_PARAMETERS_RE = re.compile(r"(?:\d+(?:\.\d+)?(?:x\d+(?:\.\d+)?)?[bm]|e\d+b)", re.I)
_CAPABILITIES = {"tools", "vision", "thinking", "embedding", "cloud", "completion", "rerank"}


class DownloadCancelled(RuntimeError):
    """A download was cancelled before Ollama reported completion."""


class _Node:
    def __init__(self, tag="", attrs=()):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []

    def nodes(self, tag=None):
        pending = [iter(self.children)]
        while pending:
            child = next(pending[-1], None)
            if child is None:
                pending.pop()
            elif isinstance(child, _Node):
                if tag is None or child.tag == tag:
                    yield child
                pending.append(iter(child.children))

    def text(self):
        # Flatten once, avoiding repeated copies and whitespace normalization
        # at every ancestor of a text node on deeply nested pages.
        parts = []
        pending = [iter(self.children)]
        while pending:
            child = next(pending[-1], None)
            if child is None:
                pending.pop()
            elif isinstance(child, _Node):
                pending.append(iter(child.children))
            else:
                parts.append(child)
        return " ".join(" ".join(parts).split())

    def has_classes(self, *classes):
        return set(classes).issubset(self.attrs.get("class", "").split())


class _PageParser(HTMLParser):
    _VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input",
                  "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self._VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _read_page(url):
    request = urllib.request.Request(url, headers={"User-Agent": "AI-Assist-Desktop/1.0"})
    try:
        # Inside a caller's task (several pages for one recommendation) one approval covers every page.
        page_task = contextlib.nullcontext() if network_gate.in_task() else network_gate.task(
            "Read Ollama's online model list", sends="A request for one page of ollama.com. No chats or files are sent.")
        with page_task, urllib.request.urlopen(request, timeout=25) as response:
            data = response.read(_MAX_PAGE_BYTES + 1)
        if len(data) > _MAX_PAGE_BYTES:
            raise RuntimeError("Ollama's model page is too large to read.")
        page = _PageParser()
        page.feed(data.decode("utf-8"))
        return page.root
    except network_gate.InternetBlocked as error:
        raise RuntimeError(str(error)) from error
    except urllib.error.HTTPError as error:
        error.close()
        raise RuntimeError(f"Ollama's model library returned HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        raise RuntimeError("Could not reach Ollama's model library. Check your internet connection and retry.") from error
    except UnicodeDecodeError as error:
        raise RuntimeError("Ollama's model library returned an unreadable page.") from error


def fetch_popular_models(limit=30):
    """Return local chat model families in Ollama's live popularity order.

    Popularity is Ollama's ordering and pull counts, not worldwide model usage.
    Parameter-size badges (such as 8b) are not disk or memory requirements.
    """
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("The model limit must be a positive integer.")
    page = _read_page(POPULAR_MODELS_URL)
    models = []
    seen = set()
    for row in page.nodes("li"):
        link = next((node for node in row.nodes("a")
                     if re.fullmatch(r"/library/[A-Za-z0-9_.-]+", node.attrs.get("href", ""))), None)
        if link is None:
            continue
        name = link.attrs["href"].rsplit("/", 1)[-1]
        if name in seen:
            continue
        pills = [span.text().lower() for span in link.nodes("span") if span.has_classes("rounded-md")]
        capabilities = [pill for pill in pills if pill in _CAPABILITIES]
        sizes = [pill for pill in pills if _PARAMETERS_RE.fullmatch(pill)]
        if "embedding" in capabilities or "rerank" in capabilities:
            continue
        if "cloud" in capabilities and not sizes:
            continue
        description = next((node.text() for node in link.nodes("p")), "")
        pulls_match = re.search(r"([\d,.]+[KMB]?)\s+Pulls\b", link.text(), re.I)
        models.append({
            "name": name,
            "description": description,
            "pulls": pulls_match.group(1) if pulls_match else "Not available",
            "sizes": list(dict.fromkeys(sizes)),
            "capabilities": list(dict.fromkeys(capabilities)),
        })
        seen.add(name)
        if len(models) >= limit:
            break
    if not models:
        raise RuntimeError("Could not read Ollama's popular model list. The website may have changed; retry later.")
    return models


HUGGING_FACE_API = "https://huggingface.co/api/models"
_HF_REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,95}")
# The quantization Ollama's hf.co/<repo>:<quant> tag selects, from a single-file GGUF name.
_HF_QUANT_RE = re.compile(r"[-._](i?q\d(?:_[a-z0-9]+)*|f16|bf16)\.gguf$", re.I)
_HF_SPLIT_RE = re.compile(r"-\d{5}-of-\d{5}\.gguf$", re.I)
_HF_PARAMETERS_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)[Bb](?![A-Za-z0-9])")


def _read_hugging_face(url):
    request = urllib.request.Request(url, headers={"User-Agent": "AI-Assist-Desktop/1.0", "Accept": "application/json"})
    try:
        # Inside a caller's task (the model suggestions) its approval covers every request.
        page_task = contextlib.nullcontext() if network_gate.in_task() else network_gate.task(
            "Read Hugging Face's model list", sends="A request for model information on huggingface.co. "
            "No chats or files are sent.", hosts=("huggingface.co",))
        with page_task, urllib.request.urlopen(request, timeout=25) as response:
            data = response.read(_MAX_PAGE_BYTES + 1)
        if len(data) > _MAX_PAGE_BYTES:
            raise RuntimeError("Hugging Face returned more data than expected.")
        return json.loads(data.decode("utf-8"))
    except network_gate.InternetBlocked as error:
        raise RuntimeError(str(error)) from error
    except urllib.error.HTTPError as error:
        error.close()
        raise RuntimeError(f"Hugging Face returned HTTP {error.code}.") from error
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        raise RuntimeError("Could not reach Hugging Face. Check your internet connection and retry.") from error
    except (UnicodeDecodeError, ValueError) as error:
        raise RuntimeError("Hugging Face returned an unreadable answer.") from error


def _gguf_variants(repo, detail):
    """Single-file GGUF quantizations Ollama can pull as hf.co/<repo>:<quant>, smallest first."""
    files = detail.get("siblings") if isinstance(detail, dict) else None
    variants = {}
    for item in files if isinstance(files, list) else []:
        name = item.get("rfilename") if isinstance(item, dict) else None
        size = item.get("size") if isinstance(item, dict) else None
        # Folders, split files and vision projectors are not what a quantization tag selects.
        if (not isinstance(name, str) or "/" in name or "mmproj" in name.lower() or _HF_SPLIT_RE.search(name)
                or type(size) is not int or size <= 0):
            continue
        match = _HF_QUANT_RE.search(name)
        if match is None:
            continue
        quant = match.group(1).upper()
        if quant in variants:
            # Two files claim one quantization; the tag would be ambiguous.
            variants[quant] = None
            continue
        variants[quant] = {"name": f"hf.co/{repo}:{quant}", "quant": quant, "size_bytes": size}
    return sorted((variant for variant in variants.values() if variant), key=lambda variant: variant["size_bytes"])


_HF_TAG_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
# The same request Ollama makes before it pulls an hf.co tag.
_HF_MANIFEST_HEADERS = {"Accept": "application/vnd.docker.distribution.manifest.v2+json", "User-Agent": "AI-Assist-Desktop/1.0"}


def hugging_face_tag_available(tag):
    """Ask Hugging Face's registry, as Ollama does, whether ``hf.co/<repo>:<quant>`` can be pulled.

    True or False when the registry says so; None when it could not be asked.
    A file name can suggest a quantization that the registry does not offer.
    """
    repo, _, quant = tag.removeprefix("hf.co/").rpartition(":") if isinstance(tag, str) else ("", "", "")
    if not _HF_REPO_RE.fullmatch(repo) or not _HF_TAG_RE.fullmatch(quant):
        return False
    request = urllib.request.Request(f"https://huggingface.co/v2/{urllib.parse.quote(repo, safe='/')}/manifests/{quant}",
                                     headers=_HF_MANIFEST_HEADERS)
    try:
        page_task = contextlib.nullcontext() if network_gate.in_task() else network_gate.task(
            "Check a Hugging Face model", sends="The model and version names. No chats or files are sent.",
            hosts=("huggingface.co",))
        with page_task, urllib.request.urlopen(request, timeout=15) as response:
            response.read(_MAX_PAGE_BYTES)
            return response.status == 200
    except network_gate.InternetBlocked as error:
        raise RuntimeError(str(error)) from error
    except urllib.error.HTTPError as error:
        error.close()
        return False if error.code in (400, 404) else None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        return None


def fetch_hugging_face_models(limit=6):
    """Hugging Face's most downloaded GGUF chat models, each with its downloadable quantizations.

    Download counts are Hugging Face's own; they show popularity, not quality.
    Each model needs one more request for its file sizes, so ``limit`` bounds the work.
    """
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("The model limit must be a positive integer.")
    query = urllib.parse.urlencode({"filter": "gguf", "pipeline_tag": "text-generation",
                                    "sort": "downloads", "direction": "-1", "limit": str(limit * 4)})
    rows = _read_hugging_face(f"{HUGGING_FACE_API}?{query}")
    if not isinstance(rows, list):
        raise RuntimeError("Hugging Face returned an invalid model list.")
    models, checked = [], 0
    for row in rows:
        repo = row.get("id") if isinstance(row, dict) else None
        if not isinstance(repo, str) or not _HF_REPO_RE.fullmatch(repo):
            continue
        if checked >= limit * 2:
            break
        checked += 1
        try:
            detail = _read_hugging_face(f"{HUGGING_FACE_API}/{urllib.parse.quote(repo, safe='/')}?blobs=true")
        except RuntimeError:
            continue
        variants = _gguf_variants(repo, detail)
        if not variants:
            continue
        parameters = _HF_PARAMETERS_RE.search(repo.split("/", 1)[1])
        downloads = row.get("downloads")
        models.append({"repo": repo, "variants": variants,
                       "parameters": float(parameters.group(1)) if parameters else None,
                       "downloads": downloads if type(downloads) is int else None})
        if len(models) >= limit:
            break
    if not models:
        raise RuntimeError("No downloadable Hugging Face models were found. Try again later.")
    return models


def fetch_model_variants(name):
    """Return exact local pull tags with advertised download sizes, smallest first."""
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("Choose a model family from the model library.")
    page = _read_page(f"https://ollama.com/library/{urllib.parse.quote(name)}/tags")
    variants = []
    seen = set()
    for row in page.nodes("div"):
        if not row.has_classes("group", "px-4", "py-3"):
            continue
        prefix = f"/library/{name}:"
        link = next((node for node in row.nodes("a")
                     if node.attrs.get("href", "").startswith(prefix)), None)
        if link is None:
            continue
        tag = urllib.parse.unquote(link.attrs["href"].removeprefix("/library/"))
        if "cloud" in tag.lower() or tag in seen:
            continue
        size_match = _SIZE_RE.search(row.text())
        if size_match is None:
            continue
        amount, unit = size_match.groups()
        unit = unit.upper()
        size_bytes = int(float(amount) * {"KB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4}[unit])
        if size_bytes <= 0:
            continue
        variants.append({"name": tag, "size": f"{amount} {unit}", "size_bytes": size_bytes})
        seen.add(tag)
    if not variants:
        raise RuntimeError("No downloadable local variants were found for this model. Try another model or refresh the library.")
    # Prefer the ordinary size aliases that Ollama itself advertises. This
    # avoids dozens of duplicate quantizations and hardware-specific formats.
    aliases = [variant for variant in variants if (
        variant["name"].endswith(":latest")
        or _PARAMETERS_RE.fullmatch(variant["name"].split(":", 1)[1])
    )]
    if aliases:
        variants = aliases
    else:
        readable = [variant for variant in variants if not re.search(
            r"[-_](?:i?q\d|fp?(?:16|32)|bf16)", variant["name"], re.I)]
        if readable:
            variants = readable
    return sorted(variants, key=lambda variant: (variant["size_bytes"], variant["name"].endswith(":latest"), variant["name"]))


def _check_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise DownloadCancelled("Download cancelled. Ollama can reuse downloaded data when you try again.")


def pull_model(host, name, on_progress, cancel_event=None):
    """Pull a model through Ollama's NDJSON API; report every progress object.

    Only an explicit ``success`` status completes the call. Socket reads are
    bounded to 45 seconds; cancellation is checked between received lines.
    """
    _check_cancelled(cancel_event)
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]*", name):
        raise ValueError("Choose a valid model tag before downloading.")
    parsed_host = urllib.parse.urlsplit(host)
    if parsed_host.scheme not in ("http", "https") or not parsed_host.hostname:
        raise ValueError("The Ollama server address must be an HTTP or HTTPS URL.")
    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/pull",
        data=json.dumps({"model": name, "stream": True}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        source = ("Ollama downloads this model from Hugging Face. " if network_gate.hugging_face_model(name)
                  else "Ollama asks its online registry for this model and downloads it. ")
        with network_gate.task(f"Download {name} through Ollama", sends=source + "No chats or files are sent.",
                               minutes=24 * 60), \
                urllib.request.urlopen(request, timeout=45) as response:
            while True:
                _check_cancelled(cancel_event)
                line = response.readline(_MAX_PROGRESS_BYTES + 1)
                _check_cancelled(cancel_event)
                if not line:
                    raise RuntimeError("The download connection closed before Ollama confirmed success. Try again to resume.")
                if len(line) > _MAX_PROGRESS_BYTES:
                    raise RuntimeError("Ollama returned an oversized download progress message.")
                if not line.strip():
                    continue
                try:
                    progress = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise RuntimeError("Ollama returned invalid download progress. Try again to resume.") from error
                if not isinstance(progress, dict):
                    raise RuntimeError("Ollama returned invalid download progress.")
                if progress.get("error"):
                    raise RuntimeError(f"Ollama could not download this model: {progress['error']}")
                if not isinstance(progress.get("status"), str):
                    raise RuntimeError("Ollama returned download progress without a status.")
                for field in ("total", "completed"):
                    if field in progress and (type(progress[field]) is not int or progress[field] < 0):
                        raise RuntimeError("Ollama returned invalid download byte counts.")
                on_progress(progress)
                if progress["status"] == "success":
                    return
    except DownloadCancelled:
        raise
    except network_gate.InternetBlocked as error:
        raise RuntimeError(str(error)) from error
    except urllib.error.HTTPError as error:
        error.close()
        _check_cancelled(cancel_event)
        raise RuntimeError(f"Ollama returned HTTP {error.code} while downloading. Check that the model is available.") from error
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
        _check_cancelled(cancel_event)
        raise RuntimeError("The Ollama download connection failed. Check Ollama and your internet connection, then retry to resume.") from error
