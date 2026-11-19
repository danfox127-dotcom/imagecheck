#!/bin/sh
# Start the image usage checker and open it in the browser. Close this window to stop it.
set -eu
cd "$(dirname "$0")/.."
[ -x .venv/bin/streamlit ] || { echo "Not installed yet: run install.sh first."; exit 1; }

# First free port from 8501, so a second copy (or another Streamlit app) doesn't get in the way.
PORT="$(.venv/bin/python -c '
import socket
for port in range(8501, 8600):
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", port)) != 0:
            print(port)
            break
')"
URL="http://localhost:$PORT"

# Open the browser once the app answers.
(
    i=0
    while [ $i -lt 120 ]; do
        if curl -fs "$URL/_stcore/health" >/dev/null 2>&1; then
            if command -v open >/dev/null 2>&1; then open "$URL"; else xdg-open "$URL" >/dev/null 2>&1 || true; fi
            exit 0
        fi
        i=$((i + 1))
        sleep 1
    done
) &

printf '\nImage checker is starting at %s (it opens in your browser).\n' "$URL"
printf 'Keep this window open while you use it; close it or press Ctrl-C to stop.\n\n'
# Only reachable from this computer.
exec .venv/bin/streamlit run app.py --server.port "$PORT" --server.address 127.0.0.1 \
    --server.headless true --browser.gatherUsageStats false --client.toolbarMode viewer
