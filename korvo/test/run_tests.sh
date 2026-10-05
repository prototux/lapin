#!/usr/bin/env bash
# Host tests of the firmware's portable core. With a running server
# (SERVER_WS, ADMIN), also fetches the real wake word recordings through the
# protocol with a temporary test device, runs the KWS tests on them and
# deletes the device afterwards.
set -uo pipefail
cd "$(dirname "$0")"
SERVER_WS=${SERVER_WS:-ws://localhost:8765/v1/device}
ADMIN=${ADMIN:-http://localhost:8090}
WORK=$(mktemp -d)
trap '[ -n "${KEEP:-}" ] || rm -rf "$WORK"' EXIT
fail=0

make -s all ref_trace || exit 1
echo "== mixer";  ./test_mixer 2>/dev/null | tail -1 || fail=1
./test_mixer >/dev/null 2>&1 || fail=1
echo "== protocol / state machine"; ./test_proto 2>/dev/null | tail -1
./test_proto >/dev/null 2>&1 || fail=1
echo "== device log report"; ./test_logbuf | tail -1 || fail=1
echo "== firmware update over the WebSocket"; ./test_ota 2>/dev/null | tail -1
./test_ota >/dev/null 2>&1 || fail=1

if ! curl -sf "$ADMIN/api/devices" >/dev/null; then
    echo "== server not reachable at $ADMIN: skipping the protocol and KWS tests"
    exit $fail
fi
[ -x .venv/bin/python ] || { python3 -m venv .venv && .venv/bin/pip -q install websockets; }
ID="test-korvo-$(date +%s)"
echo "== live server: device $ID fetches the wake word recordings through the protocol"
( sleep 2; curl -s -X POST "$ADMIN/api/devices/$ID/approve" -H 'content-type: application/json' \
    -d '{"approved": true}' >/dev/null ) &
.venv/bin/python ws_bridge.py --timeout 90 --dump "$WORK/tpl" "$SERVER_WS" -- \
    ./hostsat -i "$ID" -s "$WORK/kws.bin" -a 1 -d 8 -l 2 "$WORK/first.wav" >"$WORK/live.log" 2>&1 &
BR=$!
# the clip to say: the first recording, as soon as it is dumped
for i in $(seq 1 60); do
    f=$(ls "$WORK"/tpl/*.wav 2>/dev/null | sort | sed -n 3p)
    [ -n "$f" ] && { cp "$f" "$WORK/first.wav"; break; }
    sleep 0.5
done
wait $BR; rc=$?
[ -n "${KEEP:-}" ] && echo "work dir $WORK"
grep -E "templates:|^== |kws: |wake:|<- \{\"type\": \"(wake_ack|eot|cancel|transcript)" "$WORK/live.log" | cut -c1-160
grep -q '"type":"wake"' "$WORK/live.log" && echo "  ok    live: the C engine woke on the replayed recording and the server answered" \
    || { echo "  FAIL  live wake"; fail=1; }
curl -s -X DELETE "$ADMIN/api/devices/$ID" >/dev/null && echo "  (test device $ID deleted)"

echo "== KWS on the fetched recordings"
./test_kws "$WORK/tpl" 2>/dev/null | grep -vE "^I: " || fail=1
exit $fail
