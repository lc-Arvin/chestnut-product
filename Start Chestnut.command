#!/bin/zsh

set -e
cd "${0:A:h}"

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

open "http://127.0.0.1:8080"
exec .venv/bin/python server.py
