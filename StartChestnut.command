#!/bin/zsh

set -e
cd "${0:A:h}"

if [[ -f ".env" ]]; then
  set -a
  source .env
  set +a
fi

CHESTNUT_URL="http://127.0.0.1:${CHESTNUT_PORT:-8080}"

if /usr/bin/curl --silent --fail --max-time 1 "$CHESTNUT_URL/health" >/dev/null 2>&1; then
  echo "Chestnut is already running. Restarting it with the latest configuration…"
  running_pids=$(/usr/sbin/lsof -tiTCP:"${CHESTNUT_PORT:-8080}" -sTCP:LISTEN 2>/dev/null || true)
  for running_pid in ${(f)running_pids}; do
    kill "$running_pid" 2>/dev/null || true
  done

  for attempt in {1..30}; do
    if ! /usr/bin/curl --silent --fail --max-time 1 "$CHESTNUT_URL/health" >/dev/null 2>&1; then
      break
    fi
    sleep 0.1
  done

  remaining_pids=$(/usr/sbin/lsof -tiTCP:"${CHESTNUT_PORT:-8080}" -sTCP:LISTEN 2>/dev/null || true)
  for remaining_pid in ${(f)remaining_pids}; do
    echo "Chestnut did not stop in time. Force stopping process $remaining_pid…"
    kill -9 "$remaining_pid" 2>/dev/null || true
  done
fi

if [[ -z "$DASHSCOPE_API_KEY" ]]; then
  echo "Chestnut needs your Bailian API key for live translation."
  echo "The key is used only for this session and is not saved."
  read -rs "DASHSCOPE_API_KEY?Paste Bailian API key, then press Return: "
  echo
  export DASHSCOPE_API_KEY
fi

if [[ -z "$BAILIAN_API_HOST" ]]; then
  echo ""
  read "BAILIAN_API_HOST?Paste your Bailian API Host (without https://): "
  export BAILIAN_API_HOST
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Preparing Chestnut for first use…"
  /usr/bin/python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi

.venv/bin/python server.py &
server_pid=$!

cleanup() {
  if kill -0 "$server_pid" >/dev/null 2>&1; then
    kill "$server_pid"
  fi
}
trap cleanup EXIT INT TERM

for attempt in {1..60}; do
  if /usr/bin/curl --silent --fail --max-time 1 "$CHESTNUT_URL/health" >/dev/null 2>&1; then
    open "$CHESTNUT_URL"
    echo ""
    echo "ChestnutOne is running at $CHESTNUT_URL"
    echo "Keep this window open during the meeting. Close it to stop the local service."
    wait "$server_pid"
    exit $?
  fi
  sleep 0.2
done

echo "Chestnut did not become ready. Check the error messages above."
exit 1
