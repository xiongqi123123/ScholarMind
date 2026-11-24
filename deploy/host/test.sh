#!/bin/sh
# Tests scholarmind-deploy.sh against a fake docker that keeps its images and containers in
# files; no Docker needed.
#
#   sh deploy/host/test.sh          # the script under test runs with sh
#   SH=dash sh deploy/host/test.sh  # ... or with another shell

set -u
here=$(cd "$(dirname -- "$0")" && pwd)
script=$here/scholarmind-deploy.sh
shell=${SH:-sh}
tmp=$(mktemp -d "${TMPDIR:-/tmp}/scholarmind-deploy-test.XXXXXX")
trap 'rm -rf "$tmp"' EXIT
failures=0

fail() {
	echo "FAIL [$case_name]: $*"
	failures=$((failures + 1))
}

# The fake docker. State: images (lines "id repo tag", oldest first), c/<name> (one line
# "image-id running image-ref started"), calls (one line per call). Controls, lines
# "name image-ref": unhealthy (the health check fails and the container stops), restarts (the
# health check passes, then the container restarts); and the files fail-build, fail-migrate.
cat >"$tmp/docker" <<'EOF'
#!/bin/sh
F=${FAKE:?}
echo "$*" >>"$F/calls"
cmd=$1
shift
container() { [ -f "$F/c/$1" ] && read -r c_id c_running c_ref c_started <"$F/c/$1"; }
next() { n=$(($(cat "$F/seq" 2>/dev/null || echo 0) + 1)); echo "$n" >"$F/seq"; }
listed() { grep -qx "$2 $c_ref" "$F/$1" 2>/dev/null; }
case $cmd in
build)
	[ ! -f "$F/fail-build" ] || { echo "step 3/9: boom"; exit 1; }
	while [ $# -gt 0 ]; do [ "$1" != -t ] || ref=$2; shift; done
	next
	grep -v " ${ref%:*} ${ref##*:}\$" "$F/images" >"$F/images.new"; mv "$F/images.new" "$F/images"
	echo "sha256:$(printf '%064d' "$n") ${ref%:*} ${ref##*:}" >>"$F/images"
	;;
run)
	[ ! -f "$F/fail-migrate" ] || { echo "alembic: boom"; exit 1; }
	echo "alembic: upgraded"
	;;
inspect)
	fmt=
	while [ $# -gt 1 ]; do [ "$1" != -f ] || fmt=$2; shift; done
	container "$1" || exit 1
	running=$c_running
	! listed unhealthy "$1" || running=false
	case $fmt in
	'{{.Image}}') echo "$c_id" ;;
	'{{.Config.Image}}') echo "$c_ref" ;;
	'{{.State.Running}}') echo "$running" ;;
	'{{.State.StartedAt}}') echo "$c_started" ;;
	'{{.State.Running}} {{.State.StartedAt}}')
		if listed restarts "$1"; then echo "true $c_started-again"; else echo "$running $c_started"; fi
		;;
	'{{.State.Status}} {{.Config.Image}}') [ "$c_running" = true ] && echo "running $c_ref" || echo "exited $c_ref" ;;
	*) echo '[{}]' ;;
	esac
	;;
create) # the image is the first argument naming one; a command may follow it
	id=
	while [ $# -gt 0 ] && [ -z "$id" ]; do
		[ "$1" != --name ] || name=$2
		case $1 in *:*) id=$(awk -v r="${1%:*}" -v t="${1##*:}" '$2 == r && $3 == t { print $1 }' "$F/images"); ref=$1 ;; esac
		shift
	done
	[ -n "$id" ] && [ ! -f "$F/c/$name" ] || exit 1
	echo "$id false $ref -" >"$F/c/$name"
	;;
start | stop)
	name=$(eval echo "\${$#}")
	container "$name" || exit 1
	if [ "$cmd" = start ]; then next; echo "$c_id true $c_ref $n" >"$F/c/$name"; else echo "$c_id false $c_ref $c_started" >"$F/c/$name"; fi
	;;
rename)
	container "$1" && [ ! -f "$F/c/$2" ] && mv "$F/c/$1" "$F/c/$2"
	;;
rm)
	[ "$1" != -f ] || shift
	container "$1" && rm "$F/c/$1"
	;;
exec)
	container "$1" && [ "$c_running" = true ] && ! listed unhealthy "$1"
	;;
images)
	repo=$(eval echo "\${$#}")
	awk -v r="$repo" '$2 == r { print $1 }' "$F/images" | sed -n '1!G;h;$p'
	;;
rmi)
	for f in "$F"/c/*; do
		[ -f "$f" ] || continue
		read -r id _ <"$f"
		[ "$id" != "$1" ] || { echo "image is being used"; exit 1; }
	done
	grep -v "^$1 " "$F/images" >"$F/images.new"; mv "$F/images.new" "$F/images"
	;;
logs) echo "fake log line" ;;
*) echo "fake docker: unexpected $cmd"; exit 1 ;;
esac
EOF
chmod +x "$tmp/docker"

# A source archive like git archive's: the files at the top level.
mkdir -p "$tmp/src/backend"
echo 'FROM scratch' >"$tmp/src/Dockerfile"
echo '[project]' >"$tmp/src/backend/pyproject.toml"
tar -cf "$tmp/good.tar" -C "$tmp/src" Dockerfile backend

# start NAME sets up a fresh host for one case.
start() {
	case_name=$1
	dir=$tmp/case-$1
	FAKE=$dir/fake
	mkdir -p "$FAKE/c" "$dir/state"
	: >"$FAKE/images"
	export FAKE
	cat >"$dir/scholarmind-deploy.conf" <<EOF
CONTAINER=app
IMAGE=sm
STATE_DIR=$dir/state
DOCKER=$tmp/docker
HEALTH_TIMEOUT=0
SETTLE=0
RUN_FLAGS="--network net -e TZ=Asia/Shanghai --env-file /srv/sm/.env"
MIGRATE_FLAGS="--cpus 1"
CONTAINER_FLAGS="--restart always --cpus 3 -e ANALYZE_WORKERS=2"
EOF
	SCHOLARMIND_DEPLOY_CONF=$dir/scholarmind-deploy.conf
	export SCHOLARMIND_DEPLOY_CONF
}

# run COMMAND [ARCHIVE] runs the script as the forced command of an SSH key; it sets out and
# code.
run() {
	out=$(SSH_ORIGINAL_COMMAND=$1 "$shell" "$script" <"${2:-/dev/null}" 2>&1)
	code=$?
}

# run_argv SOC ARCHIVE ARG... runs the script by path with arguments, as a shell on the host
# does for an unrestricted key; SOC is SSH_ORIGINAL_COMMAND, or - for none. It sets out and
# code.
run_argv() {
	soc=$1 archive=$2
	shift 2
	out=$(
		if [ "$soc" = - ]; then unset SSH_ORIGINAL_COMMAND; else SSH_ORIGINAL_COMMAND=$soc && export SSH_ORIGINAL_COMMAND; fi
		"$shell" "$script" "$@" <"$archive" 2>&1
	)
	code=$?
}

# expect CODE [TEXT] checks the last run.
expect() {
	[ "$code" = "$1" ] || fail "exit status $code, want $1; output:
$out"
	[ $# -lt 2 ] || case $out in *"$2"*) ;; *) fail "output lacks \"$2\":
$out" ;; esac
}

running() { # running CONTAINER REF: the container runs that image
	read -r _ r ref _ <"$FAKE/c/$1" 2>/dev/null
	[ "${r-}" = true ] && [ "${ref-}" = "$2" ] || fail "$1 is not running $2 (running=${r-} ref=${ref-})"
}

gone() { # gone NAME...: no such containers
	for c in "$@"; do [ ! -f "$FAKE/c/$c" ] || fail "$c was left behind"; done
}

called() { grep -q -- "$1" "$FAKE/calls" 2>/dev/null; }

# line PATTERN prints the number of the first call matching PATTERN.
line() { grep -n -- "$1" "$FAKE/calls" | head -n 1 | cut -d : -f 1; }

# --- happy path: first deploy, second deploy, status
start happy
run "deploy abc1234" "$tmp/good.tar"
expect 0 "deployed sm:abc1234"
running app sm:abc1234
called "build -f .*/Dockerfile -t sm:abc1234" || fail "build not called as expected"
called "run --rm --name app-migrate --network net -e TZ=Asia/Shanghai --env-file /srv/sm/.env --cpus 1 sm:abc1234 alembic upgrade head" ||
	fail "migration not run as expected"
called "create --name app --network net -e TZ=Asia/Shanghai --env-file /srv/sm/.env --restart always --cpus 3 -e ANALYZE_WORKERS=2 sm:abc1234\$" ||
	fail "container not created with the flags and the image's command"
: >"$FAKE/calls"
run "deploy def5678abc" "$tmp/good.tar"
expect 0 "deployed sm:def5678abc"
running app sm:def5678abc
gone app-prev
[ "$(line 'run --rm')" -lt "$(line 'stop -t 30 app')" ] || fail "migrated after stopping the container"
run "status"
expect 0 "version=def5678abc"
expect 0 "container=app running sm:def5678abc"
[ ! -d "$dir/state/lock" ] || fail "lock left behind"
[ -z "$(ls -d "$dir"/state/build.* 2>/dev/null)" ] || fail "build directory left behind"

# --- a failed migration leaves the running container alone
start migratefail
run "deploy abc1234" "$tmp/good.tar"
touch "$FAKE/fail-migrate"
: >"$FAKE/calls"
run "deploy abc1235" "$tmp/good.tar"
expect 1 "the migration failed"
expect 1 "alembic: boom"
running app sm:abc1234
called "stop" && fail "a container was stopped for a failed migration"

# --- a failed health check rolls back
start unhealthy
run "deploy abc1234" "$tmp/good.tar"
echo "app sm:abc1235" >"$FAKE/unhealthy"
run "deploy abc1235" "$tmp/good.tar"
expect 1 "rolled back; app runs sm:abc1234 again"
running app sm:abc1234
gone app-prev
grep -q version=abc1234 "$dir/state/deployed" || fail "state changed although the deploy failed"
case $out in *"fake log line"*) ;; *) fail "logs of the failed container not shown" ;; esac

# --- a container that answers but then restarts (its workers exit) rolls back too
start restarts
run "deploy abc1234" "$tmp/good.tar"
echo "app sm:abc1235" >"$FAKE/restarts"
run "deploy abc1235" "$tmp/good.tar"
expect 1 "app stopped or restarted"
expect 1 "rolled back; app runs sm:abc1234 again"
running app sm:abc1234
gone app-prev

# --- a failed first deploy leaves nothing behind
start firstfail
echo "app sm:abc1234" >"$FAKE/unhealthy"
run "deploy abc1234" "$tmp/good.tar"
expect 1 "removed the failed app"
gone app
rm "$FAKE/unhealthy"
run "deploy abc1234" "$tmp/good.tar"
expect 0 "deployed sm:abc1234"

# --- a failed build leaves the running container alone
start buildfail
run "deploy abc1234" "$tmp/good.tar"
touch "$FAKE/fail-build"
: >"$FAKE/calls"
run "deploy abc1235" "$tmp/good.tar"
expect 1 "step 3/9: boom"
running app sm:abc1234
called "stop" && fail "container stopped for a failed build"
called "run --rm" && fail "migrated for a failed build"

# --- a container left from an interrupted deploy stops the deploy before the migration
start leftover
run "deploy abc1234" "$tmp/good.tar"
cp "$FAKE/c/app" "$FAKE/c/app-prev"
: >"$FAKE/calls"
run "deploy abc1235" "$tmp/good.tar"
expect 1 "app-prev is left from an interrupted deploy"
called "run --rm" && fail "migrated with a leftover container"
running app sm:abc1234

# --- one deploy at a time
start lock
mkdir "$dir/state/lock"
echo $$ >"$dir/state/lock/pid" # this shell is alive
run "deploy abc1234" "$tmp/good.tar"
expect 75 "another deploy is running"
called build && fail "built while locked"
sh -c 'exit 0' &
dead=$!
wait "$dead"
echo "$dead" >"$dir/state/lock/pid"
run "deploy abc1234" "$tmp/good.tar"
expect 0 "removing the stale lock"

# --- archives with anything but plain files and directories inside
start archive
mkdir -p "$tmp/evil/sub"
cp "$tmp/src/Dockerfile" "$tmp/evil/"
echo x >"$tmp/evil/escape"
# Each holds a valid Dockerfile, so only the bad entry can be refused.
tar -cPf "$tmp/dotdot.tar" -C "$tmp/evil" Dockerfile sub/../escape
tar -cPf "$tmp/absolute.tar" -C "$tmp/evil" Dockerfile "$tmp/evil/escape"
ln -s /etc "$tmp/evil/link"
tar -cf "$tmp/symlink.tar" -C "$tmp/evil" Dockerfile link
ln "$tmp/evil/Dockerfile" "$tmp/evil/hard"
tar -cf "$tmp/hardlink.tar" -C "$tmp/evil" Dockerfile hard
echo "not a tar archive" >"$tmp/garbage.tar"
mkdir -p "$tmp/nodockerfile"
echo x >"$tmp/nodockerfile/main.py"
tar -cf "$tmp/nodockerfile.tar" -C "$tmp/nodockerfile" main.py
for a in dotdot absolute symlink hardlink garbage nodockerfile; do
	run "deploy abc1234" "$tmp/$a.tar"
	[ "$code" = 2 ] || fail "$a archive: exit status $code, want 2; output:
$out"
	case $a:$out in
	*link:*"held something other than"*) fail "$a archive got unpacked before it was refused" ;;
	dotdot:* | absolute:*)
		case $out in *"unsafe path"* | *"tar warns"*) ;; *) fail "$a archive refused for another reason: $out" ;; esac
		;;
	esac
done
run "deploy abc1234" /dev/null
expect 2 "no archive on stdin"
called build && fail "built from a rejected archive"
[ ! -e "$dir/state/escape" ] && [ ! -e "$dir/escape" ] || fail "a file escaped the build directory"

# --- only the fixed commands and strictly formed versions
start input
nl='
'
for cmd in "deploy v1.0.0" "deploy abc123" "deploy ABCDEF0" "deploy abc1234;id" "deploy abc1234 extra" \
	"deploy $(printf '%041d' 0)" "deploy abc1234-dirty" "deploy \$(id)" "deploy abc1234${nl}status" \
	"status now" "rm -rf /" "" "deploy"; do
	run "$cmd" "$tmp/good.tar"
	[ "$code" = 2 ] || fail "command \"$cmd\": exit status $code, want 2"
done
called build && fail "built for a rejected command"

# --- run by path: the arguments are the command, SSH_ORIGINAL_COMMAND is ignored
start argv
run_argv - "$tmp/good.tar" deploy abc1230
expect 0 "deployed sm:abc1230"
run_argv "$script deploy abc1231" "$tmp/good.tar" deploy abc1231 # what a host shell runs for "<path> deploy abc1231"
expect 0 "deployed sm:abc1231"
running app sm:abc1231
run_argv 'deploy abc1232;id' "$tmp/good.tar" deploy abc1232
expect 0 "deployed sm:abc1232"
run_argv status "$tmp/good.tar" deploy abc1233
expect 0 "deployed sm:abc1233"
run_argv "deploy abc1234" /dev/null status
expect 0 "version=abc1233"
builds=$(grep -c '^build' "$FAKE/calls")
for args in "deploy abc1234;id" "rm -rf /" "deploy abc1234 extra" "status now" "deploy"; do
	# shellcheck disable=SC2086 # the words are the arguments
	run_argv "deploy abc1234" "$tmp/good.tar" $args # a valid SSH_ORIGINAL_COMMAND is no fallback
	[ "$code" = 2 ] || fail "arguments \"$args\": exit status $code, want 2"
done
[ "$(grep -c '^build' "$FAKE/calls")" = "$builds" ] || fail "built for rejected arguments"

# --- old images of this repository go by ID; others stay
start retention
for i in 0 1 2 3 4; do
	echo "sha256:old$i sm abc000$i" >>"$FAKE/images"
done
echo "sha256:other other v1" >>"$FAKE/images"
echo "sha256:old0 true sm:abc0000 1" >"$FAKE/c/pinned" # an old image still in use
run "deploy abc1234" "$tmp/good.tar"
expect 0 "removed old image sha256:old2"
for id in old1 old2; do
	called "rmi sha256:$id" || fail "$id not removed"
done
for id in old3 old4 other; do
	called "rmi sha256:$id" && fail "$id removed"
done
expect 0 "kept old image sha256:old0"
grep -q "^sha256:other " "$FAKE/images" || fail "image of another repository removed"
called prune && fail "prune called"

# --- the example config is one the script accepts
start example
sed -e "s|^STATE_DIR=.*|STATE_DIR=$dir/state|" "$here/scholarmind-deploy.conf.example" >"$SCHOLARMIND_DEPLOY_CONF"
echo "DOCKER=$tmp/docker" >>"$SCHOLARMIND_DEPLOY_CONF"
run "status"
expect 0 "version=none"
expect 0 "container=scholarmind missing"

if [ "$failures" -gt 0 ]; then
	echo "scholarmind-deploy tests ($shell): $failures failed"
	exit 1
fi
echo "scholarmind-deploy tests ($shell): ok"
