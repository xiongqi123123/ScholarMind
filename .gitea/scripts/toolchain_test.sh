#!/bin/sh
# Tests toolchain.sh offline: file:// mirrors, a fake archive, a pinned sum.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
t=$(mktemp -d "${TMPDIR:-/tmp}/toolchain-test.XXXXXX")
trap 'rm -rf "$t"' EXIT
fail() { echo "toolchain_test: $*" >&2; exit 1; }

case $(uname -s) in Linux) os=linux ;; *) os=darwin ;; esac
case $(uname -m) in x86_64 | amd64) arch=x64 ;; *) arch=arm64 ;; esac
f=node-v9.9.9-$os-$arch
mkdir -p "$t/pkg/$f/bin" "$t/good" "$t/bad"
printf '#!/bin/sh\necho v9.9.9\n' >"$t/pkg/$f/bin/node"
chmod 755 "$t/pkg/$f/bin/node"
tar -czf "$t/good/$f.tar.gz" -C "$t/pkg" "$f"
echo junk >"$t/bad/$f.tar.gz"
if command -v sha256sum >/dev/null 2>&1; then sum=$(sha256sum "$t/good/$f.tar.gz" | cut -d ' ' -f 1); else sum=$(shasum -a 256 "$t/good/$f.tar.gz" | cut -d ' ' -f 1); fi

# A dead mirror and one with another file are passed over.
out=$(TMPDIR=$t TOOLCHAIN_MIRRORS="file://$t/missing file://$t/bad file://$t/good" SHA256SUMS="$sum  $f.tar.gz" \
	sh "$here/toolchain.sh" node 9.9.9 "$t/node" 2>"$t/log") || { cat "$t/log"; fail "install failed"; }
[ "$out" = "$t/node/bin" ] || fail "printed $out"
[ "$("$t/node/bin/node")" = v9.9.9 ] || fail "not installed"
grep -q "served $f.tar.gz with SHA-256" "$t/log" || fail "the bad mirror was not reported"

# Installed: nothing is fetched again.
out=$(TOOLCHAIN_MIRRORS="file://$t/missing" SHA256SUMS="" sh "$here/toolchain.sh" node 9.9.9 "$t/node" 2>/dev/null) ||
	fail "an installed toolchain was fetched again"
[ "$out" = "$t/node/bin" ] || fail "printed $out the second time"

# No pinned sum for the file: nothing is downloaded or installed.
if TMPDIR=$t TOOLCHAIN_MIRRORS="file://$t/good" SHA256SUMS="$sum  other.tar.gz" \
	sh "$here/toolchain.sh" node 9.9.9 "$t/node2" 2>/dev/null; then
	fail "installed without a checksum"
fi
[ ! -e "$t/node2" ] || fail "left $t/node2"
echo "toolchain_test: ok"
