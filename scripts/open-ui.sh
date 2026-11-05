#!/usr/bin/env bash
# Wait for SA3 Studio to answer, then show it in Chrome: focus an existing studio tab
# if one is open (it reloads itself onto the new server, keeping the unsent prompt),
# otherwise open a new one. Used by `make start`.
#   scripts/open-ui.sh <port> <url>
port=$1 url=$2
for _ in $(seq 1 300); do
  curl -s -o /dev/null "http://127.0.0.1:$port/" && break
  sleep 1
done

found=$(osascript - "127.0.0.1:$port" 2>/dev/null <<'APPLESCRIPT'
on run argv
  set needle to item 1 of argv
  tell application "Google Chrome"
    if not running then return "no"
    repeat with w in windows
      set i to 0
      repeat with t in tabs of w
        set i to i + 1
        if URL of t contains needle then
          set active tab index of w to i
          set index of w to 1
          activate
          return "yes"
        end if
      end repeat
    end repeat
  end tell
  return "no"
end run
APPLESCRIPT
)
[ "$found" = yes ] || open -a "Google Chrome" "$url"
