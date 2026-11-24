#!/bin/sh
# Tests run.sh with fake uvicorn and scholarmind commands that log what they start, the TERMs
# they get and when they finish. Needs bash 4.3 or later for run.sh (wait -n):
#
#   sh deploy/run_test.sh                          # bash from PATH
#   BASH_BIN=/opt/homebrew/bin/bash sh deploy/run_test.sh
set -u
here=$(cd "$(dirname -- "$0")" && pwd)
bash=${BASH_BIN:-bash}
tmp=$(mktemp -d "${TMPDIR:-/tmp}/run-test.XXXXXX")
trap 'pkill -f "$tmp/bin/" 2>/dev/null; rm -rf "$tmp"' EXIT
failures=0
fail() {
	echo "FAIL [$case_name]: $*"
	failures=$((failures + 1))
}

# A fake process: logs "start <cmd>", exits 3 after half a second when FAIL names it, and on
# TERM logs "term <cmd>" and finishes 0.3 s later with "end <cmd>", as an RQ worker
# finishing its job would.
mkdir "$tmp/bin"
cat >"$tmp/bin/fake" <<'EOF'
#!/bin/sh
name="$(basename "$0") $*"
echo "start $name" >>"$LOG"
left=
trap 'echo "term $name" >>"$LOG"; left=${left:-3}' TERM
i=0
while :; do
	sleep 0.1
	i=$((i + 1))
	[ "$name" != "${FAIL-}" ] || [ "$i" -lt 5 ] || exit 3
	if [ -n "$left" ]; then
		left=$((left - 1))
		[ "$left" -gt 0 ] || { echo "end $name" >>"$LOG"; exit 0; }
	fi
done
EOF
chmod +x "$tmp/bin/fake"
ln -s fake "$tmp/bin/uvicorn"
ln -s fake "$tmp/bin/scholarmind"

# run CASE PART [STOP]: starts run.sh PART, sends it TERM after a second when STOP is set (as
# docker stop does, to it alone), and waits up to 10 s for it. Sets code; $LOG.exit is the
# log as it was when run.sh exited.
run() {
	case_name=$1
	LOG=$tmp/$1.log
	: >"$LOG"
	PATH="$tmp/bin:$PATH" LOG=$LOG "$bash" "$here/run.sh" $2 &
	pid=$!
	[ -z "${3-}" ] || { sleep 1; kill -TERM "$pid"; }
	i=0
	while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 100 ]; do sleep 0.1; i=$((i + 1)); done
	if kill -0 "$pid" 2>/dev/null; then
		fail "run.sh still running after 10 s"
		kill -KILL "$pid"
	fi
	wait "$pid"
	code=$?
	cp "$LOG" "$LOG.exit"
	sleep 0.5 # anything still running writes its last lines
	pkill -f "$tmp/bin/" 2>/dev/null
}

count() { grep -c -- "^$1\$" "${2:-$LOG}"; }

# each PROCESS...: every one started; every instance but the failed one got a TERM, once,
# and had finished before run.sh exited.
each() {
	for p in "$@"; do
		n=$(count "start $p")
		[ "$n" -ge 1 ] || fail "\"$p\" did not start"
		[ "$p" != "${FAIL-}" ] || continue
		[ "$(count "term $p")" = "$n" ] || fail "\"$p\" started $n times, got $(count "term $p") TERMs"
		[ "$(count "end $p" "$LOG.exit")" = "$n" ] || fail "run.sh exited before \"$p\" finished"
	done
}

all="uvicorn scholarmind.api:app --host 0.0.0.0 --port 8000|scholarmind cron|scholarmind worker fetch|scholarmind worker analyze"

# --- one process failing stops the others; the container exits with its status
FAIL="scholarmind worker fetch" ANALYZE_WORKERS=2
export FAIL ANALYZE_WORKERS
run allfail ""
[ "$code" = 3 ] || fail "exit status $code, want 3"
IFS='|'
# shellcheck disable=SC2086
each $all
unset IFS
[ "$(count "start scholarmind worker analyze")" = 2 ] || fail "not 2 analyze workers"

# --- docker stop: a TERM for each, and run.sh waits for them to finish
FAIL=
run stop "" stop
[ "$code" = 143 ] || fail "exit status $code, want 143"
IFS='|'
# shellcheck disable=SC2086
each $all
unset IFS

# --- the parts
ANALYZE_WORKERS=0
run worker worker stop
[ "$(count "start scholarmind cron")" = 1 ] && [ "$(count "start scholarmind worker fetch")" = 1 ] || fail "worker part incomplete"
grep -q '^start uvicorn' "$LOG" && fail "worker part started the api"
grep -q '^start scholarmind worker analyze' "$LOG" && fail "ANALYZE_WORKERS=0 started an analyze worker"
run api api stop
[ "$(grep -c '^start ' "$LOG")" = 1 ] && grep -q '^start uvicorn' "$LOG" || fail "api part did not start the api alone"

# --- bad input
ANALYZE_WORKERS=two
run badn ""
[ "$code" = 2 ] || fail "ANALYZE_WORKERS=two: exit status $code, want 2"
ANALYZE_WORKERS=1
run badpart "both"
[ "$code" = 2 ] || fail "part both: exit status $code, want 2"

if [ "$failures" -gt 0 ]; then
	echo "run.sh tests ($bash): $failures failed"
	exit 1
fi
echo "run.sh tests ($bash): ok"
