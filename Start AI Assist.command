#!/bin/bash
# macOS launcher. Prefer managed Python installations over Apple's older Tk.
cd "$(dirname "$0")" || exit 1

candidates=()
if [ -n "${AI_ASSIST_PYTHON:-}" ]; then
    candidates+=("$AI_ASSIST_PYTHON")
fi
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
    echo "Checking Python runtime: $candidate"
    if "$candidate" gui_runtime.py --probe; then
        "$candidate" AIassist.py && exit 0
        echo
        echo "AI Assist could not start. The error is shown above."
        read -r -p "Press Return to close this window."
        exit 1
    fi
    echo
done

echo "No compatible Python 3 with a working Tk GUI was found."
echo "Install a current Python from https://www.python.org/downloads/ with Tcl/Tk support."
echo "Alternatively, with Homebrew run: brew install python python-tk"
echo "Tk 8.6 or newer is required on macOS; the runtime errors above identify rejected installations."
read -r -p "Press Return to close this window."
exit 1
