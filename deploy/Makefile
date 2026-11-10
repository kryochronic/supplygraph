# Local MCP stack on k3d.   make k8s-up (from the repo root) or make -C deploy up
# Prerequisites: Docker Desktop (or OrbStack/Colima), k3d, kubectl, mkcert; jq for the scripts.
#   brew install k3d kubectl mkcert jq && mkcert -install

CLUSTER   := sg
NS        := supplygraph
IMAGE     := sg-exchange-mcp:dev
OVERLAY   := k8s/overlays/local
K         := kubectl -n $(NS)

.PHONY: up cluster image secrets tls apply keycloak-bootstrap status logs test token down rollout scale-test

up: cluster image secrets tls apply status ## everything, idempotent

cluster:
	@k3d cluster list $(CLUSTER) >/dev/null 2>&1 || k3d cluster create --config k3d.yaml

image:
	docker build -t $(IMAGE) ../mcp-server
	k3d image import $(IMAGE) -c $(CLUSTER)

secrets: $(OVERLAY)/secrets.env
$(OVERLAY)/secrets.env:
	@printf 'KEYCLOAK_ADMIN_USER=admin\nKEYCLOAK_ADMIN_PASSWORD=%s\nSG_USER_PASSWORD=%s\n' \
	  "$$(openssl rand -hex 16)" "$$(openssl rand -hex 12)" > $@
	@echo "wrote $@ (gitignored). Your login: user researcher, password in that file."

# Local TLS: mkcert makes a CA your Mac trusts (`mkcert -install`) and signs *.localtest.me.
# Traefik terminates TLS with it; traffic inside the cluster stays plain HTTP.
tls: .certs/localtest.pem
	kubectl create namespace $(NS) --dry-run=client -o yaml | kubectl apply -f -
	$(K) create secret tls localtest-tls --cert=.certs/localtest.pem --key=.certs/localtest-key.pem \
	  --dry-run=client -o yaml | kubectl apply -f -
.certs/localtest.pem:
	@mkdir -p .certs
	mkcert -cert-file .certs/localtest.pem -key-file .certs/localtest-key.pem "*.localtest.me" localtest.me

# The bootstrap Job is deleted first on every apply: Jobs are immutable, and the script
# is idempotent, so re-running it is cheap and keeps the realm in step with the script.
apply:
	-@$(K) delete job keycloak-bootstrap --ignore-not-found >/dev/null 2>&1
	kubectl apply -k $(OVERLAY)
	$(K) wait --for=condition=complete job/keycloak-bootstrap --timeout=600s
	@$(K) logs job/keycloak-bootstrap | tail -3
	$(K) rollout status deploy/exchange-mcp --timeout=180s

keycloak-bootstrap: apply ## re-run the realm script after editing it

rollout: image ## ship a new image: pods are replaced one by one, the PDB keeps one serving
	$(K) rollout restart deploy/exchange-mcp
	$(K) rollout status deploy/exchange-mcp

status:
	@$(K) get pods,svc,ingress,hpa,pdb -o wide
	@echo
	@curl -fsS --cacert "$$(mkcert -CAROOT)/rootCA.pem" https://mcp.localtest.me:8443/.well-known/oauth-protected-resource/mcp | jq . || true

logs:
	$(K) logs -l app=exchange-mcp --prefix -f --max-log-requests 10

test:
	cd ../mcp-server && python -m pytest -q

token:
	@scripts/sg-token.sh

# Watch the HPA react (`kubectl -n supplygraph get hpa -w` in another terminal).
# Each request is a full token check (RSA signature) plus a tool call, so CPU climbs.
#   TOKEN=$$(scripts/sg-token.sh) make scale-test
scale-test:
	@test -n "$$TOKEN" || (echo "set TOKEN first: TOKEN=\$$(scripts/sg-token.sh)"; exit 1)
	$(K) run load --rm -i --image=busybox:1.36 --restart=Never --env=TOKEN=$$TOKEN -- sh -c '\
	  end=$$(( $$(date +%s) + 180 )); \
	  for w in 1 2 3 4 5 6 7 8; do ( while [ $$(date +%s) -lt $$end ]; do \
	    wget -qO- --header="Authorization: Bearer $$TOKEN" --header="Content-Type: application/json" \
	      --header="Accept: application/json, text/event-stream" \
	      --post-data="{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"whoami\",\"arguments\":{}}}" \
	      http://exchange-mcp:8000/mcp >/dev/null; done ) & done; wait'

down:
	k3d cluster delete $(CLUSTER)
