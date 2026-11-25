#!/usr/bin/env bash
# GitHub 최신 코드를 받아 서버를 다시 켠다:  bash ~/loldov/Backend/deploy/oracle/update.sh
set -euo pipefail
cd /home/ubuntu/loldov
for r in AI Backend Frontend; do git -C "$r" pull -q --ff-only; git -C "$r" log -1 --format="$r %h %s"; done
.venv/bin/pip install -q -r Backend/deploy/oracle/requirements.txt
sudo systemctl restart loldov
sleep 3 && curl -s localhost:8000/api/config && echo
