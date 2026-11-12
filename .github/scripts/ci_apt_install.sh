#!/usr/bin/env bash
# azure.archive.ubuntu.com sometimes serves at ~100 kB/s. A warm deb cache
# installs offline; a miss installs from apt and stores the debs for the next run.
set -euo pipefail

cache_dir=$1
shift
mkdir -p "$cache_dir"

shopt -s nullglob
debs=("$cache_dir"/*.deb)
if ((${#debs[@]})); then
  # dpkg -i downgrades by default; a cached deb must never replace a newer library.
  if sudo DEBIAN_FRONTEND=noninteractive dpkg --refuse-downgrade -i "${debs[@]}"; then
    exit 0
  fi
  echo "cached debs did not apply; installing from apt" >&2
fi

sudo DEBIAN_FRONTEND=noninteractive apt-get update
# --fix-broken repairs whatever a failed dpkg run left half-installed.
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --fix-broken "$@"

fetched=(/var/cache/apt/archives/*.deb)
if ((${#fetched[@]})); then
  cp -n "${fetched[@]}" "$cache_dir"/
fi
