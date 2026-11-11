#!/usr/bin/env bash
# The SA3 render worker on the RTX 3090 box (sa3lab/beast/worker.py), driven from the Mac.
#   scripts/beast.sh deploy [--quiet]  rsync the render-path code to the box's deploy copy, restart the
#                                      worker only if its code hash changed (make start runs this)
#   scripts/beast.sh status            the worker's /health
#   scripts/beast.sh logs              last lines of the worker log
#   scripts/beast.sh restart           restart the worker
#   scripts/beast.sh install           ON THE BOX: install + start the systemd --user unit (once)
# BEAST_SSH=rednax-beast (ssh alias), or "local" when running on the box itself.
# The deploy copy has the repo's layout (~/SSD2TB/sa3-worker/AI_Audio/), so every module's
# Path(__file__).parents[…] resolves there. Never blocks the studio: short ssh timeouts, and
# every failure is one line on stderr.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
BEAST_SSH="${BEAST_SSH:-rednax-beast}"
BEAST_URL="${SA3_BEAST_URL:-http://100.117.34.17:7870}"
WORK="SSD2TB/sa3-worker"                      # relative to the box's home
DEPLOY="$WORK/AI_Audio"
SA_REPO_LOCAL="${SA_REPO:-$HOME/Documents/GitHub/semantic-anarchy}"
SSH_OPTS=(-o ConnectTimeout=3 -o BatchMode=yes)
QUIET=; [ "${2:-}" = "--quiet" ] && QUIET=1
say() { [ -n "$QUIET" ] || echo "$@"; }
die() { echo "beast: $*" >&2; exit 1; }

on_box() {      # run a shell command on the box
  if [ "$BEAST_SSH" = local ]; then (cd "$HOME" && bash -c "$1"); else ssh "${SSH_OPTS[@]}" "$BEAST_SSH" "$1"; fi
}
dest() {        # rsync destination for a path under the box's home
  if [ "$BEAST_SSH" = local ]; then echo "$HOME/$1"; else echo "$BEAST_SSH:$1"; fi
}
sync() {        # rsync [flags…] src dst, with ssh options when remote
  if [ "$BEAST_SSH" = local ]; then rsync "$@"; else rsync -e "ssh ${SSH_OPTS[*]}" "$@"; fi
}
health() { curl -s -m 3 "$BEAST_URL/health"; }
hash_of() { python3 -c 'import json,sys; print(json.load(sys.stdin).get("code_hash",""))' 2>/dev/null; }

# code dirs: exactly the file set sa3lab/beast/codehash.py hashes (keep EXCLUDE in step)
CODE_EXCL=(--exclude .venv/ --exclude __pycache__/ --exclude output/ --exclude node_modules/ --exclude .git/
           --exclude 'models/mlx/' --exclude loras/ --exclude '*.wav' --exclude '*.mp3' --exclude '*.pyc' --exclude .DS_Store)

deploy() {
  on_box "mkdir -p $DEPLOY/sa3lab $DEPLOY/stable-audio-3/optimized/mlx $DEPLOY/models/derived $WORK/semantic-anarchy" \
    || die "box unreachable ($BEAST_SSH): skipped deploy, the studio renders on the Mac"
  sync -a --delete --timeout=20 "${CODE_EXCL[@]}" "$ROOT/sa3lab/" "$(dest "$DEPLOY/sa3lab/")" || die "rsync sa3lab failed"
  sync -a --delete --timeout=20 "${CODE_EXCL[@]}" "$ROOT/stable-audio-3/optimized/mlx/" \
    "$(dest "$DEPLOY/stable-audio-3/optimized/mlx/")" || die "rsync the MLX runtime failed"
  if [ -d "$ROOT/models/derived/anarchy" ]; then      # anarchy fits (~44 MB), read by anarchy/sampler.py
    sync -a --timeout=60 "$ROOT/models/derived/anarchy" "$(dest "$DEPLOY/models/derived/")" || echo "beast: anarchy fits not synced" >&2
  fi
  if [ -d "$SA_REPO_LOCAL/semantic_anarchy" ]; then    # SA_REPO in the unit points here
    sync -a --delete --timeout=20 --exclude __pycache__/ "$SA_REPO_LOCAL/semantic_anarchy" "$(dest "$WORK/semantic-anarchy/")" \
      || echo "beast: semantic-anarchy not synced" >&2
  fi
  local want have
  want="$(python3 "$ROOT/sa3lab/beast/codehash.py" "$ROOT")"
  have="$(health | hash_of)"
  if [ "$want" = "$have" ]; then say "beast: worker runs this code ($want)"; return 0; fi
  on_box "systemctl --user restart sa3-worker" \
    && say "beast: deployed $want, worker restarting (was ${have:-down})" \
    || echo "beast: deployed $want, but couldn't restart the worker (run: scripts/beast.sh install on the box)" >&2
}

install_unit() {
  [ "$(hostname)" = rednax-beast ] || [ -n "${FORCE:-}" ] || die "install runs ON the box (FORCE=1 to override)"
  mkdir -p "$HOME/.config/systemd/user"
  cp "$ROOT/sa3lab/beast/sa3-worker.service" "$HOME/.config/systemd/user/sa3-worker.service"
  systemctl --user daemon-reload
  systemctl --user enable --now sa3-worker
  systemctl --user --no-pager status sa3-worker | head -5
}

case "${1:-}" in
  deploy) deploy ;;
  status) health | python3 -m json.tool 2>/dev/null || die "worker not answering at $BEAST_URL" ;;
  logs) on_box "tail -n ${N:-40} $WORK/worker.log" ;;
  restart) on_box "systemctl --user restart sa3-worker" && echo "beast: worker restarting" ;;
  install) install_unit ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
