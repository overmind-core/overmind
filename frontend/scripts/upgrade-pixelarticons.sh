#!/usr/bin/env bash
# Unlock pixelarticons Pro into node_modules after install.
# https://github.com/halfmage/pixelarticons#unlock-all-4400-icons
#
# Key source, first match wins: PIXELARTICONS_LICENSE_KEY, then .env.local,
# .env.development.local, .env.production. No key → exit 0 (CI and fresh clones
# without the secret stay on the free set).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

load_key_from_file() {
  local file="$1"
  [[ -f "$file" ]] || return 1
  # shellcheck disable=SC1090
  set -a
  # Export only this var; the rest of the file is not ours to source.
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    case "$line" in
      PIXELARTICONS_LICENSE_KEY=*)
        export "$line"
        ;;
    esac
  done <"$file"
  set +a
  [[ -n "${PIXELARTICONS_LICENSE_KEY:-}" ]]
}

if [[ -z "${PIXELARTICONS_LICENSE_KEY:-}" ]]; then
  load_key_from_file "$ROOT/.env.local" || true
fi
if [[ -z "${PIXELARTICONS_LICENSE_KEY:-}" ]]; then
  load_key_from_file "$ROOT/.env.development.local" || true
fi
if [[ -z "${PIXELARTICONS_LICENSE_KEY:-}" ]]; then
  load_key_from_file "$ROOT/.env.production" || true
fi

if [[ -z "${PIXELARTICONS_LICENSE_KEY:-}" ]]; then
  echo "pixelarticons: no PIXELARTICONS_LICENSE_KEY — keeping free icon set"
  exit 0
fi

if [[ ! -d "$ROOT/node_modules/pixelarticons" ]]; then
  echo "pixelarticons: package not installed — skip Pro upgrade"
  exit 0
fi

echo "pixelarticons: unlocking Pro icon set…"
bunx pixelarticons upgrade --key="$PIXELARTICONS_LICENSE_KEY"

# Upstream names each component after its icon, so the React logo icon emits
# `import React from 'react'; export const React = …` — an illegal redeclaration
# that fails any bundler, and the barrel re-exports it. Alias the runtime import
# in that one file; the generator rewrites it on every upgrade, so this runs here.
for generated in node_modules/pixelarticons/react/React.js \
                 node_modules/pixelarticons/react/React.d.ts; do
  [[ -f "$generated" ]] || continue
  sed -e "s/^import React from 'react';/import ReactRuntime from 'react';/" \
      -e 's/React\./ReactRuntime./g' \
      "$generated" >"$generated.tmp" && mv "$generated.tmp" "$generated"
done
