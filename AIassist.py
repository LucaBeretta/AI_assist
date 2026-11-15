import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, END
import urllib.error
import urllib.request
import urllib.parse


def ollama_url():
    """Convert Ollama's listening address into a usable client URL."""
    host = os.environ.get("OLLAMA_HOST", "").strip() or "http://localhost:11434"
    if "://" not in host:
        host = f"http://{host}"
    parsed = urllib.parse.urlsplit(host)
    if parsed.hostname in ("0.0.0.0", "::"):
        port = parsed.port if parsed.port is not None else 11434
        parsed = parsed._replace(netloc=f"127.0.0.1:{port}")
    return parsed.geturl().rstrip("/")


def list_ollama_models():
    """Read the models installed on the configured Ollama server."""
    host = ollama_url()
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as error:
        raise RuntimeError(
            f"Could not load models from {host}. Start Ollama, then click Refresh."
        ) from error
    except json.JSONDecodeError as error:
        raise RuntimeError("Ollama returned an invalid model list.") from error

    models = result.get("models") if isinstance(result, dict) else None
    if not isinstance(models, list):
        raise RuntimeError("Ollama returned an invalid model list.")
    return sorted({
        model["name"] for model in models
        if isinstance(model, dict) and isinstance(model.get("name"), str)
        and model["name"].strip()
    })


def request_ollama(messages, model=None):
    """Send the conversation to the local Ollama chat API."""
    host = ollama_url()
    model = model or os.environ.get("OLLAMA_MODEL", "qwen3:30b")
    request = urllib.request.Request(
        f"{host}/api/chat",
        data=json.dumps({
            "model": model,
            "messages": messages,
            "stream": False
        }).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Ollama returned HTTP {error.code}: {details or error.reason}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Could not connect to Ollama at {host}. Start Ollama or run 'ollama serve', then try again."
        ) from error
    except TimeoutError as error:
        raise RuntimeError("The Ollama request timed out.") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("Ollama returned an invalid response.") from error

    message = result.get("message") if isinstance(result, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Ollama's response did not contain a message.")
    return content.strip()

class AIAssistantGUI:
    COLORS = {
        "background": "#f5f6fa",
        "surface": "#ffffff",
        "sidebar": "#171e2d",
        "sidebar_hover": "#2b354a",
        "sidebar_muted": "#9aa8bf",
        "ink": "#243047",
        "muted": "#65738b",
        "border": "#e1e6ef",
        "accent": "#4c61d8",
        "accent_hover": "#3c4fbd",
        "user": "#edf0ff",
        "success": "#51c7a4",
        "error": "#ad473e",
    }

    def __init__(self, root):
        self.root = root
        self.conversation = []
        self.response_queue = queue.Queue()
        self.model_queue = queue.Queue()
        self.model_name = tk.StringVar(root, value="")
        self.active_model = None
        self.is_loading_models = False
        self.is_waiting = False
        self.empty_state = None
        self.closed = False
        self.root.title("AI Assist | Your local workspace")
        self.root.geometry("1120x780")
        self.root.minsize(860, 640)
        self.root.configure(bg=self.COLORS["background"])
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.configure_styles()
        self.build_sidebar()
        self.build_workspace()
        self.show_empty_state()
        self.refresh_models()
        self.poll_id = self.root.after(100, self.process_responses)
        self.root.bind("<Control-n>", self.new_chat)
        self.root.bind_all("<MouseWheel>", self.scroll_messages, add="+")

    def configure_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Model.TCombobox", padding=(10, 10), arrowsize=14,
            fieldbackground="#252f43", background="#252f43",
            foreground="#f5f7fc", arrowcolor="#bdc8db",
            bordercolor="#35415a", lightcolor="#35415a", darkcolor="#35415a",
        )
        style.map(
            "Model.TCombobox",
            fieldbackground=[("readonly", "#252f43"), ("disabled", "#20283a")],
            foreground=[("disabled", "#8f9bb0"), ("readonly", "#f5f7fc")],
            selectbackground=[("readonly", "#252f43")],
            selectforeground=[("readonly", "#f5f7fc")],
            bordercolor=[("focus", "#8294fa")],
        )
        style.configure(
            "Chat.Vertical.TScrollbar", background="#ccd3df", troughcolor=self.COLORS["background"],
            borderwidth=0, width=8, gripcount=0, troughborderwidth=0,
            lightcolor=self.COLORS["background"], darkcolor=self.COLORS["background"],
            bordercolor=self.COLORS["background"],
        )
        style.layout("Chat.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"}),
            ]}),
        ])
        style.map("Chat.Vertical.TScrollbar", background=[("active", "#aeb9cd")])
        style.configure(
            "Activity.Horizontal.TProgressbar", background=self.COLORS["accent"],
            troughcolor="#e7eaf4", borderwidth=0, thickness=3,
        )
        self.root.option_add("*TCombobox*Listbox.background", "#252f43")
        self.root.option_add("*TCombobox*Listbox.foreground", "#f5f7fc")
        self.root.option_add("*TCombobox*Listbox.selectBackground", "#4c61d8")
        self.root.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))

    def button(self, parent, text, command, bg, fg, hover, **options):
        button = tk.Button(
            parent, text=text, command=command, bg=bg, fg=fg,
            activebackground=hover, activeforeground=fg,
            disabledforeground="#8794aa", font=("Segoe UI", 10),
            relief="flat", borderwidth=0, highlightthickness=0,
            cursor="hand2", padx=14, pady=10, **options,
        )
        button.bind("<Enter>", lambda event: button.config(bg=hover)
                    if button["state"] == "normal" else None)
        button.bind("<Leave>", lambda event: button.config(bg=bg)
                    if button["state"] == "normal" else None)
        return button

    def build_sidebar(self):
        c = self.COLORS
        self.sidebar = tk.Frame(self.root, bg=c["sidebar"], width=244)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        brand = tk.Frame(self.sidebar, bg=c["sidebar"])
        brand.pack(fill="x", padx=24, pady=(30, 28))
        mark = tk.Canvas(brand, width=34, height=34, bg=c["sidebar"], highlightthickness=0)
        mark.pack(side="left", padx=(0, 10))
        mark.create_rectangle(0, 0, 34, 34, fill="#7182f5", outline="")
        mark.create_text(17, 16, text="a", fill="white", font=("Segoe UI", 23, "bold"))
        tk.Label(brand, text="AI Assist", bg=c["sidebar"], fg="#f5f7fc",
                 font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Label(self.sidebar, text="YOUR OLLAMA WORKSPACE", bg=c["sidebar"],
                 fg=c["sidebar_muted"], font=("Segoe UI", 8, "bold"), anchor="w").pack(
                     fill="x", padx=24, pady=(0, 25))

        self.new_chat_button = self.button(
            self.sidebar, "+  New chat", self.new_chat, "#2b354a", "#f5f7fc", "#35425d", anchor="w"
        )
        self.new_chat_button.pack(fill="x", padx=20, pady=(0, 28))
        tk.Frame(self.sidebar, bg="#303a4d", height=1).pack(fill="x", padx=24, pady=(0, 24))
        tk.Label(self.sidebar, text="CHOOSE A MODEL", bg=c["sidebar"],
                 fg=c["sidebar_muted"], font=("Segoe UI", 8, "bold"), anchor="w").pack(
                     fill="x", padx=24, pady=(0, 10))
        self.model_picker = ttk.Combobox(
            self.sidebar, textvariable=self.model_name, state="disabled",
            style="Model.TCombobox", font=("Segoe UI", 10), height=10,
        )
        self.model_picker.pack(fill="x", padx=20)
        self.model_picker.bind("<<ComboboxSelected>>", self.select_model)
        self.model_help = tk.Label(
            self.sidebar, text="Select a model to start chatting.", bg=c["sidebar"],
            fg=c["sidebar_muted"], font=("Segoe UI", 9), wraplength=190, justify="left", anchor="w",
        )
        self.model_help.pack(fill="x", padx=24, pady=(10, 6))
        self.refresh_button = self.button(
            self.sidebar, "Refresh models", self.refresh_models,
            c["sidebar"], "#c3cde0", c["sidebar_hover"], anchor="w",
        )
        self.refresh_button.pack(fill="x", padx=10, pady=(0, 18))

        connection = tk.Frame(self.sidebar, bg=c["sidebar"])
        connection.pack(side="bottom", fill="x", padx=24, pady=26)
        tk.Frame(connection, bg="#303a4d", height=1).pack(fill="x", pady=(0, 20))
        status = tk.Frame(connection, bg=c["sidebar"])
        status.pack(fill="x")
        self.connection_dot = tk.Label(status, text="\u25cf", bg=c["sidebar"], fg="#9aa8bf",
                                       font=("Segoe UI", 9))
        self.connection_dot.pack(side="left", padx=(0, 6))
        self.connection_label = tk.Label(status, text="Connecting to Ollama", bg=c["sidebar"],
                                        fg="#cbd4e4", font=("Segoe UI", 9), anchor="w")
        self.connection_label.pack(side="left")
        tk.Label(connection, text="Powered by your own models.", bg=c["sidebar"],
                 fg=c["sidebar_muted"], font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(8, 0))

    def build_workspace(self):
        c = self.COLORS
        self.main_frame = tk.Frame(self.root, bg=c["background"])
        self.main_frame.pack(side="left", fill="both", expand=True)

        header = tk.Frame(self.main_frame, bg=c["surface"])
        header.pack(fill="x")
        heading = tk.Frame(header, bg=c["surface"])
        heading.pack(side="left", padx=30, pady=22)
        tk.Label(heading, text="Conversation", bg=c["surface"], fg=c["ink"],
                 font=("Segoe UI", 20, "bold"), anchor="w").pack(fill="x")
        self.chat_subtitle = tk.Label(
            heading, text="A little space to think, create, and explore.",
            bg=c["surface"], fg=c["muted"], font=("Segoe UI", 10), anchor="w",
        )
        self.chat_subtitle.pack(fill="x", pady=(3, 0))
        tk.Label(header, text="OLLAMA", bg="#eef0ff", fg=c["accent"],
                 font=("Segoe UI", 9, "bold"), padx=12, pady=7).pack(side="right", padx=28)
        tk.Frame(self.main_frame, bg=c["border"], height=1).pack(fill="x")

        # Pack the composer first so it stays visible as the conversation grows.
        composer_area = tk.Frame(self.main_frame, bg=c["background"])
        composer_area.pack(side="bottom", fill="x", padx=28, pady=(8, 18))
        self.composer_frame = tk.Frame(
            composer_area, bg=c["surface"], highlightbackground=c["border"], highlightthickness=1,
        )
        self.composer_frame.pack(fill="x")
        tk.Label(self.composer_frame, text="YOUR MESSAGE", bg=c["surface"], fg=c["muted"],
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", padx=16, pady=(12, 3))
        self.input_box = tk.Text(
            self.composer_frame, height=3, width=1, wrap="word", font=("Segoe UI", 11),
            bg=c["surface"], fg=c["ink"], insertbackground=c["accent"],
            selectbackground="#dce2ff", relief="flat", borderwidth=0,
            highlightthickness=0, padx=0, pady=4, spacing1=2, spacing3=2, undo=True,
        )
        self.input_box.pack(fill="x", padx=16)
        self.input_box.bind("<Return>", self.handle_enter)
        self.input_box.bind("<FocusIn>", lambda event: self.composer_frame.config(
            highlightbackground=c["accent"]))
        self.input_box.bind("<FocusOut>", lambda event: self.composer_frame.config(
            highlightbackground=c["border"]))
        composer_footer = tk.Frame(self.composer_frame, bg=c["surface"])
        composer_footer.pack(fill="x", padx=14, pady=(4, 12))
        self.composer_hint = tk.Label(
            composer_footer, text="Choose a model to start chatting", bg=c["surface"],
            fg=c["muted"], font=("Segoe UI", 9), anchor="w",
        )
        self.composer_hint.pack(side="left")
        self.send_button = self.button(
            composer_footer, "Send  \u2192", self.send_prompt, c["accent"], "white", c["accent_hover"],
        )
        self.send_button.pack(side="right")

        status_row = tk.Frame(self.main_frame, bg=c["background"])
        status_row.pack(side="bottom", fill="x", padx=30, pady=(4, 0))
        self.activity = ttk.Progressbar(
            status_row, mode="indeterminate", length=42, style="Activity.Horizontal.TProgressbar",
        )
        self.status_label = tk.Label(status_row, text="", bg=c["background"], fg=c["muted"],
                                     font=("Segoe UI", 9), anchor="w", wraplength=530, justify="left")
        self.status_label.pack(side="left", fill="x", expand=True)

        chat = tk.Frame(self.main_frame, bg=c["background"])
        chat.pack(fill="both", expand=True)
        self.chat_canvas = tk.Canvas(chat, bg=c["background"], highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(chat, orient="vertical", command=self.chat_canvas.yview,
                                  style="Chat.Vertical.TScrollbar")
        scrollbar.pack(side="right", fill="y", padx=(0, 7), pady=12)
        self.chat_canvas.pack(side="left", fill="both", expand=True)
        self.chat_canvas.configure(yscrollcommand=scrollbar.set)
        self.message_frame = tk.Frame(self.chat_canvas, bg=c["background"])
        self.chat_window = self.chat_canvas.create_window((0, 0), window=self.message_frame, anchor="nw")
        self.chat_canvas.bind("<Configure>", self.resize_chat)
        self.message_frame.bind("<Configure>", lambda event: self.chat_canvas.configure(
            scrollregion=self.chat_canvas.bbox("all")))

    def resize_chat(self, event):
        self.chat_canvas.itemconfigure(self.chat_window, width=event.width)

    def scroll_messages(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget == self.chat_canvas or widget == self.message_frame:
                self.chat_canvas.yview_scroll(-int(event.delta / 120), "units")
                return "break"
            widget = widget.master

    def show_empty_state(self):
        c = self.COLORS
        self.empty_state = tk.Frame(self.message_frame, bg=c["background"])
        self.empty_state.pack(fill="x", padx=30, pady=(64, 30))
        icon = tk.Canvas(self.empty_state, width=56, height=56, bg=c["background"], highlightthickness=0)
        icon.pack(pady=(0, 18))
        icon.create_oval(1, 1, 55, 55, fill="#e6eaff", outline="")
        icon.create_polygon(28, 10, 33, 23, 46, 28, 33, 33, 28, 46, 23, 33, 10, 28, 23, 23,
                            fill=c["accent"], outline="")
        tk.Label(self.empty_state, text="What can I help with?", bg=c["background"], fg=c["ink"],
                 font=("Segoe UI", 24, "bold")).pack()
        subtitle = (f"Start a fresh conversation with {self.model_name.get()}." if self.model_name.get()
                    else "Choose a model on the left, then make yourself at home.")
        tk.Label(self.empty_state, text=subtitle, bg=c["background"], fg=c["muted"],
                 font=("Segoe UI", 10), wraplength=440, justify="center").pack(pady=(8, 25))
        suggestions = tk.Frame(self.empty_state, bg=c["background"])
        suggestions.pack(fill="x")
        self.suggestion_buttons = []
        prompts = [
            ("Explore an idea", "Explain a complex idea using a simple everyday example."),
            ("Write something", "Help me write a clear and friendly email."),
            ("Help me code", "Help me plan and build a small Python project."),
        ]
        for column, (title, prompt) in enumerate(prompts):
            suggestions.columnconfigure(column, weight=1, uniform="suggestion")
            border = tk.Frame(suggestions, bg=c["border"], padx=1, pady=1)
            border.grid(row=0, column=column, sticky="ew", padx=4)
            button = self.button(border, title, lambda text=prompt: self.use_suggestion(text),
                                 c["surface"], c["ink"], "#edf0fb")
            button.pack(fill="x")
            self.suggestion_buttons.append(button)

    def use_suggestion(self, text):
        if self.is_waiting or self.is_loading_models or not self.model_name.get():
            return
        self.input_box.delete("1.0", END)
        self.input_box.insert("1.0", text)
        self.input_box.focus_set()

    def clear_messages(self):
        for child in self.message_frame.winfo_children():
            child.destroy()
        self.empty_state = None
        self.suggestion_buttons = []
        self.chat_canvas.yview_moveto(0)

    def new_chat(self, event=None):
        if self.is_waiting or self.is_loading_models:
            return "break"
        self.conversation.clear()
        self.input_box.config(state="normal")
        self.input_box.delete("1.0", END)
        self.input_box.edit_reset()
        self.clear_messages()
        self.show_empty_state()
        self.chat_subtitle.config(text=(f"Chatting with {self.model_name.get()}" if self.model_name.get()
                                       else "A little space to think, create, and explore."))
        self.set_status("Ready when you are." if self.model_name.get() else "Choose a model to begin.")
        self.update_controls()
        if self.model_name.get():
            self.input_box.focus_set()
        return "break"

    def update_controls(self):
        busy = self.is_waiting or self.is_loading_models
        ready = bool(self.model_name.get()) and not busy
        self.model_picker.config(state="disabled" if busy else "readonly")
        self.refresh_button.config(state="disabled" if busy else "normal")
        self.new_chat_button.config(state="disabled" if busy else "normal")
        self.input_box.config(state="normal" if ready else "disabled")
        self.send_button.config(
            state="normal" if ready else "disabled",
            text="Working..." if self.is_waiting else "Send  \u2192",
            bg=self.COLORS["accent"] if ready else "#e5e9f2",
        )
        self.composer_hint.config(text=("Enter to send \u00b7 Shift+Enter for a new line" if self.model_name.get()
                                       else "Choose a model to start chatting"))
        for button in getattr(self, "suggestion_buttons", []):
            button.config(state="normal" if ready else "disabled")

    def set_status(self, text, error=False):
        self.status_label.config(text=text, fg=self.COLORS["error"] if error else self.COLORS["muted"])

    def set_connection(self, text, color):
        self.connection_label.config(text=text)
        self.connection_dot.config(fg=color)

    def refresh_models(self):
        if self.is_waiting or self.is_loading_models:
            return
        self.is_loading_models = True
        self.update_controls()
        self.set_status("Finding your installed models...")
        self.set_connection("Connecting to Ollama", self.COLORS["sidebar_muted"])
        threading.Thread(target=self.fetch_models, daemon=True).start()

    def fetch_models(self):
        try:
            self.model_queue.put((list_ollama_models(), None))
        except (RuntimeError, ValueError) as error:
            self.model_queue.put(([], str(error)))

    def process_models(self):
        try:
            models, error = self.model_queue.get_nowait()
        except queue.Empty:
            return
        self.is_loading_models = False
        if error:
            self.set_status("Could not load models. Click Refresh models to retry.", error=True)
            self.set_connection("Connection issue", "#efa396")
            self.display_message("Connection issue", error, "error")
        else:
            self.model_picker.config(values=models)
            self.set_connection("Ollama connected", self.COLORS["success"])
            if self.model_name.get() not in models:
                had_model = self.active_model is not None
                self.model_name.set("")
                self.active_model = None
                self.conversation.clear()
                if had_model:
                    self.new_chat()
            if not models:
                self.set_status("No models installed. Install a model in Ollama, then refresh.")
            elif self.model_name.get():
                self.set_status("Ready when you are.")
            else:
                self.set_status("Choose a model in the sidebar to begin.")
                self.model_picker.focus_set()
            self.model_help.config(text=f"{len(models)} installed model{'s' if len(models) != 1 else ''}. Switching starts a fresh chat.")
        self.update_controls()

    def select_model(self, event=None):
        if self.is_waiting or self.is_loading_models or self.model_name.get() == self.active_model:
            return
        self.active_model = self.model_name.get()
        self.new_chat()

    def display_message(self, sender, message, role):
        if self.empty_state is not None:
            self.empty_state.destroy()
            self.empty_state = None
            self.suggestion_buttons = []
        c = self.COLORS
        is_user = role == "user"
        bg = c["user"] if is_user else c["surface"]
        card = tk.Frame(self.message_frame, bg=bg, highlightthickness=1,
                        highlightbackground="#dce2fc" if is_user else c["border"])
        card.pack(fill="x", padx=(82, 28) if is_user else (28, 52), pady=(18, 0))
        heading = tk.Frame(card, bg=bg)
        heading.pack(fill="x", padx=18, pady=(13, 6))
        title = "YOU" if is_user else ("AI ASSIST" if role == "ai" else sender.upper())
        color = c["accent"] if is_user else (c["error"] if role == "error" else c["muted"])
        tk.Label(heading, text=title, bg=bg, fg=color,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        copy_button = self.button(heading, "Copy", None, bg, c["muted"], "#e8ecf6")
        copy_button.config(padx=6, pady=2, font=("Segoe UI", 8),
                           command=lambda: self.copy_message(message, copy_button))
        copy_button.pack(side="right")
        body = tk.Label(card, text=message, bg=bg, fg=c["error"] if role == "error" else c["ink"],
                        font=("Segoe UI", 11), anchor="w", justify="left", wraplength=500)
        body.pack(fill="x", padx=18, pady=(0, 18))
        card.bind("<Configure>", lambda event: body.config(wraplength=max(100, event.width - 40)))
        self.root.after_idle(lambda: self.chat_canvas.yview_moveto(1.0))

    def copy_message(self, message, button):
        self.root.clipboard_clear()
        self.root.clipboard_append(message)
        button.config(text="Copied")
        self.root.after(1600, lambda: button.config(text="Copy") if button.winfo_exists() else None)

    def handle_enter(self, event):
        if event.state & 0x0001:
            return None
        return self.send_prompt(event)

    def send_prompt(self, event=None):
        if self.is_waiting or self.is_loading_models or not self.model_name.get():
            return "break"
        user_input = self.input_box.get("1.0", "end-1c").strip()
        if not user_input:
            return "break"
        self.display_message("You", user_input, "user")
        self.input_box.delete("1.0", END)
        self.input_box.edit_reset()
        self.is_waiting = True
        self.update_controls()
        self.set_status(f"{self.model_name.get()} is thinking...")
        self.activity.pack(side="right", padx=(12, 0))
        self.activity.start(12)
        messages = self.conversation + [{"role": "user", "content": user_input}]
        threading.Thread(target=self.fetch_response,
                         args=(messages, user_input, self.model_name.get()), daemon=True).start()
        return "break"

    def fetch_response(self, messages, user_input, model):
        try:
            response = request_ollama(messages, model)
            self.response_queue.put((user_input, response, None))
        except (RuntimeError, ValueError) as error:
            self.response_queue.put((user_input, None, str(error)))

    def process_responses(self):
        self.process_models()
        try:
            user_input, response, error = self.response_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            self.is_waiting = False
            self.activity.stop()
            self.activity.pack_forget()
            self.update_controls()
            if error:
                self.set_status("Something went wrong. You can try again.", error=True)
                self.display_message("Request failed", error, "error")
            else:
                self.set_status("Ready for your next question.")
                self.set_connection("Ollama connected", self.COLORS["success"])
                self.conversation.extend([
                    {"role": "user", "content": user_input},
                    {"role": "assistant", "content": response},
                ])
                self.display_message("AI Assistant", response, "ai")
            self.input_box.focus_set()
        if not self.closed:
            self.poll_id = self.root.after(100, self.process_responses)

    def close(self):
        self.closed = True
        self.root.after_cancel(self.poll_id)
        self.activity.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = AIAssistantGUI(root)
    root.mainloop()
