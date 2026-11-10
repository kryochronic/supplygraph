#####################################################################
# SupplyGraph build system
#
# Same shape as my other projects: compose files per feature, `?=` variables you
# can override on the command line, `make help` as the default goal.
#
#   make mcp-up              exchange MCP + Keycloak on docker compose
#   make k8s-up              the same stack on a local k3d cluster (deploy/Makefile)
#####################################################################

# --- Variables -------------------------------------------------------
# All overridable: make mcp-up COMPOSE_MCP=other.yml, make mcp-scale N=5, ...
COMPOSE_MCP ?= docker-compose-mcp.yml
COMPOSE_APP ?= docker-compose-app.yml
COMPOSE_LAB ?= docker-compose-lab.yml
SEED        ?= data/seed/ai_infra.yaml
MCP_ENV     ?= mcp.env
N           ?= 3
TAG         ?= latest
REGISTRY    ?=
PLATFORMS   ?= linux/amd64,linux/arm64
MCP_IMAGE   := sg-exchange-mcp
MCP_URL     ?= https://mcp.localtest.me:8443/mcp
MCP_BASE    := $(patsubst %/mcp,%,$(MCP_URL))
CERTS       := deploy/.certs/localtest.pem
# curl on macOS does not read the keychain, so point it at mkcert's CA explicitly.
CURL         = curl -fsS --cacert "$$(mkcert -CAROOT)/rootCA.pem"

DC = docker compose --env-file $(MCP_ENV) -f $(COMPOSE_MCP)
APP = docker compose -f $(COMPOSE_APP)
LAB = docker compose -f $(COMPOSE_LAB)

.PHONY: help demo publish mcp-env mcp-certs mcp-build mcp-up mcp-wait mcp-down mcp-clean mcp-ps mcp-logs \
        mcp-bootstrap mcp-scale mcp-test mcp-token mcp-walkthrough mcp-login mcp-push \
        app-build app-up app-down app-logs app-seed app-shell \
        lab-up lab-smoke lab-logs lab-shell lab-down \
        ollama-serve ollama-log ollama-watch qwen-pull qwen-check qwen-status qwen-env

# --- Setup -----------------------------------------------------------

# Copy the example env and fill in random passwords. Never overwrites an existing mcp.env.
mcp-env: $(MCP_ENV)
$(MCP_ENV):
	@sed -e "s/^KEYCLOAK_ADMIN_PASSWORD=__generate__/KEYCLOAK_ADMIN_PASSWORD=$$(openssl rand -hex 16)/" \
	     -e "s/^SG_USER_PASSWORD=__generate__/SG_USER_PASSWORD=$$(openssl rand -hex 12)/" \
	     mcp.env.example > $@
	@echo "\033[1;32mwrote $@\033[0m (gitignored). Your login: user researcher, SG_USER_PASSWORD in that file."
	@echo "Edit SEC_USER_AGENT in $@ before fetching US funds."

# Local TLS via mkcert (one-time `mkcert -install` makes your Mac trust it). Shared with k3d.
mcp-certs: $(CERTS)
$(CERTS):
	@command -v mkcert >/dev/null || { echo "install mkcert: brew install mkcert && mkcert -install"; exit 1; }
	@mkdir -p deploy/.certs
	mkcert -cert-file deploy/.certs/localtest.pem -key-file deploy/.certs/localtest-key.pem "*.localtest.me" localtest.me

# --- Run (docker compose) --------------------------------------------

mcp-build: ## Build the MCP image
	$(DC) build exchange-mcp

mcp-up: mcp-env mcp-certs ## Start everything; first run takes ~1 min while Keycloak boots
	$(DC) up -d --build proxy exchange-mcp
	@$(MAKE) --no-print-directory mcp-wait
	@$(DC) logs --no-log-prefix keycloak-bootstrap | tail -1
	@$(MAKE) --no-print-directory mcp-ps

# Poll through the proxy until the MCP server answers (Keycloak boot + realm script ≈ 1 min).
mcp-wait:
	@printf "waiting for $(MCP_BASE) "; for i in $$(seq 1 60); do \
	  $(CURL) -o /dev/null $(MCP_BASE)/readyz 2>/dev/null && { echo " ready"; exit 0; }; printf "."; sleep 3; \
	done; echo " timed out; see: make mcp-logs"; exit 1

mcp-down: ## Stop; Keycloak's data volume is kept
	$(DC) down

mcp-clean: ## Stop and delete volumes (realm, users and registered clients are gone)
	$(DC) down -v

mcp-ps:
	@$(DC) ps
	@echo
	@$(CURL) $(MCP_BASE)/.well-known/oauth-protected-resource/mcp 2>/dev/null | python3 -m json.tool \
	  || echo "metadata not reachable yet at $(MCP_URL)"

mcp-logs: ## Follow MCP and Keycloak logs
	$(DC) logs -f exchange-mcp keycloak

mcp-bootstrap: ## Re-run the realm script (after editing deploy/k8s/base/keycloak-bootstrap.sh)
	$(DC) run --rm keycloak-bootstrap

mcp-scale: ## Run N stateless replicas behind the proxy: make mcp-scale N=3
	$(DC) up -d --no-recreate --scale exchange-mcp=$(N) exchange-mcp
	@$(DC) ps exchange-mcp

# --- Try it ------------------------------------------------------------

mcp-test: ## Unit + end-to-end tests (no Docker needed)
	cd mcp-server && python3 -m pytest -q

mcp-token: ## Device-flow token: TOKEN=$$(make -s mcp-token)
	@deploy/scripts/sg-token.sh

mcp-walkthrough: ## The OAuth flow, step by step, against the running stack
	SSL_CERT_FILE="$$(mkcert -CAROOT)/rootCA.pem" python3 deploy/scripts/oauth-walkthrough.py $(MCP_URL)

mcp-login: ## Add the server to Claude Code, then run /mcp in Claude Code to sign in
	@echo "export NODE_EXTRA_CA_CERTS=\"$$(mkcert -CAROOT)/rootCA.pem\"   # Node does not read the macOS keychain"
	claude mcp add --transport http sg-exchanges $(MCP_URL)

# --- SupplyGraph app (API + UI) on docker compose ----------------------------

app-build: ## Build the app image (context = repo root)
	$(APP) build supplygraph

app-up: ## Build and start the app on http://localhost:58100
	$(APP) up -d --build supplygraph
	@printf "waiting for the app "; for i in $$(seq 1 30); do \
	  curl -fsS -o /dev/null http://localhost:$${APP_PORT:-58100}/api/themes 2>/dev/null && { echo " ready"; exit 0; }; printf "."; sleep 2; \
	done; echo " timed out; see: make app-logs"; exit 1

app-seed: ## Load $(SEED) into the container's database (idempotent)
	$(APP) exec supplygraph disg seed /app/$(SEED)

app-logs: ## Follow app logs
	$(APP) logs -f supplygraph

app-shell: ## Shell in the app container (disg ingest/categorise/export live here)
	$(APP) exec supplygraph bash

app-down: ## Stop; the data volume is kept (add -v by hand to wipe it)
	$(APP) down

# --- REST in Peace lab (docker compose) ------------------------------

lab-up: ## Build and start the lab on http://localhost:58200, with the app on :58100
	$(LAB) up -d --build lab supplygraph
	@printf "waiting for the lab "; for i in $$(seq 1 30); do \
	  curl -fsS -o /dev/null http://localhost:$${LAB_PORT:-58200}/api/status 2>/dev/null && { echo " ready: http://localhost:$${LAB_PORT:-58200}"; exit 0; }; printf "."; sleep 2; \
	done; echo " timed out; see: make lab-logs"; exit 1
	@[ -n "$$ANTHROPIC_API_KEY" ] || echo "\033[33mANTHROPIC_API_KEY is not set: J2-J4 stay disabled. export it and re-run make lab-up\033[0m"

lab-smoke: ## Run the stubbed smoke test inside the lab container (no API)
	$(LAB) exec lab python experiments/rest-in-peace/smoke.py

lab-logs: ## Follow lab logs
	$(LAB) logs -f lab

lab-shell: ## Shell in the lab container
	$(LAB) exec lab bash

lab-down: ## Stop the lab and the app; results and bench.db stay in experiments/rest-in-peace/
	$(LAB) down

# --- Local models: Ollama + Qwen (D-26) ----------------------------------------
# The categoriser and the experiments call whatever DISG_PROVIDER / DISG_MODEL / DISG_BASE_URL name.
# These targets point them at a Qwen model served by Ollama on this Mac.
#   M3 Pro, 36 GB: qwen3:14b (default, ~9 GB + KV cache) fits comfortably at a 32k window;
#   qwen3:30b-a3b (MoE, ~19 GB) also fits and is faster per token; 32B dense is too tight.
QWEN_MODEL  ?= qwen3:14b
OLLAMA_CTX  ?= 32768
OLLAMA_URL  ?= http://localhost:11434/v1
VENV        ?= .venv
LOCAL_LLM    = DISG_PROVIDER=openai DISG_BASE_URL=$(OLLAMA_URL) DISG_MODEL=$(QWEN_MODEL)

ollama-serve: ## Run Ollama in the foreground with a context window big enough for categorise
	@command -v ollama >/dev/null || { echo "install Ollama first: brew install ollama"; exit 1; }
	@if curl -fsS -m 2 http://localhost:11434/api/version >/dev/null 2>&1; then \
	  echo "Ollama is already running (often the Ollama menu-bar app), with whatever context window it started with."; \
	  echo "To run it with OLLAMA_CONTEXT_LENGTH=$(OLLAMA_CTX):"; \
	  echo "  either quit the Ollama app (menu bar -> Quit Ollama) and run make ollama-serve again,"; \
	  echo "  or: launchctl setenv OLLAMA_CONTEXT_LENGTH $(OLLAMA_CTX), then quit and reopen the app."; \
	  echo "Check what is loaded and with which context: make qwen-status"; \
	else mkdir -p $(dir $(SERVE_LOG)); echo "log: $(SERVE_LOG) (make ollama-watch draws a progress bar from it)"; \
	  OLLAMA_CONTEXT_LENGTH=$(OLLAMA_CTX) ollama serve 2>&1 | tee -a "$(SERVE_LOG)"; fi

qwen-pull: ## Download the model: make qwen-pull QWEN_MODEL=qwen3:30b-a3b
	ollama pull $(QWEN_MODEL)

qwen-check: ## Structured output, context window and speed of the local model, via disg llm-check
	@$(VENV)/bin/python -c "import openai" 2>/dev/null || $(VENV)/bin/pip install -q -e "backend[dev]"
	@curl -fsS -m 2 http://localhost:11434/api/version >/dev/null 2>&1 || { echo "Ollama is not running: make ollama-serve"; exit 1; }
	@ollama list | awk 'NR>1{print $$1}' | grep -qx "$(QWEN_MODEL)" || { echo "$(QWEN_MODEL) is not pulled: make qwen-pull"; exit 1; }
	@echo "First call loads the model into memory and reads a ~16k-token prompt: 1-3 minutes is normal. Gives up after $${DISG_TIMEOUT:-300}s."
	@if [ -n "$(OLLAMA_LOG)" ]; then python3 scripts/ollama_progress.py "$(OLLAMA_LOG)" --pid $$$$ & W=$$!; \
	else echo "(no progress bar: Ollama's log is in the terminal it was started from; restart it with make ollama-serve)"; fi; \
	$(LOCAL_LLM) $(VENV)/bin/disg llm-check --tokens 16000; rc=$$?; \
	[ -n "$$W" ] && kill $$W 2>/dev/null; exit $$rc

# Where the log is depends on how Ollama was started: make ollama-serve writes $(SERVE_LOG) (and the
# terminal), the menu-bar app ~/.ollama/logs/server.log, `brew services start ollama`
# $(brew --prefix)/var/log/ollama.log, and a plain `ollama serve` only its own terminal.
# OLLAMA_LOG is whichever of these files changed most recently.
SERVE_LOG  ?= $(HOME)/.ollama/logs/serve.log
OLLAMA_LOG  = $(firstword $(shell ls -t $(SERVE_LOG) $(HOME)/.ollama/logs/server.log $(shell brew --prefix 2>/dev/null)/var/log/ollama.log 2>/dev/null))

qwen-status: ## What Ollama has loaded, on CPU or GPU, and with what context window
	@ollama ps
	@echo
	@echo "server: $$(ps -o command= -p $$(lsof -ti tcp:11434 -sTCP:LISTEN 2>/dev/null | head -1) 2>/dev/null || echo 'not running')"
	@if [ -n "$(OLLAMA_LOG)" ]; then echo "log: $(OLLAMA_LOG)"; \
	  grep -E "truncat|library=|n_ctx|offload" "$(OLLAMA_LOG)" | tail -8; \
	else echo "log: none found; Ollama was started with a plain 'ollama serve', so its log is in that terminal"; fi

ollama-log: ## Follow Ollama's server log, wherever this Mac keeps it
	@[ -n "$(OLLAMA_LOG)" ] || { echo "No log file: Ollama was started with a plain 'ollama serve'; its log is in that terminal."; exit 1; }
	tail -f "$(OLLAMA_LOG)"

ollama-watch: ## Progress bar for every request Ollama serves (disg, VS Code, curl); Ctrl-C to stop
	@[ -n "$(OLLAMA_LOG)" ] || { echo "No log file: restart Ollama with make ollama-serve."; exit 1; }
	@echo "watching $(OLLAMA_LOG)"
	@python3 scripts/ollama_progress.py "$(OLLAMA_LOG)"

qwen-env: ## Print the exports that point disg at the local model: eval "$$(make -s qwen-env)"
	@echo 'export DISG_PROVIDER=openai DISG_BASE_URL=$(OLLAMA_URL) DISG_MODEL=$(QWEN_MODEL)'

# --- Registry ------------------------------------------------------------

mcp-push: ## Multi-arch build and push: make mcp-push REGISTRY=ghcr.io/you TAG=v0.1.0
	@test -n "$(REGISTRY)" || { echo "set REGISTRY, e.g. make mcp-push REGISTRY=ghcr.io/you TAG=v0.1.0"; exit 1; }
	docker buildx build --platform $(PLATFORMS) -t $(REGISTRY)/$(MCP_IMAGE):$(TAG) --push mcp-server

# --- Kubernetes (k3d): every target in deploy/Makefile as k8s-<target> ----

k8s-%:
	@$(MAKE) --no-print-directory -C deploy $*

# --- Help ------------------------------------------------------------------

help:
	@echo "\033[1;33mSupplyGraph\033[0m"
	@echo "\033[1;33m===========\033[0m"
	@echo ""
	@echo "\033[1;35mExchange MCP on docker compose:\033[0m"
	@echo "  \033[1;36mmcp-up\033[0m          Build and start Keycloak, the MCP server and the TLS proxy"
	@echo "                  First run also writes \033[33mmcp.env\033[0m (passwords) and the mkcert certificate"
	@echo "  \033[1;36mmcp-ps\033[0m          Containers, plus the server's protected-resource metadata"
	@echo "  \033[1;36mmcp-logs\033[0m        Follow MCP and Keycloak logs"
	@echo "  \033[1;36mmcp-scale\033[0m       Run N MCP replicas behind the proxy   Args: \033[33mN=3\033[0m"
	@echo "  \033[1;36mmcp-bootstrap\033[0m   Re-run the Keycloak realm script"
	@echo "  \033[1;36mmcp-down\033[0m        Stop (keeps Keycloak data)"
	@echo "  \033[1;36mmcp-clean\033[0m       Stop and delete volumes"
	@echo ""
	@echo "\033[1;35mSupplyGraph app on docker compose:\033[0m"
	@echo "  \033[1;36mapp-up\033[0m          Build and start the app on \033[33mhttp://localhost:58100\033[0m   Args: \033[33mAPP_PORT=58100 APP_DATA=./data\033[0m"
	@echo "  \033[1;36mapp-seed\033[0m        Load the seed YAML into the container database   Args: \033[33mSEED=data/seed/ai_infra.yaml\033[0m"
	@echo "  \033[1;36mapp-logs\033[0m        Follow app logs"
	@echo "  \033[1;36mapp-shell\033[0m       Shell inside the container"
	@echo "  \033[1;36mapp-down\033[0m        Stop (keeps data)"
	@echo ""
	@echo "\033[1;35mLocal models (Ollama + Qwen):\033[0m"
	@echo "  \033[1;36mollama-serve\033[0m    Ollama with a $(OLLAMA_CTX)-token window (leave running)"
	@echo "  \033[1;36mqwen-pull\033[0m       Download \033[33m$(QWEN_MODEL)\033[0m   Args: \033[33mQWEN_MODEL=qwen3:30b-a3b\033[0m"
	@echo "  \033[1;36mqwen-check\033[0m      Can it do the job? tool call, context window, tokens/s"
	@echo "  \033[1;36mqwen-status\033[0m     What Ollama has loaded, CPU or GPU, context window"
	@echo "  \033[1;36mollama-log\033[0m      Follow Ollama's server log"
	@echo "  \033[1;36mollama-watch\033[0m    Progress bar for every request: reading prompt, generating, done"
	@echo "  \033[1;36mqwen-env\033[0m        \033[32meval \"\$$(make -s qwen-env)\"\033[0m, then disg categorise uses Qwen"
	@echo ""
	@echo "\033[1;35mREST in Peace lab (experiments J1-J5):\033[0m"
	@echo "  \033[1;36mlab-up\033[0m          Build and start the lab on \033[33mhttp://localhost:58200\033[0m, app on :58100   Needs \033[33mANTHROPIC_API_KEY\033[0m for J2-J4"
	@echo "  \033[1;36mlab-smoke\033[0m       Stubbed smoke test in the container (no API)"
	@echo "  \033[1;36mlab-logs\033[0m        Follow lab logs"
	@echo "  \033[1;36mlab-shell\033[0m       Shell inside the lab container"
	@echo "  \033[1;36mlab-down\033[0m        Stop lab and app (results stay on disk)"
	@echo ""
	@echo "\033[1;35mTry the auth flow:\033[0m"
	@echo "  \033[1;36mmcp-walkthrough\033[0m Print each OAuth step: 401 → metadata → DCR → PKCE login → token → call"
	@echo "  \033[1;36mmcp-token\033[0m       Device-flow access token for curl"
	@echo "                  \033[32mTOKEN=\$$(make -s mcp-token) deploy/scripts/mcp-call.sh tools/list\033[0m"
	@echo "  \033[1;36mmcp-login\033[0m       Register the server in Claude Code"
	@echo "  \033[1;36mmcp-test\033[0m        Unit and end-to-end tests"
	@echo ""
	@echo "\033[1;35mRegistry:\033[0m"
	@echo "  \033[1;36mmcp-push\033[0m        Multi-arch build and push   Args: \033[33mREGISTRY=ghcr.io/you TAG=v0.1.0\033[0m"
	@echo ""
	@echo "\033[1;35mKubernetes (k3d), when you get to scaling:\033[0m"
	@echo "  \033[1;36mk8s-up\033[0m, \033[1;36mk8s-status\033[0m, \033[1;36mk8s-logs\033[0m, \033[1;36mk8s-rollout\033[0m, \033[1;36mk8s-scale-test\033[0m, \033[1;36mk8s-down\033[0m"
	@echo "                  (every target in deploy/Makefile, prefixed k8s-)"
	@echo ""
	@echo "\033[1;35mPublic demo:\033[0m"
	@echo "  \033[1;36mdemo\033[0m            Build the static demo page (no server, no holdings, unreviewed AI left out)"
	@echo "  \033[1;36mpublish\033[0m         Public copy in dist/public (source from HEAD minus dev docs + demo)   Args: \033[33mPUBLIC_REPO_DIR=… PUBLIC_SOURCE_URL=…\033[0m"
	@echo ""
	@echo "\033[1;35mHosted on one VM (docker-compose-prod.yml), runbook docs/deploy/vm.md:\033[0m"
	@echo "  \033[1;36mprod-ship\033[0m       [Mac] Copy the committed tree (HEAD) to the VM   Args: \033[33mVM=user@host REMOTE_DIR=supplygraph\033[0m"
	@echo "  \033[1;36mprod-push-data\033[0m  [Mac] Upload data/supplygraph.db and data/docs to the VM's app volume"
	@echo "  \033[1;36mprod-tunnel\033[0m     [Mac] Keycloak admin console at http://localhost:8080"
	@echo "  \033[1;36mprod-env\033[0m        [VM] Write prod.env with random secrets (once)"
	@echo "  \033[1;36mprod-up\033[0m         [VM] Build, start, bootstrap the realm; also applies a new release"
	@echo "  \033[1;36mprod-logs\033[0m, \033[1;36mprod-ps\033[0m, \033[1;36mprod-shell\033[0m, \033[1;36mprod-backup\033[0m, \033[1;36mprod-bootstrap\033[0m, \033[1;36mprod-down\033[0m"
	@echo ""
	@echo "Guide: docs/mcp/README.md"

.DEFAULT_GOAL := help

# --- Public static demo (D-39) -----------------------------------------------------
# One self-contained HTML: no server, no documents, no holdings, no unreviewed AI proposals; a disclaimer on top.
demo: ## Build the public demo page: frontend/dist-static/supplygraph.html
	$(VENV)/bin/disg export ai_infra_in --public -o frontend/public/graph.json
	cd frontend && npm run build:static
	@echo "open frontend/dist-static/supplygraph.html; publish that one file anywhere static"

# --- Publish: public source + demo (D-40) ---------------------------------------------
# Rules (what is left out, rewritten, forbidden) are in publish/publish.yaml. Built from HEAD, never the working tree.
# The public repo (github.com/kryochronic/supplygraph) serves the demo on GitHub Pages at supplygraph.prkhya.com;
# this private repo is kryochronic/supplygraph-src. Clone the public one next to this one (PUBLIC_REPO_DIR).
PUBLIC_REPO_DIR    ?= ../supplygraph-public
PUBLIC_SOURCE_URL  ?= https://github.com/kryochronic/supplygraph
PUBLIC_DEMO_URL    ?= https://supplygraph.prkhya.com
PUBLIC_PROJECT_URL ?=
PUBLIC_DOMAIN      ?= supplygraph.prkhya.com
publish: ## Public copy in dist/public: source from HEAD minus dev docs, README, demo at site/   Args: PUBLIC_REPO_DIR=../supplygraph-public PUBLIC_SOURCE_URL=… PUBLIC_DEMO_URL=… PUBLIC_PROJECT_URL=… PUBLIC_DOMAIN=…
	VITE_SOURCE_URL="$(PUBLIC_SOURCE_URL)" VITE_PROJECT_URL="$(PUBLIC_PROJECT_URL)" $(MAKE) --no-print-directory demo
	$(VENV)/bin/python publish/publish.py --out dist/public --site frontend/dist-static/supplygraph.html \
	  --source-url "$(PUBLIC_SOURCE_URL)" --demo-url "$(PUBLIC_DEMO_URL)" --project-url "$(PUBLIC_PROJECT_URL)" \
	  --domain "$(PUBLIC_DOMAIN)" $(if $(PUBLIC_REPO_DIR),--sync "$(PUBLIC_REPO_DIR)")

# --- Hosted on one VM (docker-compose-prod.yml; docs/deploy/vm.md) -------------
-include deploy/prod/prod.mk
