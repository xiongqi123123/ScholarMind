#!/bin/sh
# scholarmind-deploy.sh: deploy ScholarMind on a Docker host.
#
# Install it on the host with scholarmind-deploy.conf next to it. CI runs it over SSH,
# preferably with a key that may only run this script (authorized_keys command="...",restrict),
# else by its path (see deploy/README.md). It takes exactly these commands, from its
# arguments, or from SSH_ORIGINAL_COMMAND when it has none (the forced-command case):
#
#   deploy <version>   read a tar archive of the sources (git archive) on stdin, build the
#                      Dockerfile, migrate the database, and replace the container (the api
#                      and the workers run in one)
#   status             print the deployed version and the container's state
#
# Nothing from the input is executed or sourced: the command is checked against those two
# words, the version (a commit hash) against a strict pattern, and the archive may only hold
# plain files and directories below the build directory. Docker builds the sources in its own
# sandbox.
#
# Exit status: 0 done, 1 failed (the previous container runs again when there was one),
# 2 rejected input, 75 another deploy is running.
#
# Written for POSIX sh (tested with dash and bash).
# Needs docker, tar, head, mktemp, find, grep and date +%s on the host.

set -u
set -f # no globbing: settings like RUN_FLAGS are split on spaces only
umask 077
LC_ALL=C
export LC_ALL

# Settings; scholarmind-deploy.conf overrides them (see scholarmind-deploy.conf.example).
CONTAINER=scholarmind
IMAGE=scholarmind
STATE_DIR=
KEEP_IMAGES=3
HEALTH_URL=http://127.0.0.1:8000/api/health
HEALTH_TIMEOUT=90
HEALTH_INTERVAL=2
SETTLE=15
STOP_TIMEOUT=30
MAX_ARCHIVE_MB=200
BUILD_FLAGS=
RUN_FLAGS=
MIGRATE_FLAGS=
CONTAINER_FLAGS=
DOCKER=docker

conf=${SCHOLARMIND_DEPLOY_CONF:-$(dirname -- "$0")/scholarmind-deploy.conf}
phase=    # done once the new container is up
work=     # build directory, removed on exit
lockdir=
locked=0
version=
stopped=  # a container stopped but not yet renamed to <name>-prev
created=0 # 1 once this deploy created a new CONTAINER
replaced= # containers kept as <name>-prev to start again on failure, newest first

# The api's health check, run with the image's python; no proxy, whatever the env file says.
probe='import sys, urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open(sys.argv[1], timeout=5)'

log() {
	line="$(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
	printf '%s\n' "$line" 2>/dev/null || true # the CI connection may be gone
	if [ -n "$STATE_DIR" ] && [ -d "$STATE_DIR" ]; then
		printf '%s\n' "$line" >>"$STATE_DIR/deploy.log" 2>/dev/null || true
	fi
}

# die STATUS MESSAGE logs MESSAGE and exits with STATUS.
die() {
	status=$1
	shift
	log "error: $*"
	exit "$status"
}

usage() {
	printf 'usage: scholarmind-deploy.sh deploy <version> < archive.tar\n       scholarmind-deploy.sh status\n' >&2
	exit 2
}

# dk runs docker; tests point DOCKER at a stub.
dk() {
	$DOCKER "$@"
}

exists() {
	dk inspect --type container "$1" >/dev/null 2>&1
}

check_version() {
	if ! printf '%s\n' "$1" | grep -Eqx '[0-9a-f]{7,40}'; then
		die 2 "invalid version: $1"
	fi
}

load_conf() {
	[ -f "$conf" ] || die 1 "no config file at $conf"
	# shellcheck source=/dev/null
	. "$conf"
	[ -n "$STATE_DIR" ] || die 1 "STATE_DIR is not set in $conf"
	for n in "$KEEP_IMAGES" "$HEALTH_TIMEOUT" "$HEALTH_INTERVAL" "$SETTLE" "$STOP_TIMEOUT" "$MAX_ARCHIVE_MB"; do
		case $n in '' | *[!0-9]*) die 1 "$conf: numbers only for KEEP_IMAGES, *_TIMEOUT, HEALTH_INTERVAL, SETTLE, MAX_ARCHIVE_MB" ;; esac
	done
	[ "$KEEP_IMAGES" -ge 1 ] || die 1 "$conf: KEEP_IMAGES must be at least 1"
	for n in "$CONTAINER" "$IMAGE"; do
		case $n in '' | [!a-z0-9]* | *[!a-z0-9_./-]*) die 1 "$conf: invalid container or image name: $n" ;; esac
	done
	mkdir -p "$STATE_DIR" || die 1 "cannot create $STATE_DIR"
}

lock() {
	lockdir=$STATE_DIR/lock
	if ! mkdir "$lockdir" 2>/dev/null; then
		pid=$(cat "$lockdir/pid" 2>/dev/null || true)
		if [ -z "$pid" ] || kill -0 "$pid" 2>/dev/null; then
			die 75 "another deploy is running (pid ${pid:-unknown}; remove $lockdir if there is none)"
		fi
		log "removing the stale lock of pid $pid"
		rm -rf "$lockdir"
		mkdir "$lockdir" 2>/dev/null || die 75 "another deploy is running"
	fi
	locked=1
	echo $$ >"$lockdir/pid"
}

on_exit() {
	status=$1
	if [ "$status" -ne 0 ] && [ "$phase" != done ]; then
		if [ -n "$stopped" ]; then # the rename failed; the old container is intact
			log "starting $stopped again"
			dk start "$stopped" >/dev/null 2>&1 || log "could not start $stopped; check it by hand"
		fi
		if [ "$created" = 1 ]; then
			show_logs "$CONTAINER"
			dk rm -f "$CONTAINER" >/dev/null 2>&1
			case " $replaced " in
			*" $CONTAINER "*) ;;
			*) log "removed the failed $CONTAINER; there was no previous one to start" ;;
			esac
		fi
		for c in $replaced; do
			restore "$c"
		done
	fi
	[ -z "$work" ] || rm -rf "$work"
	[ "$locked" = 0 ] || rm -rf "$lockdir"
}

show_logs() {
	exists "$1" || return 0
	log "last log lines of $1:"
	dk logs --tail 30 "$1" 2>&1 </dev/null | sed 's/^/  | /' || true
}

# restore NAME starts NAME-prev again under its own name.
restore() {
	if dk rename "$1-prev" "$1" && dk start "$1" >/dev/null && check "$1"; then
		log "rolled back; $1 runs $(dk inspect --type container -f '{{.Config.Image}}' "$1" 2>/dev/null) again"
	else
		log "ROLLBACK FAILED: check $1 and $1-prev by hand"
	fi
}

# receive reads the archive from stdin into $work/src, refusing anything but plain files and
# directories below it.
receive() {
	max=$((MAX_ARCHIVE_MB * 1024 * 1024))
	head -c $((max + 1)) >"$work/src.tar" || die 2 "cannot read the archive"
	size=$(wc -c <"$work/src.tar")
	[ "$size" -gt 0 ] || die 2 "no archive on stdin"
	[ "$size" -le "$max" ] || die 2 "the archive is larger than $MAX_ARCHIVE_MB MB"

	tar -tf "$work/src.tar" >"$work/names" 2>"$work/tar.err" || die 2 "not a tar archive: $(head -n 1 "$work/tar.err")"
	# GNU tar lists absolute names without the leading / and only warns.
	[ ! -s "$work/tar.err" ] || die 2 "tar warns about the archive: $(head -n 1 "$work/tar.err")"
	while IFS= read -r name; do
		case /$name/ in
		//* | */../*) die 2 "unsafe path in the archive: $name" ;;
		esac
	done <"$work/names"
	tar -tvf "$work/src.tar" >"$work/list" 2>/dev/null || die 2 "cannot list the archive"
	while IFS= read -r line; do
		case $line in
		-* | d*) ;;
		*) die 2 "the archive may only hold plain files and directories: $line" ;;
		esac
		case $line in
		*' -> '* | *' link to '*) die 2 "the archive may not hold links: $line" ;;
		esac
	done <"$work/list"

	mkdir "$work/src" && tar -xf "$work/src.tar" -C "$work/src" 2>"$work/tar.err" ||
		die 2 "cannot unpack the archive: $(head -n 1 "$work/tar.err")"
	[ -z "$(find "$work/src" ! -type f ! -type d)" ] || die 2 "the archive held something other than files and directories"
	[ -f "$work/src/Dockerfile" ] || die 2 "the archive has no Dockerfile"
	rm -f "$work/src.tar"
}

build() {
	log "building $IMAGE:$version"
	if ! dk build -f "$work/src/Dockerfile" -t "$IMAGE:$version" $BUILD_FLAGS "$work/src" \
		>"$work/build.log" 2>&1 </dev/null; then
		cp "$work/build.log" "$STATE_DIR/last-build.log" 2>/dev/null
		tail -n 30 "$work/build.log" | sed 's/^/  | /' 2>/dev/null || true
		die 1 "the build failed; nothing else changed (full log: $STATE_DIR/last-build.log)"
	fi
	cp "$work/build.log" "$STATE_DIR/last-build.log" 2>/dev/null
}

# migrate brings the database to the new image's schema while the old container still runs.
# Alembic runs all migrations in one transaction, so a failed one changes nothing.
migrate() {
	log "migrating the database"
	if ! dk run --rm --name "$CONTAINER-migrate" $RUN_FLAGS $MIGRATE_FLAGS "$IMAGE:$version" alembic upgrade head \
		>"$work/migrate.log" 2>&1 </dev/null; then
		tail -n 30 "$work/migrate.log" | sed 's/^/  | /' 2>/dev/null || true
		die 1 "the migration failed; the containers are unchanged"
	fi
	sed 's/^/  | /' "$work/migrate.log" 2>/dev/null || true
}

# retire NAME stops NAME and keeps it as NAME-prev, to start again if the deploy fails.
retire() {
	log "stopping $1 ($(dk inspect --type container -f '{{.Config.Image}}' "$1"))"
	dk stop -t "$STOP_TIMEOUT" "$1" >/dev/null || die 1 "cannot stop $1"
	stopped=$1
	dk rename "$1" "$1-prev" || die 1 "cannot rename $1"
	stopped=
	replaced="$1 $replaced"
}

# check NAME: NAME answers HEALTH_URL and then keeps running, not restarted, for SETTLE
# seconds: the workers in it exit at once when they cannot start (Redis unreachable, a bad
# config.yaml), and the container with them.
check() {
	started=$(dk inspect --type container -f '{{.State.StartedAt}}' "$1" 2>/dev/null)
	wait_healthy "$1" || return 1
	log "checking that $1 stays up for ${SETTLE}s"
	sleep "$SETTLE"
	if [ "$(dk inspect --type container -f '{{.State.Running}} {{.State.StartedAt}}' "$1" 2>/dev/null)" != "true $started" ]; then
		log "$1 stopped or restarted"
		return 1
	fi
}

wait_healthy() {
	log "waiting up to ${HEALTH_TIMEOUT}s for $HEALTH_URL in $1"
	deadline=$(($(date +%s) + HEALTH_TIMEOUT))
	while :; do
		if dk exec "$1" python -c "$probe" "$HEALTH_URL" >/dev/null 2>&1 </dev/null; then
			return 0
		fi
		if [ "$(dk inspect --type container -f '{{.State.Running}}' "$1" 2>/dev/null)" != true ]; then
			log "$1 is not running"
			return 1
		fi
		[ "$(date +%s)" -lt "$deadline" ] || return 1
		sleep "$HEALTH_INTERVAL"
	done
}

# retain_images keeps the newest KEEP_IMAGES images of IMAGE and the one that runs, and
# removes the other images of IMAGE by ID; never anything else.
retain_images() {
	current=$(dk inspect --type container -f '{{.Image}}' "$CONTAINER" 2>/dev/null)
	dk images --no-trunc --format '{{.ID}}' "$IMAGE" >"$work/images" 2>/dev/null </dev/null || {
		log "warning: cannot list the images of $IMAGE"
		return 0
	}
	n=0
	seen=' '
	while read -r id; do
		case $seen in *" $id "*) continue ;; esac
		seen="$seen$id "
		n=$((n + 1))
		[ "$n" -gt "$KEEP_IMAGES" ] && [ "$id" != "$current" ] || continue
		if dk rmi "$id" >/dev/null 2>&1 </dev/null; then
			log "removed old image $id"
		else
			log "kept old image $id (still in use or tagged elsewhere)"
		fi
	done <"$work/images"
}

deploy() {
	version=$1
	lock
	trap 'on_exit $?' EXIT
	trap 'exit 1' INT TERM
	trap '' HUP PIPE # finish even if the CI connection drops
	work=$(mktemp -d "$STATE_DIR/build.XXXXXX") || die 1 "cannot create a build directory in $STATE_DIR"

	log "deploying $IMAGE:$version"
	receive
	build
	if exists "$CONTAINER-prev"; then
		die 1 "$CONTAINER-prev is left from an interrupted deploy; keep one of $CONTAINER and $CONTAINER-prev by hand"
	fi
	migrate

	if exists "$CONTAINER"; then
		retire "$CONTAINER"
	fi
	created=1
	# shellcheck disable=SC2086 # flags are split on spaces on purpose
	dk create --name "$CONTAINER" $RUN_FLAGS $CONTAINER_FLAGS "$IMAGE:$version" >/dev/null </dev/null || die 1 "cannot create $CONTAINER"
	dk start "$CONTAINER" >/dev/null || die 1 "cannot start $CONTAINER"
	check "$CONTAINER" || die 1 "$CONTAINER did not become healthy or did not stay up"
	phase=done

	for c in $replaced; do
		dk rm "$c-prev" >/dev/null || log "warning: cannot remove $c-prev"
	done
	printf 'version=%s\nimage=%s\ndeployed_at=%s\n' "$version" "$IMAGE:$version" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$STATE_DIR/deployed.new" &&
		mv "$STATE_DIR/deployed.new" "$STATE_DIR/deployed"
	retain_images
	log "deployed $IMAGE:$version"
}

status() {
	if [ -f "$STATE_DIR/deployed" ]; then
		cat "$STATE_DIR/deployed"
	else
		echo "version=none"
	fi
	state=$(dk inspect --type container -f '{{.State.Status}} {{.Config.Image}}' "$CONTAINER" 2>/dev/null) || state=missing
	echo "container=$CONTAINER $state"
}

# Arguments win: run by path over SSH, SSH_ORIGINAL_COMMAND holds the whole command line,
# path included.
if [ $# -gt 0 ]; then
	words="$*"
else
	words=${SSH_ORIGINAL_COMMAND-}
fi
case $words in
'' | *[!a-z0-9.\ -]*) usage ;;
esac
# shellcheck disable=SC2086 # split the checked words on purpose
set -- $words
case ${1-}:$# in
deploy:2)
	check_version "$2"
	load_conf
	deploy "$2"
	;;
status:1)
	load_conf
	status
	;;
*) usage ;;
esac
