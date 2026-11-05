#!/usr/bin/env bash
# One-shot git sync for AI_Audio and its submodules (stable-audio-3, underfit).
#
#   scripts/sync.sh push ["message"]   (= make push m="message")
#     For each submodule, then the parent: stage everything, commit, push.
#     Submodules go first so the parent never points at an unpushed commit.
#   scripts/sync.sh pull               (= make pull)
#     Pull the parent, then put every submodule on main and pull it.
#
# Guards: every CLAUDE.md / AGENTS.md is force-added even if a .gitignore
# excludes it (underfit's does); audio, weights and files over 5 MB are refused,
# because every repo here is public.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT=$(pwd)

submodules() { git config -f .gitmodules --get-regexp '\.path$' | awk '{print $2}'; }

add_agent_docs() {
  find . -mindepth 1 \
    \( -name .git -o -name .venv -o -name node_modules -o -name models -o -name outputs -o -name audio-data \) -prune -o \
    -type d -exec test -e '{}/.git' \; -prune -o \
    \( -name CLAUDE.md -o -name AGENTS.md \) -print |
    while IFS= read -r f; do git add -f -- "$f"; done
}

refuse_big_or_binary() {
  local bad=0 f
  while IFS= read -r -d '' f; do
    [[ -f $f && ! -L $f ]] || continue
    if [[ $f =~ \.(wav|mp3|flac|ogg|m4a|aif|aiff|safetensors|ckpt|pt|pth|bin|npz|npy|gguf|onnx)$ ]] ||
       (( $(wc -c <"$f") > 5000000 )); then
      echo "  ✋ $f (audio / weights / >5 MB)"; bad=1
    fi
  done < <(git diff --cached --name-only --diff-filter=AM -z)
  if (( bad )); then
    git reset -q
    echo "  Refusing to commit in $(pwd): gitignore or move those files (nothing was committed here)."
    exit 1
  fi
}

commit_and_push() {
  local msg=$1
  git add -A
  add_agent_docs
  if git diff --cached --quiet; then
    echo "  nothing to commit"
  else
    refuse_big_or_binary
    git commit -q -m "$msg"
    echo "  committed: $(git log -1 --format='%h %s')"
  fi
  if [[ -n $(git log '@{u}..HEAD' --oneline 2>/dev/null) ]]; then
    git push -q || { echo "  push rejected (the other machine pushed first?): run 'make pull', then 'make push' again"; exit 1; }
    echo "  pushed → $(git rev-parse --abbrev-ref '@{u}')"
  else
    echo "  up to date with $(git rev-parse --abbrev-ref '@{u}' 2>/dev/null || echo 'remote')"
  fi
}

push() {
  local msg=${1:-updates}
  for sm in $(submodules); do
    echo "▸ $sm"
    (
      cd "$sm"
      if [[ -z $(git branch --show-current) ]]; then
        echo "  detached HEAD: run 'cd $sm && git checkout main && git pull' first"; exit 1
      fi
      commit_and_push "$msg"
    )
  done
  echo "▸ AI_Audio"
  commit_and_push "$msg"
}

pull() {
  echo "▸ AI_Audio"
  git pull -q --rebase --autostash
  git submodule update -q --init   # clones any newly added submodule
  for sm in $(submodules); do
    echo "▸ $sm"
    ( cd "$sm" && git checkout -q main && git pull -q --rebase --autostash && echo "  at $(git log -1 --format='%h %s')" )
  done
}

case ${1:-} in
  push) shift; push "${1:-}" ;;
  pull) pull ;;
  *) echo "usage: $0 push [message] | pull"; exit 2 ;;
esac
