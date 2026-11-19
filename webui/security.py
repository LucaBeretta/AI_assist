"""Who may use the web workspace's API, and the headers every answer carries.

The design assumes hostile pages in the same browser: an HTML preview runs
generated scripts on another 127.0.0.1 port, and any website can make the
browser send requests to loopback addresses. Ports do not separate *sites*,
so cookies would be sent along; the API therefore never uses cookies.

- **Launch codes.** The server prints and opens one URL whose fragment holds a
  single-use code (fragments never reach the network or server logs). The page
  exchanges it for a session token. A code expires after five minutes.
- **Session tokens.** Sent as ``Authorization: Bearer``. A cross-origin page
  cannot add that header without a CORS preflight, and the server never
  answers preflights. Tokens die with the server process.
- **Host, Origin, Sec-Fetch-Site.** ``Host`` must name this server exactly
  (DNS rebinding). An ``Origin`` other than this server is refused, and so is
  any ``Sec-Fetch-Site`` other than ``same-origin`` on the API.
"""

import hmac
import secrets
import threading
import time

LAUNCH_SECONDS = 300
MAX_SESSIONS = 16

PAGE_POLICY = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
               "connect-src 'self'; font-src 'self'; manifest-src 'self'; base-uri 'none'; "
               "form-action 'none'; frame-ancestors 'none'")
API_POLICY = "default-src 'none'; frame-ancestors 'none'"
COMMON_HEADERS = (
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=(), usb=(), serial=(), bluetooth=(), payment=()"),
    ("Cache-Control", "no-store"),
)


class Sessions:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.codes = {}
        self.tokens = []

    def new_launch_code(self):
        code = secrets.token_urlsafe(24)
        with self.lock:
            now = self.clock()
            self.codes = {key: until for key, until in self.codes.items() if until > now}
            self.codes[code] = now + LAUNCH_SECONDS
        return code

    def exchange(self, code):
        """A new session token for an unused, unexpired launch code; otherwise None."""
        if not isinstance(code, str) or not code:
            return None
        with self.lock:
            now = self.clock()
            match = next((key for key in self.codes if hmac.compare_digest(key.encode(), code.encode())), None)
            if match is None or self.codes.pop(match) <= now:
                return None
            token = secrets.token_urlsafe(32)
            self.tokens = (self.tokens + [token])[-MAX_SESSIONS:]
            return token

    def valid(self, header):
        """True for ``Bearer <token>`` with a token this server issued."""
        if not isinstance(header, str) or not header.startswith("Bearer "):
            return False
        supplied = header[len("Bearer "):].strip().encode()
        with self.lock:
            tokens = list(self.tokens)
        # Compare against every token so timing does not reveal which one nearly matched.
        return any([hmac.compare_digest(token.encode(), supplied) for token in tokens])


def request_problem(headers, authority, api):
    """Why a request must be refused, as (status, message), or None.

    ``authority`` is this server's ``host:port``; ``api`` is True for /api paths."""
    if headers.get("Host") != authority:
        return 421, "This address is not served here."
    origin = headers.get("Origin")
    if origin is not None and origin != "http://" + authority:
        return 403, "Requests from other sites are refused."
    if api:
        site = headers.get("Sec-Fetch-Site")
        if site is not None and site != "same-origin":
            return 403, "Requests from other sites are refused."
    return None
