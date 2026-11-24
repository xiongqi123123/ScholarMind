#!/bin/bash
# The container's processes: the api (uvicorn, port 8000), the cron scheduler that enqueues the
# daily sync, one fetch worker (data sources are rate limited) and ANALYZE_WORKERS analyze
# workers (default 1, 0 for none). `scholarmind-run api` or `scholarmind-run worker` runs only
# that part, for a deployment that splits them; run the worker part once, it has the cron.
# When any process exits, stops the others and exits with its status, so Docker's restart
# policy starts the whole container again. docker stop asks each to finish what it is doing.
set -u
part=${1:-all}
n=${ANALYZE_WORKERS:-1}
case $part in
all | api | worker) ;;
*) echo "usage: scholarmind-run [all|api|worker]" >&2; exit 2 ;;
esac
case $n in
'' | *[!0-9]*) echo "ANALYZE_WORKERS must be a number, not $n" >&2; exit 2 ;;
esac

pids=()
if [ "$part" != worker ]; then
	uvicorn scholarmind.api:app --host 0.0.0.0 --port 8000 &
	pids+=($!)
fi
if [ "$part" != api ]; then
	scholarmind cron &
	pids+=($!)
	scholarmind worker fetch &
	pids+=($!)
	for ((i = 0; i < n; i++)); do
		scholarmind worker analyze &
		pids+=($!)
	done
fi

# A trapped TERM ends wait -n; each process then gets one TERM (RQ: warm shutdown).
trap : TERM INT
wait -n
status=$?
kill -TERM "${pids[@]}" 2>/dev/null
wait
exit "$status"
