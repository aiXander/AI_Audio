PORT ?= 7860
URL  := http://127.0.0.1:$(PORT)/?__theme=dark
UP   := curl -s -o /dev/null http://127.0.0.1:$(PORT)/

.PHONY: start stop help
.DEFAULT_GOAL := help

help: ## List targets
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-6s %s\n", $$1, $$2}'

start: ## Launch SA3 Studio (sa3lab/studio) and open it in Chrome; Ctrl-C stops it
	@if $(UP); then \
	  echo "SA3 Studio is already running on :$(PORT) — opening Chrome"; \
	  open -a "Google Chrome" "$(URL)"; \
	else \
	  ( for i in $$(seq 1 300); do $(UP) && { open -a "Google Chrome" "$(URL)"; exit 0; }; sleep 1; done ) & \
	  exec sa3lab/studio --port $(PORT); \
	fi

stop: ## Stop a SA3 Studio running in the background
	@pkill -f "sa3lab/studio.py" && echo "SA3 Studio stopped" || echo "SA3 Studio was not running"
