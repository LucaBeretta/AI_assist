"""Manual lifecycle gate for opt-in LAN sharing.

The application must not instantiate a PairingHost during startup, even for
status checks: PairingHost.__init__ binds an HTTPS socket. This controller
creates the service only from an explicit user action and never remembers an
'enabled' state across runs.

This is a UI lifecycle guard, NOT peer authentication/authorization.
"""

from threading import RLock


class SharingStateError(RuntimeError):
    """The manual sharing controller cannot complete the requested transition."""


class ManualSharingController:
    """Manage a pairing host on demand without startup/network side effects.

    Factory is a zero-argument callable returning a service with .start() and
    .stop() methods. Production code can use a lazy factory:
        ManualSharingController(lambda: PairingHost(bind_ip, data_dir))
    Do not call enable() from application startup or restored preferences.
    """

    def __init__(self, host_factory):
        if not callable(host_factory):
            raise TypeError("A callable host factory is required.")
        self._factory = host_factory
        self._host = None
        self._state = "off"
        self._lock = RLock()

    @property
    def state(self):
        with self._lock:
            return self._state

    @property
    def enabled(self):
        return self.state == "on"

    def enable(self, *, user_initiated=False):
        """Explicit share action; never called by launch, discovery or restore.

        The flag is a guard against accidental initialization, not a security
        token. The UI must only pass True from an actual user interaction.
        """
        if user_initiated is not True:
            raise SharingStateError("LAN sharing requires an explicit user action.")
        with self._lock:
            if self._state == "on":
                return False
            if self._state != "off":
                raise SharingStateError("LAN sharing is transitioning.")
            self._state = "starting"
            host = None
            try:
                host = self._factory()
                host.start()
            except BaseException:
                # Host constructors may already have opened a socket.
                try:
                    if host is not None:
                        host.stop()
                finally:
                    self._state = "off"
                    self._host = None
                raise
            self._host = host
            self._state = "on"
            return True

    def disable(self):
        """Stop sharing; no automatic retry or reactivation."""
        with self._lock:
            if self._state == "off":
                return False
            if self._state != "on":
                raise SharingStateError("LAN sharing is transitioning.")
            host = self._host
            self._state = "stopping"
            try:
                host.stop()
            finally:
                self._host = None
                self._state = "off"
            return True

    def close(self):
        """Called on application shutdown. Never turns sharing on."""
        return self.disable()
