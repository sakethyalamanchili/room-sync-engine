#!/usr/bin/env bash
# quick check that the running stack works. exits non-zero on the first failure.
# SKIP_DOCKER_CHECKS=1 skips the prometheus/grafana part

set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
PROM_URL="${PROM_URL:-http://localhost:9090}"
GRAFANA_URL="${GRAFANA_URL:-http://localhost:3000}"
PYTHON="${PYTHON:-$(command -v python3 >/dev/null 2>&1 && echo python3 || echo python)}"

PASS=0
step() { printf '\n[%s] %s\n' "$1" "$2"; }
ok() { PASS=$((PASS + 1)); printf '  OK  %s\n' "$1"; }
fail() { printf '  FAIL %s\n' "$1" >&2; exit 1; }

json() { "$PYTHON" -c "import json,sys; d=json.load(sys.stdin); print($1)" | tr -d '\r'; }

# sets CODE, BODY, HEADERS
http() {
  local method="$1" url="$2" body="${3:-}" header="${4:-}"
  local hfile bfile
  hfile="$(mktemp)"; bfile="$(mktemp)"
  local args=(-sS -X "$method" -o "$bfile" -D "$hfile" -w '%{http_code}' -H 'Content-Type: application/json')
  [[ -n "$header" ]] && args+=(-H "$header")
  [[ -n "$body" ]] && args+=(--data "$body")
  CODE="$(curl "${args[@]}" "$url")" || CODE="000"
  BODY="$(cat "$bfile")"
  HEADERS="$(cat "$hfile")"
  rm -f "$hfile" "$bfile"
}

metric() {
  # metric 'name{labels}' -> value (0 if missing)
  curl -sS "$API_URL/metrics" | grep -F "$1 " | awk '{print $2}' | head -n1 | sed 's/^$/0/' || echo 0
}

uuid() { "$PYTHON" -c "import uuid; print(uuid.uuid4())" | tr -d '\r'; }

# tomorrow (NY time) at a random afternoon hour so reruns don't collide. tr is for windows line endings
read -r TOMORROW START END CONFLICT_START CONFLICT_END < <("$PYTHON" - <<'PY' | tr -d '\r'
import random
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("America/New_York")
except Exception:  # no tzdata available: fixed EST offset is fine for a smoke test
    tz = timezone(timedelta(hours=-5))
d = (datetime.now(tz) + timedelta(days=1)).date()
hour = random.randint(12, 16)
start = datetime(d.year, d.month, d.day, hour, 0, tzinfo=tz).astimezone(timezone.utc)
end = start + timedelta(minutes=60)
cs, ce = start + timedelta(minutes=30), end + timedelta(minutes=30)
f = lambda x: x.strftime("%Y-%m-%dT%H:%M:%SZ")
print(d.isoformat(), f(start), f(end), f(cs), f(ce))
PY
)

echo "RoomSync smoke test against $API_URL (date $TOMORROW, slot $START to $END)"

step 1 "GET /health"
http GET "$API_URL/health"
[[ "$CODE" == 200 ]] || fail "/health returned $CODE: $BODY"
[[ "$(echo "$BODY" | json 'd["status"]')" == ok ]] || fail "/health status not ok"
ok "/health is ok"

step 2 "GET /api/v1/rooms"
http GET "$API_URL/api/v1/rooms"
[[ "$CODE" == 200 ]] || fail "rooms returned $CODE"
ROOM_COUNT="$(echo "$BODY" | json 'len(d)')"
[[ "$ROOM_COUNT" == 12 ]] || fail "expected 12 rooms, got $ROOM_COUNT"
ROOM_ID="$(echo "$BODY" | "$PYTHON" -c "import json,sys,random; print(random.choice(json.load(sys.stdin))['id'])")"
ok "12 rooms returned"

step 3 "GET availability twice, second from cache"
http GET "$API_URL/api/v1/rooms/availability?date=$TOMORROW"
[[ "$CODE" == 200 ]] || fail "availability returned $CODE"
[[ "$(echo "$BODY" | json 'len(d["rooms"])')" == 12 ]] || fail "availability did not contain 12 rooms"
HITS_BEFORE="$(metric 'redis_cache_operations_total{operation="hit"}')"
http GET "$API_URL/api/v1/rooms/availability?date=$TOMORROW"
[[ "$CODE" == 200 ]] || fail "second availability returned $CODE"
HITS_AFTER="$(metric 'redis_cache_operations_total{operation="hit"}')"
"$PYTHON" -c "import sys; sys.exit(0 if float('$HITS_AFTER') > float('$HITS_BEFORE') else 1)" \
  || fail "cache hit counter did not increase ($HITS_BEFORE -> $HITS_AFTER)"
ok "12 rooms, cache hit counter $HITS_BEFORE -> $HITS_AFTER"

step 4 "POST booking, idempotent replay, conflict"
KEY="$(uuid)"
PAYLOAD="{\"room_id\":\"$ROOM_ID\",\"organizer_name\":\"Smoke Test\",\"organizer_email\":\"smoke@example.com\",\"title\":\"Smoke test\",\"start_time\":\"$START\",\"end_time\":\"$END\"}"
http POST "$API_URL/api/v1/bookings" "$PAYLOAD" "Idempotency-Key: $KEY"
[[ "$CODE" == 201 ]] || fail "create returned $CODE: $BODY"
BOOKING_ID="$(echo "$BODY" | json 'd["id"]')"
ok "created booking $BOOKING_ID"

http POST "$API_URL/api/v1/bookings" "$PAYLOAD" "Idempotency-Key: $KEY"
[[ "$CODE" == 201 ]] || fail "replay returned $CODE: $BODY"
[[ "$(echo "$BODY" | json 'd["id"]')" == "$BOOKING_ID" ]] || fail "replay returned a different id"
echo "$HEADERS" | grep -qi '^idempotent-replay: true' || fail "replay missing Idempotent-Replay header"
ok "replay returned same id with Idempotent-Replay: true"

CONFLICT="{\"room_id\":\"$ROOM_ID\",\"organizer_name\":\"Other\",\"organizer_email\":\"other@example.com\",\"title\":\"Clash\",\"start_time\":\"$CONFLICT_START\",\"end_time\":\"$CONFLICT_END\"}"
http POST "$API_URL/api/v1/bookings" "$CONFLICT" "Idempotency-Key: $(uuid)"
[[ "$CODE" == 409 ]] || fail "conflicting booking returned $CODE: $BODY"
ok "overlapping booking rejected with 409"

step 5 "DELETE booking and rebook"
http DELETE "$API_URL/api/v1/bookings/$BOOKING_ID"
[[ "$CODE" == 200 ]] || fail "delete returned $CODE"
[[ "$(echo "$BODY" | json 'd["status"]')" == CANCELLED ]] || fail "booking not CANCELLED"
http POST "$API_URL/api/v1/bookings" "$PAYLOAD" "Idempotency-Key: $(uuid)"
[[ "$CODE" == 201 ]] || fail "rebook returned $CODE: $BODY"
REBOOK_ID="$(echo "$BODY" | json 'd["id"]')"
ok "cancelled, then rebooked as $REBOOK_ID"
http DELETE "$API_URL/api/v1/bookings/$REBOOK_ID" >/dev/null
[[ "$CODE" == 200 ]] || fail "cleanup delete returned $CODE"

step 6 "/metrics exposes custom metrics"
METRICS="$(curl -sS "$API_URL/metrics")"
for m in booking_requests_total redis_cache_operations_total db_pool_checked_out_connections active_websocket_clients; do
  echo "$METRICS" | grep -q "^$m" || fail "/metrics missing $m"
  ok "$m present"
done

if [[ "${SKIP_DOCKER_CHECKS:-0}" == 1 ]]; then
  step 7 "SKIPPED (SKIP_DOCKER_CHECKS=1): Prometheus and Grafana not checked"
else
  step 7 "Prometheus target up and Grafana dashboard provisioned"
  UP=""
  for _ in $(seq 1 15); do
    UP="$(curl -sS "$PROM_URL/api/v1/targets" | "$PYTHON" -c "
import json,sys
d=json.load(sys.stdin)
print(next((t['health'] for t in d['data']['activeTargets'] if t['labels'].get('job')=='roomsync-backend'), 'missing'))
" 2>/dev/null || echo error)"
    [[ "$UP" == up ]] && break
    sleep 2
  done
  [[ "$UP" == up ]] || fail "Prometheus target roomsync-backend is '$UP'"
  ok "Prometheus target roomsync-backend is up"

  http GET "$GRAFANA_URL/api/dashboards/uid/roomsync"
  [[ "$CODE" == 200 ]] || fail "Grafana dashboard uid roomsync returned $CODE"
  PANELS="$(echo "$BODY" | json 'len(d["dashboard"]["panels"])')"
  [[ "$PANELS" == 4 ]] || fail "dashboard has $PANELS panels, expected 4"
  ok "Grafana dashboard 'roomsync' has 4 panels"
fi

printf '\nSMOKE TEST PASSED (%d checks)\n' "$PASS"
