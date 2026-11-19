"""Desktop adapters over the mandatory gate and existing Windows firewall lock."""
from copy import deepcopy
import threading

import firewall_lock
import network_gate
from webui.api_utils import json_method
from webui.internet import Approvals


class PrivacyApi:
    def _init_privacy(self):
        self._internet = Approvals(self._bridge.emit)
        self._privacy_lock = threading.RLock()
        folder = getattr(self._colibri, "folder", lambda: None)
        self._firewall = firewall_lock.Lock(folder, self._store.directory / "firewall-lift.json")
        self._privacy_started = False
        self._firewall_outcomes = []

    def _poll_firewall(self):
        self._firewall_outcomes.extend(self._firewall.poll())
        self._firewall_outcomes = self._firewall_outcomes[-10:]

    def _start_privacy(self):
        with self._privacy_lock:
            if self._privacy_started or self._closed:
                return
            self._privacy_started = True
            network_gate.set_approver(self._internet.approve)
            network_gate.add_listener(self._connection_entry)
            network_gate.set_lock_check(self._firewall.blocks)
            self._firewall.refresh()

    def _connection_entry(self, entry):
        if not self._closed:
            self._bridge.emit("connection", entry=entry)

    def _firewall_summary(self):
        with self._privacy_lock:
            self._poll_firewall()
            return deepcopy(self._firewall.summary)

    @json_method
    def privacy_state(self):
        with self._privacy_lock:
            self._poll_firewall()
            if self._firewall.summary is None and not self._firewall.busy and not self._closed:
                self._firewall.refresh()
            return {"ok": True, "gate": network_gate.status(), "connections": network_gate.recent()[-200:],
                    "firewall": deepcopy(self._firewall.summary), "busy": self._firewall.busy,
                    "approval": self._internet.pending()}

    @json_method
    def respond_internet(self, approval_id, action):
        return self._internet.respond(approval_id, action)

    @json_method
    def change_firewall(self, action, minutes=None):
        if action not in {"on", "off", "relock", "lift"}:
            raise ValueError("Choose on, off, relock or lift for the firewall lock.")
        if action == "lift" and (type(minutes) is not int or minutes not in firewall_lock.LIFT_MINUTES):
            raise ValueError("Choose a firewall lift of 5, 15 or 60 minutes.")
        def work(progress, cancel):
            request = network_gate.Request("Firewall lock change", action, "Change the AI engines' Windows Firewall rules.",
                "No chat data. This changes firewall rules and needs administrator approval.", "webui/privacy.py", "this change only")
            if not self._internet.approve(request) or cancel.is_set():
                raise RuntimeError("The firewall change was blocked.")
            with self._privacy_lock:
                if self._firewall.busy:
                    raise ValueError("Wait for the current firewall check or change.")
                self._firewall_outcomes = []
                self._firewall.run(action, minutes)
            while not cancel.wait(.1):
                with self._privacy_lock:
                    self._poll_firewall()
                    if not self._firewall.busy:
                        for _, error in self._firewall_outcomes:
                            if error:
                                raise RuntimeError(error)
                        return self.privacy_state()
            return self.privacy_state()
        with self._lock:
            if self._closed:
                raise ValueError("The app is closing.")
            return self._jobs.start("firewall", work, key="firewall")

    def _close_privacy(self):
        self._internet.close()
        with self._privacy_lock:
            if self._privacy_started:
                network_gate.set_approver(None)
                network_gate.set_lock_check(None)
                network_gate.remove_listener(self._connection_entry)
                self._privacy_started = False
