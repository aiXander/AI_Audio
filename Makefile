PORT ?= 7860
URL  := http://127.0.0.1:$(PORT)/?__theme=dark
UP   := curl -s -o /dev/null http://127.0.0.1:$(PORT)/

.PHONY: start stop arranger push pull help
.DEFAULT_GOAL := help

help: ## List targets
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-6s %s\n", $$1, $$2}'

# A running instance (background or another terminal) is stopped and replaced. Once the
# server answers, scripts/open-ui.sh focuses the open studio tab in Chrome (it reloads
# itself onto the new server) or opens a new one if there is none.
start: ## (Re)start SA3 Studio in the foreground, picking up code edits; Ctrl-C stops it
	@if $(UP) || pgrep -f "sa3lab/(devwatch|studio)\.py" >/dev/null; then \
	  echo "SA3 Studio already running on :$(PORT) — restarting it"; \
	  pkill -f "sa3lab/(devwatch|studio)\.py"; \
	  for i in $$(seq 1 30); do $(UP) || break; sleep 0.5; done; \
	  if $(UP); then echo "port $(PORT) is still in use by something else"; exit 1; fi; \
	fi; \
	scripts/open-ui.sh $(PORT) "$(URL)" & \
	exec sa3lab/studio --port $(PORT)

stop: ## Stop a SA3 Studio running in the background
	@pkill -f "sa3lab/(devwatch|studio)\.py" && echo "SA3 Studio stopped" || echo "SA3 Studio was not running"

# The arranger (sa3lab/arranger/) is its own server on :7861; restarting it never touches the studio.
arranger: ## (Re)start the SA3 Arranger (song-sketch mini-DAW) on :7861 in the foreground
	@pkill -f "sa3lab/arranger/server\.py" && sleep 0.5 || true; \
	(for i in $$(seq 1 40); do curl -s -o /dev/null http://127.0.0.1:7861/ && break; sleep 0.25; done; \
	 open http://127.0.0.1:7861/) & \
	exec sa3lab/arranger/arranger

push: ## Commit + push every submodule, then AI_Audio (make push m="message")
	@scripts/sync.sh push "$(m)"

pull: ## Pull AI_Audio and every submodule (on main), e.g. after the other machine pushed
	@scripts/sync.sh pull
