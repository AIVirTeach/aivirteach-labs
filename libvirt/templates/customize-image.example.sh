#!/usr/bin/env bash
set -euo pipefail

# Runs as root inside the golden-image builder after XFCE, XRDP, Python,
# Git, SSH, and the QEMU guest agent are installed.
install -d -m 0755 /opt/aivirteach/guest-agent
python3 -m venv /opt/aivirteach/venv
/opt/aivirteach/venv/bin/pip install --upgrade pip

# Examples:
# /opt/aivirteach/venv/bin/pip install fastapi uvicorn
# install -m 0755 ./guest-agent.py /opt/aivirteach/guest-agent/guest-agent.py

# Never embed production secrets or learner credentials in the image.
