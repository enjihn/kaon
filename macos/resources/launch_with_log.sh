#!/bin/zsh
# Installed into the shared Steam library by Kaon Setup.
set -u

readonly self="${0:A:h}"
readonly log_dir="$HOME/Library/Logs/Kaon"
readonly log="$log_dir/game-launch.log"
/bin/mkdir -p "$log_dir"
/bin/chmod 700 "$log_dir" 2>/dev/null || true

# Keep a small diagnostic log without copying the user's full environment.
if [[ -f "$log" ]] && (( $(/usr/bin/stat -f %z "$log" 2>/dev/null || print 0) > 1048576 )); then
    /bin/mv -f "$log" "$log.1"
fi

{
    print -r -- "-- $(/bin/date '+%Y-%m-%d %H:%M:%S') ----------------"
    print -r -- "launcher: $0"
    print -r -- "argument count: $#"
    "$self/launch_crossover.sh" "$@"
    status=$?
    print -r -- "exit status: $status"
    exit "$status"
} >> "$log" 2>&1
