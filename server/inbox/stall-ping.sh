#!/bin/bash
# stall-ping.sh: Telegram ping when phone captures sit unfiled in the iofast inbox.
# Cron on the droplet, hourly. If the oldest pending link is older than STALL_HOURS (default 3),
# sends one short message, then stays quiet for QUIET_HOURS (default 12) unless the count grows by 10+.
# Reuses the recon bot from duet-ping.env (never printed). Missing env or inbox -> exits quietly.
set -uo pipefail
BASE=${AGENTOS_BASE:-/opt/agentos}
INBOX=${INBOX:-$BASE/iofast-inbox/data/inbox.json}
PING_ENV=${PING_ENV:-$BASE/secrets/duet-ping.env}
STATE=${STATE:-$BASE/iofast-inbox/data/stall-ping.state}
STALL_HOURS=${STALL_HOURS:-3}
QUIET_HOURS=${QUIET_HOURS:-12}
API=${PING_API:-https://api.telegram.org}
[ -r "$INBOX" ] && [ -r "$PING_ENV" ] || exit 0
set -a; . "$PING_ENV"; set +a
[ -n "${RECON_TELEGRAM_TOKEN:-}" ] && [ -n "${RECON_TELEGRAM_CHAT_ID:-}" ] || exit 0
read -r count oldest < <(jq -r '[.links[] | select(.done_at == null)] | "\(length) \(map(.received_at) | min // "")"' "$INBOX")
[ "${count:-0}" -gt 0 ] && [ -n "$oldest" ] || { rm -f "$STATE"; exit 0; }
now=$(date -u +%s); old_s=$(date -u -d "$oldest" +%s 2>/dev/null || echo "$now")
age_h=$(( (now - old_s) / 3600 ))
[ "$age_h" -ge "$STALL_HOURS" ] || exit 0
last_t=0; last_n=0; [ -f "$STATE" ] && read -r last_t last_n < "$STATE"
if [ $(( now - last_t )) -lt $(( QUIET_HOURS * 3600 )) ] && [ "$count" -lt $(( last_n + 10 )) ]; then exit 0; fi
msg="iofast: ${count} phone captures are waiting (oldest ${age_h} h). The PC app is not filing them. Turn on the PC, or open iofast from the Start menu."
if curl -s -m 15 -o /dev/null -w '%{http_code}' -X POST "$API/bot${RECON_TELEGRAM_TOKEN}/sendMessage" \
     --data-urlencode "chat_id=${RECON_TELEGRAM_CHAT_ID}" --data-urlencode "text=${msg}" | grep -q '^200$'; then
  echo "$now $count" > "$STATE"; echo "stall-ping: sent ($count pending, ${age_h} h)"
fi
exit 0
