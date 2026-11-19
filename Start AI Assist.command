#!/bin/bash
# macOS launcher. Use an installed Python whose Tk can open the window; if none
# works, offer to download a private, checksum-verified Python into ./runtime.
# The private copy needs no administrator password and changes nothing else.
cd "$(dirname "$0")" || exit 1

# An allowlisted alternate entrypoint lets the dashboard run by itself.
APP_SCRIPT="${AI_ASSIST_ENTRYPOINT:-AIassist.py}"
case "$APP_SCRIPT" in
    AIassist.py|launch_compute_dashboard.py) ;;
    *) echo "Unknown AI Assist entrypoint: $APP_SCRIPT"; exit 1 ;;
esac

RUNTIME_DIR="$PWD/runtime"
PRIVATE_PYTHON="$RUNTIME_DIR/python/bin/python3"
MANIFEST="$PWD/python-runtime.txt"

close_prompt() {
    read -r -p "Press Return to close this window."
}

# Probe a candidate; when its GUI runtime works, run the app and never return.
try_python() {
    echo "Checking Python runtime: $1"
    if "$1" gui_runtime.py --probe; then
        "$1" "$APP_SCRIPT" && exit 0
        echo
        echo "AI Assist could not start. The error is shown above."
        close_prompt
        exit 1
    fi
    echo
}

install_private_python() {
    local platform base file sha archive actual answer staging
    if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then
        platform=aarch64-apple-darwin
    else
        platform=x86_64-apple-darwin
    fi
    # A copy made on Windows has CRLF line endings; drop the CR before reading fields.
    base=$(awk '{ sub(/\r$/, "") } $1 == "base" { print $2 }' "$MANIFEST" 2>/dev/null)
    file=$(awk -v p="$platform" '{ sub(/\r$/, "") } $1 == p { print $2 }' "$MANIFEST" 2>/dev/null)
    sha=$(awk -v p="$platform" '{ sub(/\r$/, "") } $1 == p { print $3 }' "$MANIFEST" 2>/dev/null)
    if [ -z "$base" ] || [ -z "$file" ] || [ "${#sha}" -ne 64 ]; then
        echo "python-runtime.txt has no valid entry for $platform."
        return 1
    fi

    echo "AI Assist can set up its own private Python with a modern Tk (about 25 MB, downloaded from github.com)."
    echo "It is stored only in: $RUNTIME_DIR"
    echo "No administrator password is needed and other Python installations are not changed."
    echo "This goes on the internet, so it needs two confirmations."
    read -r -p "Download it now? [y/N] " answer
    case "$answer" in [Yy]*) ;; *) return 1 ;; esac
    read -r -p "Second check: type yes to download $file: " answer
    if [ "$answer" != "yes" ]; then
        return 1
    fi

    staging="$RUNTIME_DIR/.staging"
    rm -rf "$staging"
    mkdir -p "$staging" || return 1
    archive="$staging/$file"
    echo "Downloading $file"
    # "+" must be escaped in the release URL; --proto keeps redirects on HTTPS.
    if ! curl -fL --proto '=https' --progress-bar -o "$archive" "$base/${file//+/%2B}"; then
        echo "The download failed. Check the internet connection and try again."
        rm -rf "$staging"
        return 1
    fi
    actual=$(shasum -a 256 "$archive" | awk '{ print $1 }')
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
    echo "Private Python installed."
    echo
}

candidates=()
if [ -n "${AI_ASSIST_PYTHON:-}" ]; then
    candidates+=("$AI_ASSIST_PYTHON")
fi
# A previously installed private runtime is known to work, so prefer it.
[ -x "$PRIVATE_PYTHON" ] && candidates+=("$PRIVATE_PYTHON")
candidates+=(
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3
    /opt/homebrew/bin/python3
    /usr/local/bin/python3
)
for framework in /Library/Frameworks/Python.framework/Versions/*/bin/python3; do
    [ -x "$framework" ] && candidates+=("$framework")
done
candidates+=(python3)

for candidate in "${candidates[@]}"; do
    if ! command -v "$candidate" >/dev/null 2>&1; then
        if [ -n "${AI_ASSIST_PYTHON:-}" ] && [ "$candidate" = "$AI_ASSIST_PYTHON" ]; then
            echo "The AI_ASSIST_PYTHON interpreter was not found: $candidate"
        fi
        continue
    fi
    try_python "$candidate"
done

echo "No installed Python 3 with a working Tk 8.6+ GUI was found."
if install_private_python; then
    try_python "$PRIVATE_PYTHON"
fi

echo "AI Assist could not find or set up a compatible Python."
echo "Run this launcher again to retry the private setup, or install Python from"
echo "https://www.python.org/downloads/ (or with Homebrew: brew install python-tk@3.13)."
close_prompt
exit 1
