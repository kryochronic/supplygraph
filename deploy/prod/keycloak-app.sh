#!/usr/bin/env bash
# Configure the `supplygraph` realm for the hosted app (docker-compose-prod.yml) through kcadm.sh.
# Idempotent: creates what is missing and re-applies settings, so `make prod-bootstrap` is safe to repeat.
#
# What it sets up:
#   realm      supplygraph: no self-registration, brute-force protection, HTTPS for everyone outside the VM
#   role       sg-user: oauth2-proxy lets in only users holding it
#   user       $SG_USER (you), with that role and the password from prod.env
#   client     supplygraph-web: confidential, authorization code + PKCE, redirect to https://$SG_HOST/oauth2/callback
#
# The Keycloak image is UBI-micro: no awk, curl, jq or python. Stick to bash, sed and grep.
set -euo pipefail

KC=${KC_URL:-http://keycloak:8080}
R=supplygraph
k() { "${KCADM:-/opt/keycloak/bin/kcadm.sh}" "$@"; }

echo "waiting for keycloak..."
until k config credentials --server "$KC" --realm master --user "${KEYCLOAK_ADMIN_USER:-admin}" --password "$KEYCLOAK_ADMIN_PASSWORD" >/dev/null 2>&1; do
  sleep 5
done

# --- realm --------------------------------------------------------------------------
k get "realms/$R" >/dev/null 2>&1 || k create realms -s realm="$R" -s enabled=true
k update "realms/$R" -s sslRequired=external -s registrationAllowed=false -s resetPasswordAllowed=false \
  -s loginWithEmailAllowed=true -s bruteForceProtected=true -s failureFactor=10 \
  -s accessTokenLifespan=300 -s ssoSessionIdleTimeout=86400 -s ssoSessionMaxLifespan=604800 \
  -s displayName=SupplyGraph

# --- role and user ------------------------------------------------------------------------
k get roles/sg-user -r "$R" >/dev/null 2>&1 || k create roles -r "$R" -s name=sg-user -s description="May use the SupplyGraph app"
if [ -z "$(k get users -r "$R" -q username="$SG_USER" -q exact=true --fields id --format csv --noquotes)" ]; then
  k create users -r "$R" -s username="$SG_USER" -s enabled=true -s email="$SG_USER_EMAIL" -s emailVerified=true \
    -s firstName="$SG_USER_FIRST" -s lastName="$SG_USER_LAST"
  k set-password -r "$R" --username "$SG_USER" --new-password "$SG_USER_PASSWORD"
fi
# Password is set only when the user is created: change it in Keycloak afterwards and a re-run keeps yours.
k add-roles -r "$R" --uusername "$SG_USER" --rolename sg-user

# --- the app's client (oauth2-proxy) --------------------------------------------------------
CID=$(k get clients -r "$R" -q clientId=supplygraph-web --fields id --format csv --noquotes)
if [ -z "$CID" ]; then
  CID=$(k create clients -r "$R" -i -s clientId=supplygraph-web -s name="SupplyGraph web" -s enabled=true \
    -s publicClient=false -s clientAuthenticatorType=client-secret)
fi
k update "clients/$CID" -r "$R" -s secret="$OIDC_CLIENT_SECRET" \
  -s standardFlowEnabled=true -s directAccessGrantsEnabled=false -s implicitFlowEnabled=false \
  -s serviceAccountsEnabled=false -s frontchannelLogout=true \
  -s "redirectUris=[\"https://$SG_HOST/oauth2/callback\"]" -s "webOrigins=[\"https://$SG_HOST\"]" \
  -s "attributes.\"post.logout.redirect.uris\"=https://$SG_HOST/*" \
  -s 'attributes."pkce.code.challenge.method"=S256'

# Realm roles in the token, where oauth2-proxy's keycloak-oidc provider reads them (realm_access.roles).
# Keycloak's default `roles` scope already does this; the audience mapper makes `aud` carry the client id,
# which oauth2-proxy checks on the access token.
if ! k get "clients/$CID/protocol-mappers/models" -r "$R" --fields name --format csv --noquotes | grep -qx aud-supplygraph-web; then
  k create "clients/$CID/protocol-mappers/models" -r "$R" -s name=aud-supplygraph-web -s protocol=openid-connect \
    -s protocolMapper=oidc-audience-mapper -s 'config."included.client.audience"=supplygraph-web' \
    -s 'config."access.token.claim"=true' -s 'config."id.token.claim"=true'
fi

echo "realm $R ready: https://$AUTH_HOST/realms/$R, client supplygraph-web, user $SG_USER"
