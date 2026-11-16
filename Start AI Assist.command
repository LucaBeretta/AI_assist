#!/bin/bash
# macOS launcher: double-click in Finder. Starts AI Assist from this folder with
# a Python 3 that includes Tkinter, and keeps errors visible.
cd "$(dirname "$0")" || exit 1

for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 \
                 /Library/Frameworks/Python.framework/Versions/Current/bin/python3 /usr/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import tkinter" >/dev/null 2>&1; then
        "$candidate" AIassist.py && exit 0
        echo
        echo "AI Assist could not start. The error is shown above."
        read -r -p "Press Return to close this window."
        exit 1
    fi
done

echo "No Python 3 with Tkinter was found."
echo "Install Python from https://www.python.org/downloads/ (it includes Tkinter),"
echo "or with Homebrew run: brew install python python-tk"
read -r -p "Press Return to close this window."
exit 1
