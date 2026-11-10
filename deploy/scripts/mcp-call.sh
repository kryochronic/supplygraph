#!/usr/bin/env bash
# Call the MCP server with plain curl: one JSON-RPC request per HTTP POST, no session.
#   deploy/scripts/mcp-call.sh tools/list
#   deploy/scripts/mcp-call.sh tools/call '{"name":"whoami","arguments":{}}'
#   deploy/scripts/mcp-call.sh tools/call '{"name":"get_constituents","arguments":{"basket_id":"nifty50"}}'
# Reads the token from $TOKEN; without one you will see the 401 and its WWW-Authenticate header.
set -euo pipefail
URL=${MCP_URL:-https://mcp.localtest.me:8443/mcp}
METHOD=$1
PARAMS=${2:-'{}'}
# curl on macOS does not read the keychain; trust mkcert's CA explicitly when it exists.
CA=${CA:-$(mkcert -CAROOT 2>/dev/null)/rootCA.pem}
CURL=(curl); [ -f "$CA" ] && CURL=(curl --cacert "$CA")
"${CURL[@]}" -sS -i "$URL" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  ${TOKEN:+-H "Authorization: Bearer $TOKEN"} \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"$METHOD\",\"params\":$PARAMS}"
echo
