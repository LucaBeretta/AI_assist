"""Errors in plain words: what happened, what to do, and the one action that fixes it.

``explain(error)`` recognises the common problems from their error text. The
original text is always kept and shown as "Details"; an unknown error gets no
explanation rather than a wrong guess.

Actions (each interface decides how to offer them):
    "refresh"      check the engine again (starts a local Ollama when needed)
    "get_started"  open the Get started guide
    "find_model"   open the models suggested for this computer
    "documents"    choose the chat's document folder again
"""

import re

_RULES = (
    (r"ollama is not installed",
     "Ollama is not installed",
     "AI Assist needs the free Ollama program to run models on this computer. Get started shows how to install it.",
     "get_started"),
    (r"could not (connect to|load models from) .*ollama|could not connect to ollama|ollama did not become ready|"
     r"ollama request failed|the ollama connection failed|response connection closed before ollama",
     "Ollama is not answering",
     "AI Assist talks to Ollama on this computer. Start it (AI Assist can do this for you), then send your message again.",
     "refresh"),
    (r"remote ollama server is offline",
     "The Ollama server on another computer is not answering",
     "Start Ollama on that computer, or change the server address, then try again.",
     "refresh"),
    (r"could not reach colibri|colibri disconnected|colibri returned an invalid or interrupted",
     "Colibri is not answering",
     "Start Colibri, or check its address in Settings > AI backend, then try again.",
     "refresh"),
    (r"(requires|needs) more (system )?memory|not enough memory|out of memory|insufficient memory|cuda error|"
     r"failed to allocate|unable to allocate",
     "The model needs more memory than is free",
     "Choose a smaller model, or close other programs, then try again.",
     "find_model"),
    (r"model ['\"]?[^ ]*['\"]? not found|http 404|no longer served|is not loaded\. try a smaller model",
     "This model is not available",
     "It may not be installed, or it stopped. Choose an installed model in the sidebar, or find one for this computer.",
     "find_model"),
    (r"does not advertise tool calling|requires a model with native tool calling|does not support tool",
     "This model cannot use tools",
     "The developer agent needs a model that can use tools, for example qwen3-coder. Choose or install one.",
     "find_model"),
    (r"not approved, so it was blocked|internet access was blocked|firewall lock is on",
     "Blocked: going online was not approved",
     "Nothing left this computer. If you expected this, try again and approve the request twice.",
     None),
    (r"timed out|time limit",
     "The model took too long",
     "Large models can be slow on this computer. Try a smaller model or a shorter request.",
     "find_model"),
    (r"document folder could not be used",
     "The chat's document folder could not be read",
     "The folder may have moved or been renamed. Choose it again.",
     "documents"),
)
_COMPILED = tuple((re.compile(pattern, re.I | re.S), title, text, action) for pattern, title, text, action in _RULES)


def explain(error):
    """{"title", "help", "action"} for a known problem, else None."""
    text = str(error or "")
    for pattern, title, help_text, action in _COMPILED:
        if pattern.search(text):
            return {"title": title, "help": help_text, "action": action}
    return None


ACTION_LABELS = {"refresh": "Check again", "get_started": "Get started...", "find_model": "Find a model...",
                 "documents": "Choose the folder..."}
