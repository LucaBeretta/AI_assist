"""Small native controls shared by the desktop workspace."""

import tkinter as tk


class HoverButton(tk.Button):
    """A flat button whose hover never overwrites a later application state."""

    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self._appearance_bg = str(self.cget("background"))
        self._appearance_hover = str(self.cget("activebackground"))
        self._hovered = False
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def configure(self, cnf=None, **options):
        changes = dict(cnf) if isinstance(cnf, dict) else {}
        changes.update(options)
        result = super().configure(cnf, **options)
        if "background" in changes or "bg" in changes:
            self._appearance_bg = str(self.cget("background"))
        if "activebackground" in changes:
            self._appearance_hover = str(self.cget("activebackground"))
        if changes and hasattr(self, "_hovered"):
            self.refresh_hover()
        return result

    config = configure

    def refresh_hover(self):
        color = self._appearance_hover if self._hovered and str(self.cget("state")) == "normal" else self._appearance_bg
        # Bypass configure: a pointer highlight is temporary, not a new base.
        tk.Button.configure(self, background=color)

    def _enter(self, event):
        self._hovered = True
        self.refresh_hover()

    def _leave(self, event):
        self._hovered = False
        self.refresh_hover()
