#!/usr/bin/env bash
# scripts/test.sh — Smoke test completo del runtime
set -e

BASE="http://localhost:8080"
SKU="${1:-SKU-001}"

echo "=== Health check ==="
curl -sf "$BASE/health" | python3 -m json.tool

echo ""
echo "=== Spawn reposition para $SKU ==="
SPAWN=$(curl -sf -X POST "$BASE/spawn" \
  -H "Content-Type: application/json" \
  -d "{\"agent\":\"reposition\",\"input\":{\"sku\":\"$SKU\"}}")
echo "$SPAWN" | python3 -m json.tool

INSTANCE_ID=$(echo "$SPAWN" | python3 -c "import sys,json; print(json.load(sys.stdin)['instance_id'])")
echo ""
echo "=== Polling instance $INSTANCE_ID ==="

for i in $(seq 1 30); do
  sleep 2
  STATUS=$(curl -sf "$BASE/instances/$INSTANCE_ID" | python3 -c "
import sys, json
r = json.load(sys.stdin)
print(r.get('status','?'))
")
  echo "  intento $i: status=$STATUS"
  if [ "$STATUS" = "ok" ] || [ "$STATUS" = "error" ]; then
    break
  fi
done

echo ""
echo "=== Decisión estructurada (backend-integrable) ==="
curl -sf "$BASE/instances/$INSTANCE_ID/result" | python3 -m json.tool || \
  echo "(pendiente o error — ver /instances/$INSTANCE_ID)"

echo ""
echo "=== Vista completa de la instancia ==="
curl -sf "$BASE/instances/$INSTANCE_ID" | python3 -m json.tool

echo ""
echo "=== Todas las decisiones ==="
curl -sf "$BASE/decisions" | python3 -m json.tool
