#!/bin/zsh
# Installed into the shared Steam library by Kaon Setup.
set -eu

readonly config="$HOME/Library/Application Support/Kaon/config.json"

if (( $# < 2 )); then
    print -u2 -- "usage: $0 <bottle> <windows-executable> [arguments ...]"
    exit 64
fi
if [[ ! -f "$config" ]]; then
    print -u2 -- "Kaon is not configured. Open Kaon Setup and choose Repair."
    exit 78
fi

if ! crossover_app="$(/usr/bin/plutil -extract crossover_app raw -o - "$config" 2>/dev/null)" \
    || [[ -z "${crossover_app//[[:space:]]/}" ]]; then
    print -u2 -- "Kaon configuration is incomplete or damaged. Open Kaon Setup and choose Repair."
    exit 78
fi
hide_tray="$(/usr/bin/plutil -extract hide_tray raw -o - "$config" 2>/dev/null || print false)"
wine="$crossover_app/Contents/SharedSupport/CrossOver/bin/wine"
bottle="$1"
shift

if [[ ! -x "$wine" ]]; then
    print -u2 -- "Kaon could not find the selected CrossOver Wine launcher: $wine"
    exit 69
fi

typeset -a command
command=("$wine" --bottle "$bottle" --no-update --wait-children)
if [[ "$hide_tray" == "true" || "$hide_tray" == "1" ]]; then
    command+=(--dll 'explorer.exe=n,b')
fi
command+=(-- "$@")
exec "${command[@]}"
