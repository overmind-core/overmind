#!/usr/bin/env bash
# GitHub runners fail DNS for azure.archive.ubuntu.com. A warm deb cache
# installs offline; a miss retries apt, then stores the debs for the next run.
set -euo pipefail

cache_dir=$1
shift
mkdir -p "$cache_dir"

shopt -s nullglob
debs=("$cache_dir"/*.deb)
if ((${#debs[@]})); then
  if sudo DEBIAN_FRONTEND=noninteractive dpkg -i "${debs[@]}"; then
    exit 0
  fi
  echo "cached debs did not apply; installing from apt" >&2
fi

run_install() {
  if [[ ${1:-} == -- ]]; then
    shift
    "$@"
    return
  fi
  sudo DEBIAN_FRONTEND=noninteractive apt-get update &&
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
}

for attempt in 1 2 3; do
  if run_install "$@"; then
    cp -n /var/cache/apt/archives/*.deb "$cache_dir"/ 2>/dev/null || true
    exit 0
  fi
  if [[ $attempt -eq 3 ]]; then
    exit 1
  fi
  sleep $((attempt * 15))
done
