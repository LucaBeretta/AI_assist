#!/usr/bin/env bash
# Linux launcher. Use an installed Python with pywebview; if none
# works, offer to download a private, checksum-verified Python into
# ./runtime/linux. The private copy needs no administrator password and
# changes nothing else. Run it from a terminal: bash "Start AI Assist.sh"
cd "$(dirname "$0")" || exit 1

case "$(uname -m)" in
    x86_64|amd64) PLATFORM=x86_64-unknown-linux-gnu ;;
    aarch64|arm64) PLATFORM=aarch64-unknown-linux-gnu ;;
    *) PLATFORM= ;;
esac
# Separate from the Windows and macOS runtime, so one folder can serve every system.
RUNTIME_DIR="$PWD/runtime/linux"
PRIVATE_PYTHON="$RUNTIME_DIR/python/bin/python3"
MANIFEST="$PWD/python-runtime.txt"

close_prompt() {
    if [ -t 0 ]; then
        read -r -p "Press Enter to close." _
    fi
}

# Probe a candidate; when its GUI runtime works, run the app and never return.
try_python() {
    echo "Checking Python runtime: $1"
    if "$1" gui_runtime.py --probe; then
        "$1" gui_runtime.py --ask-shortcut
        "$1" AIassist.py && exit 0
        echo
        echo "AI Assist could not start. The error is shown above."
        close_prompt
        exit 1
    fi
    echo
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{ print $1 }'
    else
        shasum -a 256 "$1" | awk '{ print $1 }'
    fi
}

install_private_python() {
    local base file sha url archive actual answer staging
    if [ -z "$PLATFORM" ]; then
        echo "A private Python is not available for this processor ($(uname -m))."
        return 1
    fi
    # A copy made on Windows has CRLF line endings; drop the CR before reading fields.
    base=$(awk '{ sub(/\r$/, "") } $1 == "base" { print $2 }' "$MANIFEST" 2>/dev/null)
    file=$(awk -v p="$PLATFORM" '{ sub(/\r$/, "") } $1 == p { print $2 }' "$MANIFEST" 2>/dev/null)
    sha=$(awk -v p="$PLATFORM" '{ sub(/\r$/, "") } $1 == p { print $3 }' "$MANIFEST" 2>/dev/null)
    if [ -z "$base" ] || [ -z "$file" ] || [ "${#sha}" -ne 64 ]; then
        echo "python-runtime.txt has no valid entry for $PLATFORM."
        return 1
    fi

    echo "AI Assist can set up its own private Python with pywebview (about 30 MB, downloaded from github.com)."
    echo "It is stored only in: $RUNTIME_DIR"
    echo "No administrator password is needed and other Python installations are not changed."
    echo "This downloads Python from GitHub and web dependencies from PyPI, so it needs two confirmations."
    read -r -p "Download it now? [y/N] " answer
    case "$answer" in [Yy]*) ;; *) echo "Download cancelled. Nothing was downloaded."; return 1 ;; esac
    # The second check needs the whole word; a short "y" asks again instead of cancelling silently.
    while :; do
        read -r -p "Second check: type yes (the whole word) to download $file, or press Return to cancel: " answer
        answer=$(printf '%s' "$answer" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
        [ "$answer" = "yes" ] && break
        if [ -z "$answer" ] || [ "$answer" = "n" ] || [ "$answer" = "no" ]; then
            echo "Download cancelled. Nothing was downloaded."
            return 1
        fi
        echo "Please type the whole word yes to confirm (you typed \"$answer\")."
    done

    staging="$RUNTIME_DIR/.staging"
    rm -rf "$staging"
    mkdir -p "$staging" || return 1
    archive="$staging/$file"
    # "+" must be escaped in the release URL; only HTTPS is allowed, also on redirects.
    url="$base/${file//+/%2B}"
    echo "Downloading $file"
    if command -v curl >/dev/null 2>&1; then
        curl -fL --proto '=https' --progress-bar -o "$archive" "$url"
    else
        wget -q --show-progress --https-only -O "$archive" "$url"
    fi
    if [ $? -ne 0 ]; then
        echo "The download failed. Check the internet connection and try again."
        rm -rf "$staging"
        return 1
    fi
    actual=$(sha256_of "$archive")
    if [ "$actual" != "$sha" ]; then
        echo "The download did not match its expected SHA-256 checksum and was discarded."
        rm -rf "$staging"
        return 1
    fi
    if ! tar -xzf "$archive" -C "$staging" || [ ! -x "$staging/python/bin/python3" ]; then
        echo "The downloaded Python could not be unpacked."
        rm -rf "$staging"
        return 1
    fi
    rm -rf "$RUNTIME_DIR/python"
    mv "$staging/python" "$RUNTIME_DIR/python" && rm -rf "$staging"
    if ! "$PRIVATE_PYTHON" -m ensurepip --upgrade || ! "$PRIVATE_PYTHON" -m pip install -r "$PWD/requirements.txt" 'pywebview[qt]==6.2.1'; then
        echo "Could not install the required web UI dependencies."
        return 1
    fi
    echo "Private Python installed."
    echo
}

candidates=()
if [ -n "${AI_ASSIST_PYTHON:-}" ]; then
    candidates+=("$AI_ASSIST_PYTHON")
fi
# A previously installed private runtime is known to work, so prefer it.
[ -x "$PRIVATE_PYTHON" ] && candidates+=("$PRIVATE_PYTHON")
candidates+=(python3 python3.14 python3.13 python3.12 python3.11 python3.10 /usr/local/bin/python3 /usr/bin/python3)

checked=" "
for candidate in "${candidates[@]}"; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
        if [ -n "${AI_ASSIST_PYTHON:-}" ] && [ "$candidate" = "$AI_ASSIST_PYTHON" ]; then
            echo "The AI_ASSIST_PYTHON interpreter was not found: $candidate"
        fi
        continue
    fi
    resolved=$(readlink -f "$(command -v "$candidate")")
    case "$checked" in *" $resolved "*) continue ;; esac
    checked="$checked$resolved "
    try_python "$candidate"
done

echo "No installed Python 3 with pywebview was found."
if install_private_python; then
    try_python "$PRIVATE_PYTHON"
fi

echo "AI Assist could not find or set up a compatible Python."
echo "Run this launcher again to retry the private setup, or install requirements.txt and pywebview's Qt backend:"
echo "  python3 -m pip install -r requirements.txt 'pywebview[qt]==6.2.1'"
echo "A graphical desktop and the Qt platform's system libraries are required. Linux has not yet been tested on real hardware."
close_prompt
exit 1
