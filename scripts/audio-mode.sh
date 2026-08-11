#!/bin/bash
set -e
STATE_DIR=/run/audio-hub
STATE_FILE="$STATE_DIR/mode"
mkdir -p "$STATE_DIR"

case "$1" in
  spotify)
    systemctl stop scsynth jackd 2>/dev/null || true
    systemctl start raspotify
    echo spotify > "$STATE_FILE"
    ;;
  supercollider)
    systemctl stop raspotify 2>/dev/null || true
    systemctl start jackd
    systemctl start scsynth
    echo supercollider > "$STATE_FILE"
    ;;
  mirror)
    systemctl stop raspotify scsynth jackd 2>/dev/null || true
    echo mirror > "$STATE_FILE"
    ;;
  off)
    systemctl stop raspotify scsynth jackd 2>/dev/null || true
    echo off > "$STATE_FILE"
    ;;
  status)
    if systemctl is-active --quiet raspotify; then echo spotify;
    elif systemctl is-active --quiet scsynth; then echo supercollider;
    elif [ -f "$STATE_FILE" ]; then cat "$STATE_FILE";
    else echo off; fi
    ;;
  *)
    echo "Usage: $0 {spotify|supercollider|mirror|off|status}" >&2
    exit 1
    ;;
esac
