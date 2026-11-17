"""User-directed feedback and evidence-based developer follow-ups."""

import tkinter as tk
from tkinter import END

from feedback import feedback_prompt, verification_summary


class FeedbackMixin:
    def clear_messages(self):
        self.close_feedback()
        self.feedback_buttons = []
        return super().clear_messages()

    def close_feedback(self):
        window = getattr(self, "feedback_window", None)
        if window is not None and window.winfo_exists():
            if window.grab_current() == window:
                window.grab_release()
            window.destroy()
        self.feedback_window = None

    def open_feedback(self, answer):
        if self.busy() or self.closing:
            self.set_status("Finish or stop the current operation before giving feedback.")
            return
        existing = getattr(self, "feedback_window", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        c = self.COLORS
        window = self.feedback_window = tk.Toplevel(self.root)
        window.title("Give feedback")
        window.geometry("540x480")
        window.minsize(460, 440)
        window.configure(bg=c["background"])
        window.transient(self.root)
        tk.Label(window, text="What would make this better?", font=("Segoe UI", 17, "bold"),
                 bg=c["background"], fg=c["ink"], anchor="w").pack(fill="x", padx=24, pady=(22, 8))
        tk.Label(window, text="Your feedback becomes a follow-up you can review before sending.",
                 bg=c["background"], fg=c["muted"], font=("Segoe UI", 10), wraplength=460,
                 anchor="w", justify="left").pack(fill="x", padx=24, pady=(0, 16))
        fields = []
        for label in ("What should change?", "What would a good result look like? (optional)"):
            tk.Label(window, text=label, font=("Segoe UI", 10), bg=c["background"],
                     fg=c["ink"], anchor="w").pack(fill="x", padx=24, pady=(6, 6))
            field = tk.Text(window, height=4, wrap="word", font=("Segoe UI", 11),
                            bg=c["surface"], fg=c["ink"], insertbackground=c["accent"],
                            relief="flat", padx=10, pady=8, undo=True)
            field.pack(fill="both", expand=True, padx=24)
            fields.append(field)
        window.feedback_text, window.criteria_text = fields
        status = tk.Label(window, text="", bg=c["background"], fg=c["error"],
                          font=("Segoe UI", 9), anchor="w", wraplength=460)
        status.pack(fill="x", padx=24, pady=(8, 0))
        actions = tk.Frame(window, bg=c["background"])
        actions.pack(fill="x", padx=24, pady=(8, 20))

        def apply():
            try:
                body = feedback_prompt(answer, fields[0].get("1.0", "end-1c"),
                                       fields[1].get("1.0", "end-1c"), developer=self.current_mode == "dev")
            except ValueError as error:
                status.config(text=str(error))
                return
            if self.stage_followup(body):
                self.close_feedback()
                self.input_box.focus_set()

        window.apply_button = self.button(actions, "Use feedback", apply,
                                          c["accent"], "white", c["accent_hover"])
        window.apply_button.pack(side="right")
        self.button(actions, "Cancel", self.close_feedback,
                    c["background"], c["muted"], c["user"]).pack(side="right", padx=10)
        window.protocol("WM_DELETE_WINDOW", self.close_feedback)
        window.bind("<Escape>", lambda event: self.close_feedback())
        window.grab_set()
        fields[0].focus_set()

    def stage_followup(self, body):
        if self.busy() or self.closing:
            self.set_status("Finish or stop the current operation before using feedback.")
            return False
        draft = self.input_box.get("1.0", "end-1c")
        self.input_box.config(state="normal")
        self.input_box.delete("1.0", END)
        self.input_box.insert("1.0", draft + "\n\n" + body if draft.strip() else body)
        self.persist_current()
        self.update_controls()
        action = "Run agent" if self.current_mode == "dev" else "Send"
        self.set_status(f"Feedback added to your draft. Review it, then choose {action}.")
        return True

    def prepare_verification(self):
        if self.busy() or not self.project_ready or self.current_mode != "dev":
            return
        answer = next((item["content"] for item in reversed(self.conversation)
                       if item["role"] == "assistant"), "Current project state")
        body = feedback_prompt(answer,
            "Check the current result against my request. Inspect the current files and identify appropriate checks. "
            "Request approval for relevant test or build commands and report their actual results. "
            "Explain failures and remaining manual checks before proposing more changes.",
            "The requested behavior works, relevant checks pass, and remaining uncertainty is explicit.", developer=True)
        if self.stage_followup(body):
            self.input_box.focus_set()

    def add_feedback_actions(self, card, answer, background):
        c = self.COLORS
        actions = tk.Frame(card, bg=background)
        actions.pack(fill="x", padx=14, pady=(0, 10))
        buttons = getattr(self, "feedback_buttons", [])
        feedback = self.button(actions, "Give feedback", lambda: self.open_feedback(answer),
                               background, c["accent"], c["user"])
        feedback.config(font=("Segoe UI", 9), padx=8, pady=5)
        feedback.pack(side="left")
        buttons.append(feedback)
        self.feedback_buttons = buttons

    def refresh_verification(self):
        if hasattr(self, "verification_label"):
            self.verification_label.config(text=verification_summary(self.dev_activity))

    def update_feedback_controls(self):
        for button in getattr(self, "feedback_buttons", []):
            if button.winfo_exists():
                button.config(state="disabled" if self.busy() else "normal")
        if hasattr(self, "verify_button"):
            self.verify_button.config(state="normal" if self.current_mode == "dev" and self.project_ready
                                      and not self.busy() else "disabled")
