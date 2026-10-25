#!/bin/sh
# Puts the confirm stamp next to Howdy's own, and patches Howdy's stamp loader so stamps can
# ask in the terminal when howdy-gtk is not installed.
#
#   sudo ./install.sh [--howdy-dir DIR]
#   sudo ./install.sh --remove [--howdy-dir DIR]
set -eu

here=$(cd "$(dirname "$0")" && pwd)
patch=$here/rubberstamps-terminal.patch
action=install
dir=

while [ $# -gt 0 ]; do
	case $1 in
		--remove) action=remove ;;
		--howdy-dir) dir=${2:?--howdy-dir needs a directory}; shift ;;
		*) echo "usage: $0 [--remove] [--howdy-dir DIR]" >&2; exit 2 ;;
	esac
	shift
done

if [ -z "$dir" ]; then
	# Howdy 3 installs as a Python package, Howdy 2 under the PAM security directory
	for candidate in /usr/lib/python3*/site-packages/howdy /usr/lib64/python3*/site-packages/howdy \
		/usr/local/lib/python3*/site-packages/howdy /usr/lib/security/howdy /lib/security/howdy; do
		if [ -f "$candidate/rubberstamps/__init__.py" ]; then
			dir=$candidate
			break
		fi
	done
fi

if [ -z "$dir" ] || [ ! -f "$dir/rubberstamps/__init__.py" ]; then
	echo "Howdy's rubberstamps directory was not found. Pass it with --howdy-dir." >&2
	exit 1
fi

stamps=$dir/rubberstamps

if [ ! -w "$stamps" ]; then
	echo "$stamps belongs to root. Run: sudo $0" >&2
	exit 1
fi

# git apply works outside a repository too, which spares a dependency on patch(1)
applied() {
	git -C "$dir" apply -R --check -p1 "$patch" 2>/dev/null
}

if [ "$action" = remove ]; then
	rm -f "$stamps/confirm.py"
	if applied; then
		git -C "$dir" apply -R -p1 "$patch"
		rm -f "$stamps/__init__.py.orig"
	fi
	echo "Removed the confirm stamp from $stamps."
	echo "Take its line out of stamp_rules in /etc/howdy/config.ini; until then Howdy prints"
	echo "\"Stamp not installed: confirm\" and lets a recognised face through without asking."
	exit 0
fi

install -m 644 "$here/confirm.py" "$stamps/confirm.py"

if applied; then
	echo "The loader patch is already in $stamps/__init__.py."
elif git -C "$dir" apply --check -p1 "$patch" 2>/dev/null; then
	cp -p "$stamps/__init__.py" "$stamps/__init__.py.orig"
	git -C "$dir" apply -p1 "$patch"
	echo "Patched $stamps/__init__.py (the original is __init__.py.orig)."
else
	echo "This Howdy's stamp loader differs from the one the patch was made for, so it was left alone." >&2
	echo "The confirm window still works; only prompts without howdy-gtk stay invisible." >&2
fi

echo "Installed confirm.py into $stamps."
cat <<'EOF'

Now turn it on in /etc/howdy/config.ini:

[rubberstamps]
enabled = true
stamp_rules =
	confirm	10s	failsafe	confirm_key=enter abort_key=esc
EOF
