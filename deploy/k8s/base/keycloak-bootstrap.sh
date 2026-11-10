#!/usr/bin/env bash
# Configure the `supplygraph` realm through Keycloak's admin API (kcadm.sh).
# Idempotent: safe to run again; it creates what is missing and re-applies settings.
#
# Every line here is something you could click in the admin console. It is a script so the
# setup is reviewable, repeatable and identical on every cluster. Read it alongside
# docs/mcp/README.md, Stage 2.
set -euo pipefail
# The Keycloak image is UBI-micro: no awk, curl, jq or python. Stick to bash, sed and grep.

KC=${KC_URL:-http://keycloak:8080}
R=supplygraph
k() { "${KCADM:-/opt/keycloak/bin/kcadm.sh}" "$@"; }

echo "waiting for keycloak..."
until k config credentials --server "$KC" --realm master --user "$KC_ADMIN_USER" --password "$KC_ADMIN_PASSWORD" >/dev/null 2>&1; do
  sleep 5
done

# --- realm --------------------------------------------------------------------------
if ! k get "realms/$R" >/dev/null 2>&1; then
  k create realms -s realm="$R" -s enabled=true
fi
# Short access tokens (5 min) limit the damage of a leaked token; clients refresh silently.
# sslRequired=external: HTTPS for every request from outside private networks.
k update "realms/$R" -s sslRequired="$SSL_REQUIRED" -s accessTokenLifespan=300 -s registrationAllowed=false

scope_id() { k get client-scopes -r "$R" --fields id,name --format csv --noquotes | sed -n "s/^\([^,]*\),$1\$/\1/p"; }

ensure_scope() { # name, description
  local id; id=$(scope_id "$1")
  if [ -z "$id" ]; then
    id=$(k create client-scopes -r "$R" -i -s name="$1" -s protocol=openid-connect -s description="$2" \
      -s 'attributes."include.in.token.scope"=true' -s 'attributes."display.on.consent.screen"=true' \
      -s "attributes.\"consent.screen.text\"=$2")
  fi
  echo "$id"
}

# --- scopes: what a token may do ------------------------------------------------------
READ_ID=$(ensure_scope "exchange:read" "Read index constituents and fund holdings")
ADMIN_ID=$(ensure_scope "exchange:admin" "Run source checks across all exchanges")

# Audience: tokens carrying exchange:read are addressed to the MCP server's resource URL.
# The MCP server rejects any token whose `aud` does not contain that URL, which is what
# stops a token minted for some other API being replayed against it.
if ! k get "client-scopes/$READ_ID/protocol-mappers/models" -r "$R" --fields name --format csv --noquotes | grep -qx mcp-audience; then
  k create "client-scopes/$READ_ID/protocol-mappers/models" -r "$R" -s name=mcp-audience \
    -s protocol=openid-connect -s protocolMapper=oidc-audience-mapper \
    -s "config.\"included.custom.audience\"=$MCP_RESOURCE_URL" \
    -s 'config."access.token.claim"=true' -s 'config."id.token.claim"=false'
fi

# exchange:read is a realm *default* scope: every client, including ones that register
# themselves (DCR), gets it. exchange:admin is *optional*: a client must ask for it.
has_default() { k get "realms/$R/default-$1-client-scopes" --fields name --format csv --noquotes | grep -qx "$2"; }
has_default default exchange:read || k update "realms/$R/default-default-client-scopes/$READ_ID"
has_default optional exchange:admin || k update "realms/$R/default-optional-client-scopes/$ADMIN_ID"

# exchange:admin is only granted to users holding the exchange-admin realm role:
# a client scope with role scope mappings is applied only for users with one of those roles.
k get roles/exchange-admin -r "$R" >/dev/null 2>&1 || k create roles -r "$R" -s name=exchange-admin
if ! k get "client-scopes/$ADMIN_ID/scope-mappings/realm" -r "$R" --fields name --format csv --noquotes | grep -qx exchange-admin; then
  echo "[$(k get roles/exchange-admin -r "$R")]" | k create "client-scopes/$ADMIN_ID/scope-mappings/realm" -r "$R" -f -
fi

# --- the user (you) -------------------------------------------------------------------
if [ -z "$(k get users -r "$R" -q username="$SG_USER" --fields id --format csv --noquotes)" ]; then
  k create users -r "$R" -s username="$SG_USER" -s enabled=true -s email="$SG_USER_EMAIL" -s emailVerified=true \
    -s firstName="$SG_USER_FIRST" -s lastName="$SG_USER_LAST"
fi
k set-password -r "$R" --username "$SG_USER" --new-password "$SG_USER_PASSWORD"
k add-roles -r "$R" --uusername "$SG_USER" --rolename exchange-admin

# --- a pre-registered public client for the command line (device flow) ---------------
# For scripts/curl without a browser on the same machine: OAuth device authorization grant.
if [ -z "$(k get clients -r "$R" -q clientId=sg-cli --fields id --format csv --noquotes)" ]; then
  k create clients -r "$R" -s clientId=sg-cli -s name="SupplyGraph CLI" -s publicClient=true \
    -s standardFlowEnabled=false -s directAccessGrantsEnabled=false \
    -s 'attributes."oauth2.device.authorization.grant.enabled"=true'
fi

# --- Dynamic Client Registration (MCP clients registering themselves) ----------------
# Claude Code, Claude Desktop and MCP Inspector discover Keycloak from the MCP server's
# protected-resource metadata and register a client on the fly (RFC 7591). Keycloak's
# anonymous-registration policies decide what such a client may be.
policy_id() {
  k get components -r "$R" -q type=org.keycloak.services.clientregistration.policy.ClientRegistrationPolicy \
    --fields id,name,subType --format csv --noquotes | sed -n "s/^\([^,]*\),$1,anonymous\$/\1/p"
}
TRUSTED=$(policy_id "Trusted Hosts")
# Redirect URIs must point at a trusted host (loopback: the native app on your machine).
# The registering request itself arrives through the ingress, so its source address is not checked.
k update "components/$TRUSTED" -r "$R" \
  -s "config.\"trusted-hosts\"=$DCR_TRUSTED_HOSTS" \
  -s 'config."host-sending-registration-request-must-match"=["false"]' \
  -s 'config."client-uris-must-match"=["true"]'
SCOPES=$(policy_id "Allowed Client Scopes")
k update "components/$SCOPES" -r "$R" \
  -s 'config."allowed-client-scopes"=["exchange:read","exchange:admin"]' \
  -s 'config."allow-default-scopes"=["true"]'

echo "realm $R ready; issuer ${KEYCLOAK_PUBLIC_URL}/realms/$R"
