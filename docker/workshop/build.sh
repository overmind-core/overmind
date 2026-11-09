#!/bin/sh
set -eu
useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin workshop
useradd --uid 10002 --no-create-home --shell /usr/sbin/nologin replay
python -m venv /opt/overmind-runtime/venv
/opt/overmind-runtime/venv/bin/pip install --no-cache-dir -r /opt/overmind-runtime/requirements.txt
site=$(/opt/overmind-runtime/venv/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))')
printf '%s\n' /opt/overmind-runtime/lib > "$site/overmind-runtime.pth"
TIKTOKEN_CACHE_DIR=/opt/overmind-runtime/tokenizers /opt/overmind-runtime/venv/bin/python -c "import tiktoken; [tiktoken.get_encoding(n) for n in ('cl100k_base', 'o200k_base')]"
chmod -R a+rX,go-w /opt/overmind-runtime
