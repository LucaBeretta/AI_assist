#!/bin/bash
# Standalone, local-only monitor. No AI Assist chat window or sharing server.
cd "$(dirname "$0")" || exit 1
export AI_ASSIST_ENTRYPOINT=launch_compute_dashboard.py
exec /bin/bash "./Start AI Assist.command"
