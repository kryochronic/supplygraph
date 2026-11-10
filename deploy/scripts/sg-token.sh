#!/usr/bin/env bash
# Get an access token with the OAuth 2.0 Device Authorization Grant (RFC 8628), then
# print it. Use it to poke the MCP server with curl and see each auth step yourself.
#
#   TOKEN=$(deploy/scripts/sg-token.sh)               # exchange:read
#   TOKEN=$(deploy/scripts/sg-token.sh exchange:admin) # also ask for admin
#
# Needs curl and jq. Prompts go to stderr so only the token reaches stdout.
set -euo pipefail

ISSUER=${ISSUER:-https://auth.localtest.me:8443/realms/supplygraph}
CLIENT_ID=${CLIENT_ID:-sg-cli}
SCOPE="openid exchange:read ${1:-}"
# curl on macOS does not read the keychain; trust mkcert's CA explicitly when it exists.
CA=${CA:-$(mkcert -CAROOT 2>/dev/null)/rootCA.pem}
CURL=(curl); [ -f "$CA" ] && CURL=(curl --cacert "$CA")

dev=$("${CURL[@]}" -fsS "$ISSUER/protocol/openid-connect/auth/device" -d client_id="$CLIENT_ID" --data-urlencode scope="$SCOPE")
echo "Open: $(jq -r .verification_uri_complete <<<"$dev")" >&2
echo "(code $(jq -r .user_code <<<"$dev"); waiting for you to sign in...)" >&2

code=$(jq -r .device_code <<<"$dev")
interval=$(jq -r '.interval // 5' <<<"$dev")
while :; do
  sleep "$interval"
  res=$("${CURL[@]}" -sS "$ISSUER/protocol/openid-connect/token" -d client_id="$CLIENT_ID" \
    -d grant_type=urn:ietf:params:oauth:grant-type:device_code -d device_code="$code")
  case $(jq -r '.error // "ok"' <<<"$res") in
    ok) jq -r .access_token <<<"$res"; exit 0 ;;
    authorization_pending) ;;
    slow_down) interval=$((interval + 5)) ;;
    *) echo "$res" >&2; exit 1 ;;
  esac
done
