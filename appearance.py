"""Plain theme names and transitions, including saved legacy appearance preferences."""

DARK_THEMES = ("Dark", "Night")
# The console pair: one switch in the header flips between them.
CONSOLE_PAIR = {"Night": "Day", "Day": "Night"}
DEFAULT_THEME = "Night"


def toggled_theme(theme):
    """Night <-> Day; from any other theme, to the console theme of the opposite brightness."""
    if theme in CONSOLE_PAIR:
        return CONSOLE_PAIR[theme]
    return "Day" if theme in DARK_THEMES else "Night"
