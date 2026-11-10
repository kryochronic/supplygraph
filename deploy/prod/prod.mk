# Hosted SupplyGraph on one VM (docker-compose-prod.yml). Included by the root Makefile; see docs/deploy/vm.md.
#
# On your Mac:                         On the VM (in REMOTE_DIR):
#   make prod-ship VM=user@host          make prod-env      once, then edit prod.env
#   make prod-push-data VM=user@host     make prod-up       start / apply a new release
#   make prod-tunnel VM=user@host        make prod-backup   database + Keycloak dump to backups/
#
# Ship and push-data run here and talk to the VM over ssh; everything else runs on the VM.

COMPOSE_PROD ?= docker-compose-prod.yml
PROD_ENV     ?= prod.env
VM           ?=
REMOTE_DIR   ?= supplygraph
PROD = docker compose --env-file $(PROD_ENV) -f $(COMPOSE_PROD)

.PHONY: prod-env prod-up prod-down prod-ps prod-logs prod-bootstrap prod-backup prod-shell \
        prod-ship prod-push-data prod-tunnel need-vm

# --- on the VM -----------------------------------------------------------------------------

# Copy the example and fill in random secrets. Never overwrites an existing prod.env.
prod-env: $(PROD_ENV)
$(PROD_ENV):
	@sed -e "s/^KEYCLOAK_ADMIN_PASSWORD=__generate__/KEYCLOAK_ADMIN_PASSWORD=$$(openssl rand -hex 16)/" \
	     -e "s/^KC_DB_PASSWORD=__generate__/KC_DB_PASSWORD=$$(openssl rand -hex 16)/" \
	     -e "s/^OIDC_CLIENT_SECRET=__generate__/OIDC_CLIENT_SECRET=$$(openssl rand -hex 24)/" \
	     -e "s/^COOKIE_SECRET=__generate__/COOKIE_SECRET=$$(openssl rand -hex 16)/" \
	     -e "s/^SG_USER_PASSWORD=__generate__/SG_USER_PASSWORD=$$(openssl rand -hex 12)/" \
	     prod.env.example > $@
	@chmod 600 $@
	@echo "\033[1;32mwrote $@\033[0m (mode 600, gitignored). Now edit: ACME_EMAIL, SG_USER_EMAIL, ANTHROPIC_API_KEY."

prod-up: $(PROD_ENV) ## [VM] Build and start (or apply a new release); bootstraps the realm
	@grep -q "^ACME_EMAIL=you@example.com" $(PROD_ENV) && { echo "edit ACME_EMAIL in $(PROD_ENV) first"; exit 1; } || true
	$(PROD) up -d --build --remove-orphans caddy oauth2-proxy
	$(PROD) run --rm keycloak-bootstrap
	$(PROD) ps
	@echo "open https://$$(sed -n 's/^SG_HOST=//p' $(PROD_ENV)) and log in as $$(sed -n 's/^SG_USER=//p' $(PROD_ENV)) (SG_USER_PASSWORD in $(PROD_ENV))"

prod-bootstrap: ## [VM] Re-run the realm script (idempotent)
	$(PROD) run --rm keycloak-bootstrap

prod-down: ## [VM] Stop; all volumes (database, certificates, Keycloak) are kept
	$(PROD) down

prod-ps: ## [VM] What is running
	$(PROD) ps

prod-logs: ## [VM] Follow logs: make prod-logs S=supplygraph
	$(PROD) logs -f --tail 100 $(S)

prod-shell: ## [VM] Shell in the app container (disg commands live here)
	$(PROD) exec supplygraph bash

# Consistent copies while running: SQLite's online backup API, and pg_dump. Keep backups/ off the VM too.
prod-backup: ## [VM] Back up the app database and Keycloak to backups/<date>/
	@d=backups/$$(date -u +%Y%m%dT%H%MZ); mkdir -p $$d; \
	$(PROD) exec -T supplygraph python -c "import sqlite3; s=sqlite3.connect('/app/data/supplygraph.db'); d=sqlite3.connect('/app/data/backup.db'); s.backup(d); d.close()" && \
	$(PROD) cp supplygraph:/app/data/backup.db $$d/supplygraph.db && \
	$(PROD) exec -T supplygraph rm -f /app/data/backup.db && \
	$(PROD) exec -T postgres pg_dump -U keycloak -Fc keycloak > $$d/keycloak.dump && \
	echo "backed up to $$d"

# --- on your Mac ---------------------------------------------------------------------------

need-vm:
	@[ -n "$(VM)" ] || { echo "say which VM: make $(MAKECMDGOALS) VM=user@host"; exit 1; }

# Ship exactly what is committed (HEAD), not the working tree: no data, no reports, no secrets.
prod-ship: need-vm ## [Mac] Copy the committed tree (HEAD) to the VM: make prod-ship VM=user@host
	@git diff --quiet HEAD -- . ':!docs' || echo "\033[33mnote: uncommitted changes are NOT shipped; HEAD is $$(git rev-parse --short HEAD)\033[0m"
	ssh $(VM) "mkdir -p $(REMOTE_DIR)"
	git archive --format=tar HEAD | ssh $(VM) "tar -x -C $(REMOTE_DIR) && echo $$(git rev-parse --short HEAD) > $(REMOTE_DIR)/RELEASE"
	@echo "shipped $$(git rev-parse --short HEAD). On the VM: cd $(REMOTE_DIR) && make prod-up"

# Your local database and fetched filings into the app's volume. Replaces what is there (back it up first).
prod-push-data: need-vm ## [Mac] Upload data/supplygraph.db and data/docs to the VM's app volume
	@command -v sqlite3 >/dev/null || { echo "needs sqlite3"; exit 1; }
	@rm -rf .push && mkdir -p .push/data && sqlite3 data/supplygraph.db ".backup .push/data/supplygraph.db"
	@[ -d data/docs ] && cp -R data/docs .push/data/docs || true
	tar -czf .push/sg-data.tgz -C .push/data .
	scp .push/sg-data.tgz $(VM):$(REMOTE_DIR)/sg-data.tgz
	ssh $(VM) "cd $(REMOTE_DIR) && $(PROD) stop supplygraph && \
	  docker run --rm -v supplygraph-prod_sg_data:/app/data -v \$$PWD:/in alpine sh -c 'tar -xzf /in/sg-data.tgz -C /app/data && chown -R 10001 /app/data' && \
	  rm sg-data.tgz && $(PROD) start supplygraph"
	@rm -rf .push
	@echo "data pushed; the app restarted and applied any pending migrations"

prod-tunnel: need-vm ## [Mac] Keycloak admin console at http://localhost:8080 (Ctrl-C to close)
	@echo "Keycloak admin: http://localhost:8080/admin  (user admin, KEYCLOAK_ADMIN_PASSWORD in the VM's prod.env)"
	ssh -N -L 8080:127.0.0.1:8080 $(VM)
