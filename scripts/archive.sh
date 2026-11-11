#!/usr/bin/env bash
# The box's permanent copy of outputs/ (the RTX 3090 box's 12 TB drive, ~/12TBHDD/AI_Audio/outputs/).
#   scripts/archive.sh push [--quiet]   mirror outputs/ to the box: rsync -a, NEVER deletes or moves
#                                       anything there (make archive; the studio runs it every 5 min)
#   scripts/archive.sh check            list every Mac file whose bytes are NOT on the box yet
#                                       (empty list = safe to delete locally; make archive-check)
# Sidecars change after a render (labels, stars, played): push just copies the newer file.
# Excluded: regenerable caches only (.embeddings/, _dadagrid/) and half-written temp files.
# BEAST_SSH=rednax-beast (ssh alias), or "local" when running on the box itself.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
SRC="${SA3_OUTPUTS:-$ROOT/outputs}/"
BEAST_SSH="${BEAST_SSH:-rednax-beast}"
MOUNT="12TBHDD"                               # relative to the box's home; mounted `nofail`
DEST="${SA3_ARCHIVE_DEST:-$MOUNT/AI_Audio/outputs/}"   # override only for tests
SSH_OPTS=(-o ConnectTimeout=5 -o BatchMode=yes)
EXCL=(--exclude .embeddings/ --exclude _dadagrid/ --exclude '*.part' --exclude '*.tmp' --exclude '.DS_Store')
QUIET=; [ "${2:-}" = "--quiet" ] && QUIET=1

on_box() { if [ "$BEAST_SSH" = local ]; then (cd "$HOME" && bash -c "$1"); else ssh "${SSH_OPTS[@]}" "$BEAST_SSH" "$1"; fi; }
tgt() { if [ "$BEAST_SSH" = local ]; then echo "$HOME/$DEST"; else echo "$BEAST_SSH:$DEST"; fi; }
sync() { if [ "$BEAST_SSH" = local ]; then rsync "$@"; else rsync -e "ssh ${SSH_OPTS[*]}" "$@"; fi; }

# an unmounted nofail mount point is an empty dir on /home: never fill that instead of the 12 TB drive
on_box "mountpoint -q ~/$MOUNT && mkdir -p ~/$DEST" \
  || { echo "archive: the box's ~/$MOUNT isn't reachable or mounted; nothing copied" >&2; exit 2; }

case "${1:-}" in
  push)
    sync -a --timeout=60 "${EXCL[@]}" "$SRC" "$(tgt)" || { echo "archive: rsync failed" >&2; exit 1; }
    # sidecars again, by content: a label edit can keep the size and land in the same second as the
    # last pass, which rsync's size + mtime check skips (sidecars are small, so --checksum is cheap here)
    sync -a -c --timeout=60 "${EXCL[@]}" --include '*/' --include '*.json' --exclude '*' "$SRC" "$(tgt)" \
      || { echo "archive: sidecar rsync failed" >&2; exit 1; }
    [ -n "$QUIET" ] || echo "archive: outputs/ mirrored to $(tgt)"
    ;;
  check)
    # dry run of a push that compares contents (--checksum): whatever it would send is not safely
    # on the box. Directories and symlinks are skipped (outputs/starred/ holds links; their targets count).
    if rsync --version 2>/dev/null | head -1 | grep -q "version 3"; then
      out="$(sync -a -n -c --itemize-changes "${EXCL[@]}" "$SRC" "$(tgt)" | awk '/^[<>]f/ {sub(/^[^ ]+ /, ""); print}')"
    else          # macOS' bundled rsync / openrsync: no itemize; -v lists the files, dirs end in /
      out="$(sync -a -n -c -v "${EXCL[@]}" "$SRC" "$(tgt)" \
             | grep -vE '/$|^(building|sending|sent|total|Transfer starting)|^$| -> ')"
    fi
    PY="$ROOT/stable-audio-3/optimized/mlx/.venv/bin/python"; [ -x "$PY" ] || PY=python3
    # early renders whose sidecar names audio OUTSIDE outputs/ (the mirror doesn't cover those)
    outside="$("$PY" - "$SRC" <<'EOF'
import json, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
for sc in root.rglob("*.json"):
    a = sc.with_suffix("")
    if a.suffix.lower() not in (".wav", ".mp3") or a.exists():
        continue
    try:
        p = Path(json.loads(sc.read_text()).get("path") or "")
    except Exception:
        continue
    if p.is_file() and root not in p.resolve().parents:
        print(f"{sc.relative_to(root)} → {p}")
EOF
)"
    if [ -n "$out" ]; then
      echo "Not on the box yet ($(echo "$out" | wc -l | tr -d ' ') files; run make archive):"
      echo "$out" | sed 's/^/  /'
    fi
    if [ -n "$outside" ]; then
      echo "Sidecars whose audio lives outside outputs/ (not mirrored; move the audio in before deleting it):"
      echo "$outside" | sed 's/^/  /'
    fi
    if [ -z "$out" ] && [ -z "$outside" ]; then echo "archive: every file in outputs/ is on the box"; fi
    [ -z "$out" ]
    ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
