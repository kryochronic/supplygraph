#!/usr/bin/env bash
# Show a JWT's header and claims (no signature check: this is for reading, not trusting).
#   deploy/scripts/jwt-decode.sh "$TOKEN"
set -euo pipefail
b64() { local s=${1//-/+}; s=${s//_//}; while (( ${#s} % 4 )); do s+="="; done; base64 -d <<<"$s" 2>/dev/null; }
IFS=. read -r h p _ <<<"$1"
echo "header:"; b64 "$h" | jq .
echo "claims:"; b64 "$p" | jq '. + {exp_in_s: (.exp - now | floor)}'
