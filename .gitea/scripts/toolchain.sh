#!/bin/sh
# Installs a Node.js toolchain for CI, surviving a flaky network.
#
#   toolchain.sh node <version> <dir>
#
# Installs the toolchain into <dir> unless it is there already (restored
# from the cache), and prints the directory to add to PATH. On a miss it
# downloads the archive for this machine from several mirrors in turn,
# mainland China's first, retrying each, and installs it only if its
# SHA-256 matches. The expected value comes from $SHA256SUMS when set
# (lines "<sha256>  <file>", as in SHASUMS256.txt), otherwise from
# nodejs.org. $TOOLCHAIN_MIRRORS (base URLs, separated by spaces) replaces
# the built-in mirrors. Needs curl, tar and sha256sum or shasum. Messages
# go to stderr.
set -eu

tool=${1:?usage: toolchain.sh node <version> <dir>}
version=${2:?usage: toolchain.sh node <version> <dir>}
dir=${3:?usage: toolchain.sh node <version> <dir>}

log() { printf 'toolchain: %s\n' "$*" >&2; }

[ "$tool" = node ] || { log "unknown tool $tool"; exit 2; }
case $(uname -s) in
Linux) os=linux ;;
Darwin) os=darwin ;;
*) log "unsupported system $(uname -s)"; exit 1 ;;
esac
case $(uname -m) in
x86_64 | amd64) arch=x64 ;;
aarch64 | arm64) arch=arm64 ;;
*) log "unsupported machine $(uname -m)"; exit 1 ;;
esac

file=node-v$version-$os-$arch.tar.gz
mirrors=${TOOLCHAIN_MIRRORS:-"https://cdn.npmmirror.com/binaries/node/v$version https://nodejs.org/dist/v$version"}

if [ -x "$dir/bin/node" ]; then
	log "$tool $version is installed in $dir"
	echo "$dir/bin"
	exit 0
fi

# fetch URL FILE downloads with curl's own retries; a transfer that stalls
# below 1 KB/s for a minute counts as failed.
fetch() {
	curl -fsSL --retry 2 --retry-all-errors --retry-delay 3 --connect-timeout 20 \
		--speed-limit 1024 --speed-time 60 -o "$2" "$1"
}

sha256() {
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$1" | cut -d ' ' -f 1
	else
		shasum -a 256 "$1" | cut -d ' ' -f 1
	fi
}

tmp=$(mktemp -d "${TMPDIR:-/tmp}/toolchain.XXXXXX")
trap 'rm -rf "$tmp"' EXIT

# expected prints the archive's SHA-256: pinned in $SHA256SUMS, or from
# nodejs.org.
expected() {
	if [ -n "${SHA256SUMS:-}" ]; then
		printf '%s\n' "$SHA256SUMS" | awk -v f="$file" '$2 == f { print $1; exit }'
		return
	fi
	for round in 1 2 3; do
		if fetch "https://nodejs.org/dist/v$version/SHASUMS256.txt" "$tmp/list" 2>/dev/null; then
			awk -v f="$file" '$2 == f { print $1; exit }' "$tmp/list"
			return
		fi
		log "could not get the checksum of $file (round $round); trying again"
		sleep $((round * 5))
	done
}

want=$(expected)
case $want in
*[!0-9a-f]* | "") want=bad ;;
esac
if [ "${#want}" -ne 64 ]; then
	log "no SHA-256 for $file"
	exit 1
fi

# Each round tries every mirror; a mirror that fails or serves something
# else is passed over.
for round in 1 2 3; do
	for m in $mirrors; do
		log "downloading $m/$file"
		if ! fetch "$m/$file" "$tmp/archive"; then
			log "download from $m failed"
			continue
		fi
		got=$(sha256 "$tmp/archive")
		if [ "$got" != "$want" ]; then
			log "$m served $file with SHA-256 $got, not $want"
			continue
		fi
		mkdir -p "$tmp/x" "$(dirname "$dir")"
		tar -xzf "$tmp/archive" -C "$tmp/x" --strip-components=1
		rm -rf "$dir"
		mv "$tmp/x" "$dir"
		log "installed $tool $version in $dir: $("$dir/bin/node" --version)"
		echo "$dir/bin"
		exit 0
	done
	log "no mirror worked (round $round of 3)"
	[ "$round" -eq 3 ] || sleep $((round * 10))
done
log "giving up on $file"
exit 1
