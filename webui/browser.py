"""A reading browser inside AI Assist.

This process fetches every page, so the internet gate asks before each new
site, as it does for everything else the app does online. The window itself
never loads anything from the web: a page comes back as a tree of plain
elements from an allowlist, so scripts, styles, frames, cookies, trackers and
forms that post never reach it. Images come back as data, only from sites a
person approved. Pages and approvals live in memory only; nothing is saved.
"""

import base64
import collections
import contextlib
from html.parser import HTMLParser
import ipaddress
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import network_gate
from webui.api_utils import json_method


PURPOSE = "Open web pages in the AI Assist browser"
SENDS = "The address of each page you open, and your words when you search. No chats or files are sent."
IMAGE_PURPOSE = "Show images from other sites on a page open in the AI Assist browser"
IMAGE_SENDS = "The address of each image. No chats or files are sent."
# Words typed in the address bar search Wikipedia. General search engines answer a reader without
# scripts with a robot check, and the browser does not try to get around those.
SEARCH = "https://{language}.wikipedia.org/w/index.php"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
              "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")
PAGE_ACCEPT = "text/html,application/xhtml+xml,text/plain;q=0.9,image/*;q=0.8,*/*;q=0.5"
IMAGE_ACCEPT = "image/avif,image/webp,image/png,image/jpeg,image/gif,image/*;q=0.8"

SESSION_MINUTES = 60
PAGE_TIMEOUT = 20
IMAGE_TIMEOUT = 10
PAGE_BYTES = 5 * 1024 * 1024
IMAGE_BYTES = 1536 * 1024
IMAGES_BYTES = 6 * 1024 * 1024
IMAGES_PER_PAGE = 30
IMAGE_HOSTS = 12
INLINE_IMAGE_CHARS = 300_000
OPEN_PAGES = 16
MAX_NODES = 20_000
MAX_CHARS = 1_500_000
MAX_DEPTH = 64
MAX_REDIRECTS = 8
MAX_REFRESHES = 3
READING_MIN_CHARS = 300

# Kept as they are. Everything else is unwrapped (its text stays) or dropped with its content.
_KEEP = frozenset("""
a abbr address article aside b bdi blockquote br caption cite code dd del details dfn div dl dt em figcaption
figure footer h1 h2 h3 h4 h5 h6 header hr i img ins kbd li main mark nav ol p pre q s samp section small span strong
sub summary sup table tbody td tfoot th thead time tr u ul var
""".split())
_DROP = frozenset("""
applet audio button canvas datalist dialog embed frame frameset iframe map math noembed noframes object option
script select style svg template textarea title video
""".split())
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_BLOCKS = frozenset("""
address article aside blockquote details div dl fieldset figure footer form h1 h2 h3 h4 h5 h6 header hr main nav
ol p pre section table ul
""".split())
# A start tag of a block closes an open <p>, but not past one of these.
_P_SCOPE = frozenset("""
article aside blockquote body button caption dd details div dt figure footer form header li main nav section td th
""".split())
_NO_TEXT = frozenset("dl ol table tbody tfoot thead tr ul".split())
_TEXT_INPUTS = frozenset("email number search tel text url".split())
_TEXT_BLOCKS = _BLOCKS | frozenset("br caption dd dt figcaption li tr".split())
_PARAGRAPHS = _BLOCKS - frozenset("div hr".split())
_NOT_READING = frozenset("aside footer form nav".split())
_INLINE_IMAGE = re.compile(r"data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=\s]+")
_REFRESH = re.compile(r"\s*(\d+(?:\.\d+)?)\s*[;,]?\s*(?:url\s*=\s*)?['\"]?([^'\"]*)", re.I)
_META_CHARSET = re.compile(rb"<meta[^>]+charset\s*=\s*['\"]?\s*([A-Za-z0-9_.:-]+)", re.I)


class BrowserError(ValueError):
    """A page that cannot be shown, with a reason a person can read."""


def search_url(words, language="en"):
    return SEARCH.format(language="it" if language == "it" else "en") + "?" + urllib.parse.urlencode({"search": words})


def address_url(text, language="en"):
    """The http(s) address for what a person typed: an address, or words to search for."""
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        raise BrowserError("Type an address or words to search for.")
    if len(text) > 2000:
        raise BrowserError("This address is too long.")
    parts = urllib.parse.urlsplit(text)
    if parts.scheme.lower() in ("http", "https") and "://" in text:
        return checked_url(text)
    if "://" in text or parts.scheme.lower() in ("javascript", "data", "file", "about", "blob", "vbscript"):
        raise BrowserError("The browser opens web addresses (http and https) only.")
    host = re.split(r"[/?#]", text, maxsplit=1)[0]
    name = host.split("]", 1)[0] + "]" if host.startswith("[") else host.rsplit(":", 1)[0]
    if not re.search(r"\s", text) and _looks_like_host(name):
        scheme = "https" if network_gate.classify(name) == network_gate.INTERNET else "http"
        return checked_url(f"{scheme}://{text}")
    return search_url(text, language)


def _looks_like_host(name):
    """example.com, localhost, an IP address: an address. 3.5 or a single word: a search."""
    name = name.lower()
    if name == "localhost" or (name.startswith("[") and name.endswith("]")):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    labels = name.split(".")
    return (len(labels) > 1 and all(re.fullmatch(r"[a-z0-9-]{1,63}", label) for label in labels)
            and re.fullmatch(r"[a-z]{2,63}|xn--[a-z0-9-]{1,59}", labels[-1]) is not None)


def checked_url(url, from_internet=False):
    """Refuse what the browser never opens: other schemes, passwords in the address, and,
    from a page on the internet, addresses on this computer or the local network."""
    url = url.strip() if isinstance(url, str) else ""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError:
        raise BrowserError("This is not a valid web address.") from None
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise BrowserError("The browser opens web addresses (http and https) only.")
    if parts.username is not None or parts.password is not None:
        raise BrowserError("The browser does not open addresses that contain a user name or password.")
    if len(url) > 4000:
        raise BrowserError("This address is too long.")
    if from_internet and network_gate.classify(host) != network_gate.INTERNET:
        raise BrowserError("A web page cannot open an address on this computer or your local network. "
                           "Type the address yourself if you meant to open it.")
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query,
                                    parts.fragment))


def is_internet(url):
    return network_gate.classify(urllib.parse.urlsplit(url).hostname or "") == network_gate.INTERNET


def _without_fragment(url):
    return urllib.parse.urldefrag(url)[0]


def _number(value, low, high):
    try:
        number = int(str(value).strip().rstrip("px"))
    except ValueError:
        return None
    return number if low <= number <= high else None


def _hidden(attrs):
    if "hidden" in attrs or attrs.get("aria-hidden", "").strip().lower() == "true":
        return True
    style = re.sub(r"\s+", "", attrs.get("style", "").lower())
    return "display:none" in style or "visibility:hidden" in style


class _Page(HTMLParser):
    """Turns HTML into nested [tag, attributes, children] lists from the allowlist, and plain strings."""

    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url = self.base = url
        self.root = ["div", {}, []]
        self.stack = [("#root", self.root, "keep")]
        self.drop = self.forms = self.pre = self.nodes = self.chars = 0
        self.full = False
        self.base_set = False
        self.in_title = self.title_done = False
        self.title_parts = []
        self.heading = None
        self.refresh = None
        self.images = []

    # ---------------------------------------------------------------- tree

    def _parent(self):
        for tag, node, mode in reversed(self.stack):
            if mode == "keep":
                return node
        return self.root

    def _append(self, node):
        if self.nodes >= MAX_NODES:
            self.full = True
            return False
        self.nodes += 1
        self._parent()[2].append(node)
        return True

    def _push(self, tag, node, mode):
        self.stack.append((tag, node, mode))
        if mode == "drop":
            self.drop += 1
        elif tag == "form":
            self.forms += 1
        elif tag == "pre":
            self.pre += 1

    def _pop(self):
        tag, node, mode = self.stack.pop()
        if mode == "drop":
            self.drop -= 1
        elif tag == "form":
            self.forms -= 1
        elif tag == "pre":
            self.pre -= 1

    def _close(self, names, stop):
        for index in range(len(self.stack) - 1, 0, -1):
            tag = self.stack[index][0]
            if tag in names:
                while len(self.stack) > index:
                    self._pop()
                return
            if tag in stop:
                return

    def _close_implied(self, tag):
        if tag == "li":
            self._close(("li",), ("ul", "ol", "menu"))
        elif tag in ("dt", "dd"):
            self._close(("dt", "dd"), ("dl",))
        elif tag == "tr":
            self._close(("tr",), ("table", "thead", "tbody", "tfoot"))
        elif tag in ("td", "th"):
            self._close(("td", "th"), ("tr", "table"))
        elif tag in ("thead", "tbody", "tfoot"):
            self._close(("thead", "tbody", "tfoot"), ("table",))
        if tag in _BLOCKS or tag in ("li", "dt", "dd", "tr", "td", "th"):
            self._close(("p",), _P_SCOPE)

    # ---------------------------------------------------------------- addresses

    def _resolve(self, value, from_base=True):
        value = (value or "").strip()
        if not value or len(value) > 4000:
            return None
        try:
            url = urllib.parse.urljoin(self.base if from_base else self.url, value)
            url = checked_url(url)
        except (ValueError, BrowserError):
            return None
        return url

    def _link(self, value):
        value = (value or "").strip()
        if value.lower().startswith(("javascript:", "data:", "vbscript:", "mailto:", "tel:", "blob:")):
            return None
        return self._resolve(value)

    def _image(self, attrs):
        for key in ("data-src", "data-lazy-src", "data-original", "src"):
            value = attrs.get(key, "").strip()
            if value.startswith("data:"):
                if _INLINE_IMAGE.fullmatch(value) and len(value) <= INLINE_IMAGE_CHARS:
                    return value
                continue
            url = self._resolve(value)
            if url:
                return url
        for key in ("srcset", "data-srcset"):
            first = attrs.get(key, "").strip().split(",")[0].strip().split(" ")[0]
            url = self._resolve(first)
            if url:
                return url
        return None

    # ---------------------------------------------------------------- parser events

    def handle_starttag(self, tag, attrs):
        if tag == "title" and not self.drop and not self.title_done:
            self.in_title = True  # the page's own title; an SVG icon's <title> sits inside a dropped <svg>
        attrs = {key: value if value is not None else "" for key, value in attrs}
        if tag == "base":
            href = attrs.get("href")
            if href and not self.base_set:
                self.base_set = True
                self.base = self._resolve(href, from_base=False) or self.base
            return
        if tag == "meta":
            self._meta(attrs)
            return
        if self.full:
            return
        if self.drop:
            if tag not in _VOID and len(self.stack) < 4 * MAX_DEPTH:
                self._push(tag, None, "drop")
            return
        hidden = tag not in ("html", "head", "body") and _hidden(attrs)
        if tag == "input":
            if self.forms and not hidden:
                self._input(attrs)
            return
        if tag == "button" and self.forms and not hidden and attrs.get("type", "submit").lower() == "submit":
            self._keep(tag, self._attributes(tag, attrs, name=attrs.get("name", ""), value=attrs.get("value", "")))
            return
        if tag in _DROP or (hidden and tag not in _VOID):
            if tag not in _VOID:
                self._push(tag, None, "drop")
            return
        if tag == "img":
            self._img(attrs)
            return
        if tag == "br" or tag == "hr":
            self._close_implied(tag)
            self._append([tag, {}, []])
            return
        if tag == "form":
            action = self._resolve(attrs.get("action") or self.url)
            if attrs.get("method", "get").strip().lower() == "get" and action:
                self._close_implied(tag)
                self._keep(tag, {"action": _without_fragment(action)})
            return
        if tag == "a" and "name" in attrs and "id" not in attrs:
            attrs["id"] = attrs["name"]
        if tag in _KEEP:
            self._close_implied(tag)
            self._keep(tag, self._attributes(tag, attrs))
            if tag == "h1" and self.heading is None and self.stack[-1][0] == "h1":
                self.heading = self.stack[-1][1]

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag == "title" and self.in_title:
            self.in_title, self.title_done = False, True
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index][0] == tag:
                while len(self.stack) > index:
                    self._pop()
                return

    def handle_data(self, data):
        if self.in_title and len(self.title_parts) < 50:
            self.title_parts.append(data)
        if self.drop or self.full or not data:
            return
        if not self.pre:
            data = re.sub(r"\s+", " ", data)
            if data == " " and (self._parent()[0] in _NO_TEXT or not self._parent()[2]):
                return
        if self.chars + len(data) > MAX_CHARS:
            self.full = True
            return
        self.chars += len(data)
        children = self._parent()[2]
        if children and isinstance(children[-1], str):
            children[-1] += data
        else:
            self._append(data)

    # ---------------------------------------------------------------- elements

    def _keep(self, tag, attrs):
        if len(self.stack) >= MAX_DEPTH:
            return  # too deep: the text still shows, in the parent
        node = [tag, attrs, []]
        if self._append(node):
            self._push(tag, node, "keep")

    def _attributes(self, tag, attrs, **extra):
        kept = {key: value for key, value in extra.items() if value}
        element_id = attrs.get("id", "").strip()
        if element_id and len(element_id) <= 120:
            kept["id"] = element_id
        if attrs.get("dir", "").lower() in ("ltr", "rtl", "auto"):
            kept["dir"] = attrs["dir"].lower()
        if tag == "a":
            href = self._link(attrs.get("href"))
            if href:
                kept["href"] = href
                fragment = urllib.parse.urldefrag(href)[1]
                if fragment and _without_fragment(href) == _without_fragment(self.url):
                    kept["hash"] = fragment
            if attrs.get("title"):
                kept["title"] = attrs["title"][:300]
        elif tag == "abbr" and attrs.get("title"):
            kept["title"] = attrs["title"][:300]
        elif tag in ("td", "th"):
            for key in ("colspan", "rowspan"):
                number = _number(attrs.get(key, ""), 2, 100)
                if number:
                    kept[key] = number
        elif tag == "ol":
            number = _number(attrs.get("start", ""), -100000, 100000)
            if number is not None:
                kept["start"] = number
        elif tag == "details" and "open" in attrs:
            kept["open"] = True
        return kept

    def _img(self, attrs):
        width, height = _number(attrs.get("width", ""), 0, 10000), _number(attrs.get("height", ""), 0, 10000)
        if (width is not None and width <= 2) or (height is not None and height <= 2):
            return  # a tracking pixel
        source = self._image(attrs)
        if not source:
            return
        kept = {"src": source, "alt": re.sub(r"\s+", " ", attrs.get("alt", "")).strip()[:300]}
        if width:
            kept["width"] = width
        if height:
            kept["height"] = height
        if self._append(["img", kept, []]) and not source.startswith("data:") and source not in self.images:
            self.images.append(source)

    def _input(self, attrs):
        kind = attrs.get("type", "text").strip().lower() or "text"
        name, value = attrs.get("name", "")[:200], attrs.get("value", "")[:2000]
        if kind in _TEXT_INPUTS:
            self._append(["input", {"type": "search" if kind == "search" else "text", "name": name, "value": value,
                                    "placeholder": attrs.get("placeholder", "")[:200]}, []])
        elif kind == "hidden" and name:
            self._append(["input", {"type": "hidden", "name": name, "value": value}, []])
        elif kind in ("submit", "image"):
            label = value or attrs.get("alt", "") or "Submit"
            self._append(["button", {"name": name, "value": value} if name else {}, [label[:200]]])

    def _meta(self, attrs):
        if attrs.get("http-equiv", "").strip().lower() != "refresh" or self.refresh is not None:
            return
        match = _REFRESH.match(attrs.get("content", ""))
        if match and match.group(2).strip() and float(match.group(1)) <= 10:
            self.refresh = self._link(match.group(2).strip())

    # ---------------------------------------------------------------- results

    @property
    def title(self):
        return re.sub(r"\s+", " ", "".join(self.title_parts)).strip()[:300]

    def reading_root(self):
        """The page's main text: the element holding most of its paragraphs (as reader modes do),
        under the page's heading, without menus, side boxes, footers or forms."""
        best = _densest(self.root)
        if best is None:
            return None
        body = _without(best, _NOT_READING)
        children = [body]
        if self.heading is not None and not any(item[0] == "h1" for item in _elements(body)):
            children.insert(0, ["h1", {}, [_text_of(self.heading).strip()]])
        return ["div", {}, children]


def _text_size(node, limit=MAX_CHARS):
    size, pending = 0, [node]
    while pending and size < limit:
        item = pending.pop()
        if isinstance(item, str):
            size += len(item)
        else:
            pending.extend(item[2])
    return size


def _elements(node):
    pending = [node]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            yield item
            pending.extend(item[2])


def _text_of(node):
    if isinstance(node, str):
        return node
    return "".join(_text_of(child) for child in node[2])


_BLOCK_WEIGHT = {"p": 1, "pre": 1, "blockquote": 1, "li": .5, "dd": .5}


def _densest(root):
    """The element whose paragraphs hold the most text. Each paragraph counts fully for its parent
    and half for its grandparent, so text split into wrapped sections still adds up. List items
    count half: search results are a list, but so are menus and reference lists."""
    scores = {}

    def visit(node, ancestors):
        if isinstance(node, str):
            return
        if node[0] in _BLOCK_WEIGHT:
            size = _text_size(node, 20_000)
            if size >= 25:
                for weight, ancestor in zip((1, .5), reversed(ancestors[-2:])):
                    entry = scores.setdefault(id(ancestor), [0, ancestor])
                    entry[0] += size * weight * _BLOCK_WEIGHT[node[0]]
            return
        if len(ancestors) < MAX_DEPTH:
            ancestors.append(node)
            for child in node[2]:
                visit(child, ancestors)
            ancestors.pop()

    visit(root, [])
    score, best = max(scores.values(), key=lambda entry: entry[0], default=(0, None))
    return best if score >= READING_MIN_CHARS and best is not root else None


def _without(node, tags):
    if isinstance(node, str):
        return node
    return [node[0], node[1], [_without(child, tags) for child in node[2]
                               if isinstance(child, str) or child[0] not in tags]]


def plain_text(node, limit):
    """The page as text, for the AI: blocks on their own lines, list items marked, images by their alt text."""
    out, size = [], 0

    def walk(item, depth=0):
        nonlocal size
        if size > limit:
            return
        if isinstance(item, str):
            out.append(item)
            size += len(item)
            return
        tag, attrs, children = item
        if tag in ("input", "button", "form"):
            return  # the page's own search boxes and buttons are not its content
        if tag == "img":
            if attrs.get("alt"):
                out.append(f"[{attrs['alt']}]")
            return
        if tag == "br":
            out.append("\n")
            return
        block = tag in _TEXT_BLOCKS
        if block and out and not out[-1].endswith("\n"):
            out.append("\n")
        if tag == "li":
            out.append("- ")
        elif tag in ("td", "th"):
            out.append(" | ")
        elif tag in ("h1", "h2", "h3"):
            out.append("#" * int(tag[1]) + " ")
        if depth < MAX_DEPTH:
            for child in children:
                walk(child, depth + 1)
        if block:
            out.append("\n\n" if tag in _PARAGRAPHS else "\n")

    walk(node)
    text = "\n".join(line.strip() for line in "".join(out).splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _decode(data, charset):
    if data.startswith(b"\xef\xbb\xbf"):
        charset = "utf-8"
    if not charset:
        match = _META_CHARSET.search(data[:4096])
        charset = match.group(1).decode("ascii", "ignore") if match else None
    if charset and charset.lower() in ("iso-8859-1", "latin-1", "latin1", "us-ascii", "ascii"):
        charset = "windows-1252"  # what browsers do: pages labelled Latin-1 are almost always Windows-1252
    for name in (charset, "utf-8"):
        if name:
            try:
                return data.decode(name, errors="replace")
            except LookupError:
                continue
    return data.decode("utf-8", errors="replace")


def image_type(data):
    """The image format from the file's first bytes. Headers lie; SVG is never shown."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith(b"\x00\x00\x01\x00"):
        return "image/x-icon"
    return None


def _data_url(data):
    kind = image_type(data)
    if kind is None:
        raise BrowserError("This file is not an image the browser can show.")
    return f"data:{kind};base64," + base64.b64encode(data).decode("ascii")


class _Redirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never from the internet to this computer or the local network."""

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = checked_url(urllib.parse.urljoin(req.full_url, newurl), from_internet=is_internet(req.full_url))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, limit, accept, timeout, language="en"):
    """GET one address without cookies. Error pages are pages too."""
    languages = "it-IT,it;q=0.9,en;q=0.8" if language == "it" else "en-US,en;q=0.9"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept,
                                                   "Accept-Language": languages})
    opener = urllib.request.build_opener(_Redirects())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    with contextlib.closing(response):
        try:
            data = response.read(limit + 1)
        except (AttributeError, OSError):
            data = b""
        headers = response.headers
        return {"url": response.geturl(), "status": response.getcode(), "data": data[:limit],
                "cut": len(data) > limit, "type": headers.get_content_type() if headers else "",
                "charset": headers.get_content_charset() if headers else None}


class BrowserSession:
    """One worker thread does all the fetching. The gate keeps approvals per thread and task,
    so a site approved once stays approved while this browsing session lasts (an hour at most,
    or until every tab is closed)."""

    def __init__(self, fetch=fetch, minutes=SESSION_MINUTES):
        self._fetch = fetch
        self._minutes = minutes
        self._jobs = queue.Queue()
        self._lock = threading.Lock()
        self._pages = collections.OrderedDict()
        self._thread = None
        self._generation = 0

    # ---------------------------------------------------------------- worker

    def _run(self, work):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True, name="webui-browser")
                self._thread.start()
        box = {"done": threading.Event()}
        self._jobs.put((work, box))
        box["done"].wait()
        if "error" in box:
            raise box["error"]
        return box["value"]

    def _loop(self):
        while True:
            with network_gate.task(PURPOSE, SENDS, minutes=self._minutes):
                ends = time.monotonic() + max(1, self._minutes * 60 - 60)
                while time.monotonic() < ends:
                    try:
                        job = self._jobs.get(timeout=max(.1, ends - time.monotonic()))
                    except queue.Empty:
                        break
                    if job is None:
                        break  # every tab closed: approvals end with this task
                    work, box = job
                    try:
                        box["value"] = work()
                    except BaseException as error:  # noqa: BLE001 - handed to the caller's thread
                        box["error"] = error
                    finally:
                        box["done"].set()

    # ---------------------------------------------------------------- pages

    def open(self, address, typed=True, reading=True, referrer=None, language="en"):
        url = address_url(address, language) if typed else checked_url(address)
        with self._lock:
            source = self._pages.get(referrer)
            self._generation += 1
        if not typed:
            checked_url(url, from_internet=source is None or source["internet"])

        def work():
            target = url
            for _ in range(MAX_REFRESHES + 1):
                response = self._fetch(target, PAGE_BYTES, PAGE_ACCEPT, PAGE_TIMEOUT, language)
                record = self._record(response)
                refresh = record.pop("refresh", None)
                if not refresh or _without_fragment(refresh) == _without_fragment(response["url"]):
                    break
                target = checked_url(refresh, from_internet=record["internet"])
            with self._lock:
                self._pages[record["id"]] = record
                while len(self._pages) > OPEN_PAGES:
                    self._pages.popitem(last=False)
            return self._public(record, reading)

        return self._run(work)

    def _record(self, response):
        final = checked_url(response["url"])
        kind, data = response["type"], response["data"]
        record = {"id": str(uuid.uuid4()), "url": final, "status": response["status"], "cut": response["cut"],
                  "internet": is_internet(final), "host": urllib.parse.urlsplit(final).hostname or "",
                  "images": {"same": [], "other": []}, "other_hosts": [], "reading": None}
        if kind in ("text/html", "application/xhtml+xml") or (not kind and data.lstrip()[:1] == b"<"):
            parser = _Page(final)
            parser.feed(_decode(data, response["charset"]))
            parser.close()
            record.update(title=parser.title or record["host"], full=parser.root, reading=parser.reading_root(),
                          refresh=parser.refresh, cut=record["cut"] or parser.full)
            self._sort_images(record, parser.images)
        elif kind.startswith("text/"):
            text = _decode(data, response["charset"])[:MAX_CHARS]
            record.update(title=final.rsplit("/", 1)[-1] or record["host"], full=["div", {}, [["pre", {}, [text]]]])
        elif kind.startswith("image/") and image_type(data):
            name = final.rsplit("/", 1)[-1] or record["host"]
            record.update(title=name, full=["div", {}, [["img", {"src": _data_url(data), "alt": name}, []]]])
        elif response["status"] and response["status"] >= 400:
            raise BrowserError(f"The site answered {response['status']} and sent no page to show.")
        else:
            raise BrowserError(f"This address is a file ({kind or 'unknown type'}), not a web page. "
                               "The browser shows web pages only.")
        return record

    def _sort_images(self, record, images):
        same, other, hosts = [], [], []
        for url in images:
            host = urllib.parse.urlsplit(url).hostname or ""
            if record["internet"] and network_gate.classify(host) != network_gate.INTERNET:
                continue  # a page from the internet never makes this app fetch from your network
            if host == record["host"]:
                same.append(url)
            elif host in hosts or len(hosts) < IMAGE_HOSTS:
                if host not in hosts:
                    hosts.append(host)
                other.append(url)
        record["images"] = {"same": same[:IMAGES_PER_PAGE], "other": other[:IMAGES_PER_PAGE]}
        record["other_hosts"] = sorted({urllib.parse.urlsplit(url).hostname for url in record["images"]["other"]})

    def _public(self, record, reading):
        use_reading = bool(reading) and record["reading"] is not None
        return {"id": record["id"], "url": record["url"], "host": record["host"], "title": record["title"],
                "secure": record["url"].startswith("https:"), "status": record["status"], "cut": record["cut"],
                "tree": record["reading"] if use_reading else record["full"], "reading": use_reading,
                "can_read": record["reading"] is not None,
                "images": {"same": len(record["images"]["same"]), "other": len(record["images"]["other"]),
                           "other_hosts": record["other_hosts"]}}

    def _page(self, page_id):
        with self._lock:
            record = self._pages.get(page_id)
        if record is None:
            raise BrowserError("This page is no longer open. Reload it.")
        return record

    def view(self, page_id, reading):
        return self._public(self._page(page_id), reading)

    def images(self, page_id, which):
        record = self._page(page_id)
        if which not in ("same", "other"):
            raise BrowserError("Choose which images to show.")
        urls = record["images"][which]
        with self._lock:
            generation = self._generation

        def work():
            scope = (network_gate.task(IMAGE_PURPOSE, IMAGE_SENDS, minutes=10, hosts=record["other_hosts"])
                     if which == "other" else contextlib.nullcontext())
            found, failed, total = {}, 0, 0
            with scope:
                for url in urls:
                    if self._generation != generation or total >= IMAGES_BYTES:
                        break  # the person moved on, or the page has enough images
                    try:
                        response = self._fetch(url, IMAGE_BYTES, IMAGE_ACCEPT, IMAGE_TIMEOUT)
                        if response["cut"] or not response["status"] or response["status"] >= 400:
                            raise BrowserError("Image not available.")
                        found[url] = _data_url(response["data"])
                        total += len(response["data"])
                    except network_gate.InternetBlocked:
                        if not found:
                            raise
                        break
                    except (OSError, ValueError):
                        failed += 1
            return {"images": found, "failed": failed}

        return self._run(work)

    def text(self, page_id, limit_bytes):
        record = self._page(page_id)
        header = f"{record['title']}\n{record['url']}\n\n"
        text = plain_text(record["reading"] or record["full"], limit_bytes)
        note = "\n\n[The page continues; the rest was cut to fit.]"
        data = (header + text).encode("utf-8")
        if len(data) > limit_bytes:
            data = data[:max(0, limit_bytes - len(note.encode("utf-8")))].decode("utf-8", "ignore").encode("utf-8")
            data += note.encode("utf-8")
        return {"title": record["title"], "url": record["url"], "text": data.decode("utf-8")}

    def forget(self):
        with self._lock:
            self._pages.clear()
            self._generation += 1
        self._jobs.put(None)


_session_lock = threading.Lock()


class BrowserApi:
    """Mixed into the desktop API: the Browser section of the sidebar."""

    def _browser(self):
        with _session_lock:
            session = getattr(self, "_browser_session", None)
            if session is None:
                session = self._browser_session = BrowserSession()
            return session

    @json_method
    def browser_open(self, address, options=None):
        options = options if isinstance(options, dict) else {}
        page = self._browser().open(address, typed=options.get("typed", True) is not False,
                                    reading=options.get("reading", True) is not False,
                                    referrer=options.get("referrer"), language=options.get("language", "en"))
        return {"ok": True, "page": page}

    @json_method
    def browser_view(self, page_id, reading=True):
        return {"ok": True, "page": self._browser().view(page_id, reading is not False)}

    @json_method
    def browser_images(self, page_id, which="same"):
        return {"ok": True, **self._browser().images(page_id, which)}

    @json_method
    def browser_text(self, page_id):
        import chat_attachments
        limit = chat_attachments.file_limit(self._store.directory) - 1024
        return {"ok": True, **self._browser().text(page_id, max(4096, limit))}

    @json_method
    def browser_forget(self):
        self._browser().forget()
        return {"ok": True}
