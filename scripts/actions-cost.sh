#!/usr/bin/env bash
# GitHub Actions billing audit — billable minutes reconstructed from job timestamps.
#
# Usage: scripts/actions-cost.sh [owner/repo] [run-limit]
#
# Why not the timing endpoint: /repos/{o}/{r}/actions/runs/{id}/timing returns
# 0ms on this private repo (verified 2026-08-14) — never use it. Billable
# minutes are computed the way GitHub bills: per-job wall clock from started_at
# to completed_at, rounded UP to whole minutes PER JOB, x10 on macOS runners.
# Jobs with zero steps (quota-blocked "failures") are skipped — they never
# started and bill nothing. Jobs still IN PROGRESS (completed_at null) are
# also skipped: an audit run mid-build undercounts by those jobs.
# Requires a real `jq` binary (not gh's built-in engine) for cross-page sums.
set -euo pipefail

REPO="${1:-inputoutput9/iofast}"
LIMIT="${2:-200}"

# Capture first: a failing `gh run list` inside process substitution escapes
# set -e, and a silent "TOTAL: 0" is the worst output a billing audit can give.
runs=$(gh run list -R "$REPO" --limit "$LIMIT" --json databaseId,workflowName \
         -q '.[] | [.databaseId, .workflowName] | @tsv')
test -n "$runs" || { echo "no runs returned for $REPO — refusing to report 0" >&2; exit 1; }

total=0
count=0
while IFS=$'\t' read -r run_id wf; do
  # NOTE: gh api --paginate with -q applies jq PER PAGE (jobs page at 30), so a
  # >30-job run would yield one number per page. Aggregate across pages with -s.
  mins=$(gh api "/repos/$REPO/actions/runs/$run_id/jobs" --paginate </dev/null | jq -s '
    [.[] | .jobs[]
     | select(.started_at and .completed_at and ((.steps | length) > 0))
     | (((.completed_at | fromdateiso8601) - (.started_at | fromdateiso8601)) / 60 | ceil)
       * (if ((.labels // []) | join(",") | test("macos"; "i")) then 10 else 1 end)]
    | add // 0')
  printf '%6s min  run %-12s %s\n' "$mins" "$run_id" "$wf"
  total=$((total + mins))
  count=$((count + 1))
done <<< "$runs"

printf 'TOTAL billable minutes (%s runs audited): %s\n' "$count" "$total"
