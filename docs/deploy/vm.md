# Hosting SupplyGraph on one VM

*Runbook for `docker-compose-prod.yml`: the full app behind a Keycloak login at app.supplygraph.prkhya.com
(supplygraph.prkhya.com itself is the public demo, served by GitHub Pages from github.com/kryochronic/supplygraph).
Scope and decisions live in the project register (C31, D-38). Kubernetes comes later, once this
can be rebuilt from scratch with these steps alone.*

```
internet ──443──▶ Caddy ─┬─ app.supplygraph.prkhya.com ──▶ oauth2-proxy ──▶ app (FastAPI + UI, SQLite on a volume)
                         └─ auth.prkhya.com        ──▶ Keycloak (+ Postgres); admin console only over SSH
```

Who gets in: users of Keycloak realm `supplygraph` holding realm role `sg-user`. Self-registration is off.
Everyone else, including a valid Keycloak user without the role, gets a login page or a 403, never the app.

## 1. What you need

| Item | Notes |
|---|---|
| A VM | Ubuntu 24.04, 2 vCPU, 4 GB RAM, 40 GB disk is plenty (Keycloak ~1 GB, the rest small). Any cloud. |
| Firewall | Inbound 22 (your IP only, ideally), 80 and 443 (anyone). Nothing else. |
| DNS | Two A records to the VM's public IP: `app.supplygraph.prkhya.com`, `auth.prkhya.com`. Create them first: Caddy asks Let's Encrypt for certificates on the first request, and that fails until DNS resolves. |
| A model key | `ANTHROPIC_API_KEY` (or OpenAI / OpenRouter). There is no Ollama on the VM; local Qwen stays on your Mac. |

## 2. Prepare the VM (once)

```bash
ssh user@vm
curl -fsSL https://get.docker.com | sh          # Docker Engine + compose plugin
sudo usermod -aG docker $USER && exit           # log in again for the group to apply
ssh user@vm 'sudo apt-get install -y make openssl && docker compose version'
```

## 3. Ship the code (from your Mac)

The VM gets exactly what is committed, via `git archive HEAD`: no working-tree changes, no `data/`, no
`reports/`, no secrets, and no GitHub credentials on the VM.

```bash
make prod-ship VM=user@vm            # → ~/supplygraph on the VM; RELEASE holds the commit id
```

## 4. Configure and start (on the VM)

```bash
cd ~/supplygraph
make prod-env                        # writes prod.env (mode 600) with random secrets
nano prod.env                        # set ACME_EMAIL, SG_USER_EMAIL, ANTHROPIC_API_KEY; check the hostnames
make prod-up                         # build, start, create the realm, your user and the client
```

`prod-up` prints the URL and your username; the password is `SG_USER_PASSWORD` in `prod.env`. Log in once
and change it (Keycloak account page: `https://auth.prkhya.com/realms/supplygraph/account`). Re-running
`make prod-up` or `make prod-bootstrap` never resets a password you changed.

## 5. Bring your data (from your Mac)

Your local database and fetched filings, copied into the app's volume. This **replaces** what is on the VM.

```bash
make prod-push-data VM=user@vm
```

It takes a consistent SQLite snapshot (`.backup`, safe while your local server runs), uploads it with
`data/docs/`, restarts the app, and the app applies any pending migrations on start. Not copied:
`data/private/` (your local connector overrides point at Ollama, which the VM does not have).

## 6. Day to day

| Task | Where | Command |
|---|---|---|
| Release a new commit | Mac, then VM | `make prod-ship VM=…` then `make prod-up` |
| Logs | VM | `make prod-logs S=supplygraph` (or `oauth2-proxy`, `keycloak`, `caddy`) |
| A `disg` command | VM | `make prod-shell`, then e.g. `disg quotes` |
| Back up | VM | `make prod-backup` → `backups/<date>/` (SQLite online backup + `pg_dump`). Copy it off the VM: `scp -r user@vm:supplygraph/backups .` |
| Nightly backup | VM | `crontab -e`: `17 2 * * * cd ~/supplygraph && make prod-backup >> backups/cron.log 2>&1` |
| Keycloak admin | Mac | `make prod-tunnel VM=…`, then http://localhost:8080/admin (user `admin`, `KEYCLOAK_ADMIN_PASSWORD`) |
| Let someone in | Keycloak admin | Realm `supplygraph` → Users → Add user → set password → Role mapping → assign `sg-user` |
| Stop | VM | `make prod-down` (volumes stay) |

## 7. Restore

```bash
make prod-down
docker run --rm -v supplygraph-prod_sg_data:/d -v $PWD/backups/<date>:/b alpine \
  sh -c 'cp /b/supplygraph.db /d/supplygraph.db && chown 10001 /d/supplygraph.db'
docker compose --env-file prod.env -f docker-compose-prod.yml up -d postgres
docker compose --env-file prod.env -f docker-compose-prod.yml exec -T postgres \
  pg_restore -U keycloak -d keycloak --clean --if-exists < backups/<date>/keycloak.dump
make prod-up
```

## 8. Things to know

- **Model runs cost money here.** Analyse, Extract and Ask call the hosted model with your key. The run
  queue is the same as on the Mac: one job at a time.
- **Certificates** live in the `caddy_data` volume. Keep it: deleting it and re-issuing many times a week hits
  Let's Encrypt's rate limits.
- **The admin console is not on the internet.** Caddy returns 404 for `/admin` and the master realm on
  `auth.prkhya.com`; Keycloak's port 8080 is bound to the VM's loopback only.
- **Session length:** 7 days, refreshed hourly while you use it (oauth2-proxy cookie); Keycloak's own SSO
  session idles out after a day. If a page stops loading data after a long idle, reload it to log in again.
- **Files removed from the repo** are not removed from the VM by `prod-ship` (it unpacks over the old tree).
  They are harmless; to clean up, delete `~/supplygraph` except `prod.env` and `backups/`, then ship again.

## 9. What was verified before the first deploy (10 Nov 2026)

In Claude's workspace, without Docker images (registries unreachable there), with the same versions run as
binaries: Keycloak 26.8.0 in production mode, `deploy/prod/keycloak-app.sh` (run twice: idempotent),
oauth2-proxy v7.12.0 with the compose file's settings, Caddy 2.10.2 with this Caddyfile (internal
certificates instead of Let's Encrypt), and the app.

- Without a session, `/` and `/api/*` redirect to Keycloak (authorization code + PKCE S256).
- `/admin/…` and `/realms/master/…` on the auth host return 404; tokens carry the public issuer.
- A browser logs in as `researcher`, lands on the graph, and the app defaults to the Claude connector.
- A Keycloak user without `sg-user` gets 403 from oauth2-proxy; sign-out ends the session.
- `docker compose config` validates the file.

**Not verified:** the Docker image build and the real Let's Encrypt issuance (both need the VM), and
`prod-push-data` / `prod-backup` end to end. Check those on the first deploy.
