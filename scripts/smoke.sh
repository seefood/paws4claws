#!/bin/sh
# Manual smoke test — run against a live daemon.
# Usage: PAWS_TOKEN=<token> PAWS_URL=http://localhost:7142 ./scripts/smoke.sh
# Requires the aws wrapper to be in PATH (or call wrapper/aws directly).

set -e

: "${PAWS_TOKEN:?PAWS_TOKEN must be set}"
PAWS_URL="${PAWS_URL:-http://localhost:7142}"

echo "=== Health check (no auth) ==="
curl -sf "$PAWS_URL/health"
echo ""

echo ""
echo "=== sts get-caller-identity ==="
aws sts get-caller-identity | jq .

echo ""
echo "=== Blocked service (expect error message) ==="
RESPONSE=$(curl -s \
	-H "Authorization: Bearer $PAWS_TOKEN" \
	-H "Content-Type: application/json" \
	-d '{"args": ["kms", "list-keys"]}' \
	"$PAWS_URL/invoke")
echo "$RESPONSE" | jq .

echo ""
echo "=== Local file copy without files payload (expect 501) ==="
RESPONSE=$(curl -s \
	-H "Authorization: Bearer $PAWS_TOKEN" \
	-H "Content-Type: application/json" \
	-d '{"args": ["s3", "cp", "./missing-local", "s3://bucket/key"]}' \
	"$PAWS_URL/invoke")
echo "$RESPONSE" | jq .

echo ""
echo "=== v0.3 file upload (expect AWS error without real bucket) ==="
FILE_B64=$(printf 'smoke-file-bytes' | base64 | tr -d '\n')
RESPONSE=$(curl -s \
	-H "Authorization: Bearer $PAWS_TOKEN" \
	-H "Content-Type: application/json" \
	-d "{\"args\": [\"s3\", \"cp\", \"./smoke.bin\", \"s3://bucket/smoke-key\"], \"files\": [{\"argIndex\": 2, \"content\": \"$FILE_B64\"}]}" \
	"$PAWS_URL/invoke")
echo "$RESPONSE" | jq .

echo ""
echo "=== Stdin upload (expect AWS error without real bucket) ==="
STDIN_B64=$(printf 'smoke-test-payload' | base64 | tr -d '\n')
RESPONSE=$(curl -s \
	-H "Authorization: Bearer $PAWS_TOKEN" \
	-H "Content-Type: application/json" \
	-d "{\"args\": [\"s3\", \"cp\", \"-\", \"s3://bucket/smoke-test-key\"], \"stdin\": \"$STDIN_B64\"}" \
	"$PAWS_URL/invoke")
echo "$RESPONSE" | jq .

echo ""
echo "All smoke checks complete."
