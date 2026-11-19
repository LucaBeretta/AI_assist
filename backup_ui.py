"""Chat options > Restore chats from a backup: the automatic backups, back up now, restore."""

from datetime import timezone
import tkinter as tk
from tkinter import filedialog, messagebox

from app_core import backups


def readable_size(size):
    return f"{size / 1024:.0f} KB" if size < 1024 * 1024 else f"{size / 1024 ** 2:.1f} MB"


class BackupWindow:
    def __init__(self, app):
        self.app = app
        c = app.COLORS
        window = self.window = tk.Toplevel(app.root)
        window.title("Restore chats")
        window.geometry("620x500")
        window.minsize(480, 380)
        window.configure(bg=c["background"])
        window.transient(app.root)

        def label(text, size=10, weight="normal", color="muted", pady=(0, 8)):
            widget = tk.Label(window, text=text, bg=c["background"], fg=c[color], font=("Segoe UI", size, weight),
                              anchor="w", justify="left", wraplength=560)
            widget.pack(fill="x", padx=22, pady=pady)
            return widget

        label("Backups of your chats", 15, "bold", "ink", (18, 4))
        label("AI Assist backs up every chat and your shared notes once a day, on this computer, and keeps the "
              "newest 14. Copy a backup to a USB stick to keep it somewhere else.\n\nRestoring adds chats that are "
              "missing and replaces damaged ones (the damaged file is kept in the damaged folder). It never changes "
              "a chat you still have. Shared notes come back only if yours are empty.", pady=(0, 10))
        footer = tk.Frame(window, bg=c["background"])
        footer.pack(side="bottom", fill="x", padx=22, pady=14)
        app.button(footer, "Close", window.destroy, c["user"], c["ink"], c["border"]).pack(side="right")
        self.restore_button = app.button(footer, "Restore selected", self.restore_selected,
                                         c["accent"], c["on_accent"], c["accent_hover"])
        self.restore_button.pack(side="right", padx=(0, 10))
        app.button(footer, "Other backup file...", self.restore_file, c["user"], c["ink"], c["border"]).pack(side="left")
        self.backup_button = app.button(footer, "Back up now", self.back_up_now, c["user"], c["ink"], c["border"])
        self.backup_button.pack(side="left", padx=(10, 0))
        self.status = label("", color="amber", pady=(0, 0))
        self.status.pack_configure(side="bottom")
        self.listbox = tk.Listbox(window, height=8, font=(app.mono, 10), bg=c["surface"], fg=c["ink"],
                                  selectbackground=c["user"], selectforeground=c["ink"], relief="flat",
                                  borderwidth=0, highlightthickness=1, highlightbackground=c["border"],
                                  exportselection=False)
        self.listbox.pack(fill="both", expand=True, padx=22)
        self.items = []
        self.refresh()

    def refresh(self):
        if not self.window.winfo_exists():
            return
        try:
            self.items = backups.list_backups(self.app.chat_store)
        except OSError as error:
            self.items = []
            self.status.config(text=f"Could not read the backups folder: {error}")
        self.listbox.delete(0, "end")
        for item in self.items:
            local = item["created"].replace(tzinfo=timezone.utc).astimezone()
            self.listbox.insert("end", f"{local:%Y-%m-%d %H:%M}   {readable_size(item['size'])}")
        if self.items:
            self.listbox.selection_set(0)
        else:
            self.listbox.insert("end", "No backups yet. Choose Back up now.")
        self.update_controls()

    def update_controls(self):
        busy = self.app.busy() or self.app.backup_running
        self.restore_button.config(state="disabled" if busy or not self.items else "normal")
        self.backup_button.config(state="disabled" if busy else "normal")

    def restore_selected(self):
        selection = self.listbox.curselection()
        if not selection or selection[0] >= len(self.items):
            return
        self.restore(self.items[selection[0]]["path"])

    def restore_file(self):
        path = filedialog.askopenfilename(parent=self.window, title="Choose an AI Assist backup",
                                          filetypes=[("Zip archive", "*.zip")])
        if path:
            self.restore(path)

    def restore(self, path):
        if not messagebox.askyesno("Restore chats?", "Add the chats from this backup that are missing here, and "
                                   "replace damaged ones?\n\nChats you still have are not changed.", parent=self.window):
            return
        self.status.config(text="Restoring...")
        self.app.start_restore(path)
        self.update_controls()

    def back_up_now(self):
        self.status.config(text="Backing up...")
        self.app.start_backup(manual=True)
        self.update_controls()

    def finished(self, text):
        if self.window.winfo_exists():
            self.status.config(text=text)
            self.refresh()
