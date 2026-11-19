#!/bin/bash
# Reuse the existing macOS interpreter discovery and verified private setup.
export AI_ASSIST_WEB_UI=1
exec /bin/bash "$(dirname "$0")/Start AI Assist.command"
