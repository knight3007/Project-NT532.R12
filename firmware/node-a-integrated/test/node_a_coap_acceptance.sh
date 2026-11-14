#!/usr/bin/env bash
# Run on the Raspberry Pi only after wiring, voltage, and mechanical clearance checks.
set -euo pipefail

usage() {
  echo "Usage: $0 --eid NODE_A_MESH_LOCAL_EID --allow-outputs [--watchdog-only]" >&2
  exit 2
}

eid=''
allow_outputs=false
watchdog_only=false
while (($#)); do
  case "$1" in
    --eid) (($# >= 2)) || usage; eid="$2"; shift 2 ;;
    --allow-outputs) allow_outputs=true; shift ;;
    --watchdog-only) watchdog_only=true; shift ;;
    *) usage ;;
  esac
done
[[ -n "$eid" && "$allow_outputs" == true ]] || usage
command -v coap-client-notls >/dev/null || { echo 'coap-client-notls is required' >&2; exit 2; }
command -v jq >/dev/null || { echo 'jq is required' >&2; exit 2; }
base="coap://[$eid]"
hb_pid=''

post() { coap-client-notls -m post -e "$2" "$base/$1"; }
get() { coap-client-notls -m get "$base/$1"; }
stop_heartbeat() {
  if [[ -n "$hb_pid" ]]; then
    kill "$hb_pid" 2>/dev/null || true
    wait "$hb_pid" 2>/dev/null || true
    hb_pid=''
  fi
}
cleanup() {
  trap - EXIT INT TERM
  stop_heartbeat
  post laser OFF >/dev/null 2>&1 || true
  post stop '' >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
heartbeat_loop() {
  while :; do
    post hb '' >/dev/null 2>&1 || true
    sleep 0.4
  done
}

echo 'GET /info'
info=$(get info)
echo "$info" | jq -e '.n == "A" and .fw == "a-integrated-0.1.2" and .sensor == "mq2"' >/dev/null
echo "$info" | jq .
echo 'GET /status'
get status | jq .
echo 'GET /mq2'
get mq2 | jq -e '.sensor == "mq2" and has("alarm") and has("adc_raw")' >/dev/null

heartbeat_loop & hb_pid=$!
sleep 0.5
post pantilt ARM
post pantilt HOME
sleep 0.5

if [[ "$watchdog_only" == true ]]; then
  stop_heartbeat
  sleep 2
  status=$(get status)
  echo "$status" | jq -e '.stopped == true and .servo_pwm == false and .relay_on == false and .hb_fresh == false' >/dev/null
  echo "$status" | jq .
else
  post pantilt '1540,1700'; sleep 1
  post pantilt '1500,1700'; sleep 1
  post pantilt '1500,1740'; sleep 1
  post pantilt '1500,1700'; sleep 1
  get laser | jq -e '.on == false' >/dev/null
  post laser OFF
  get laser | jq -e '.on == false' >/dev/null
  post stop ''
  get status | jq -e '.stopped == true and .servo_pwm == false and .relay_on == false' >/dev/null
fi
echo 'CoAP checks completed; cleanup sends OFF and STOP.'
