#!/bin/sh
# Bring up `infra/docker-compose.yml` before the Dev Container starts; the container joins its
# `xavier` network.
set -eu

readonly SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
readonly COMPOSE_FILE="${SCRIPT_DIR}/../infra/docker-compose.yml"

# Message types:
#   🫱 info – announcing a step
#   ✋ wait – blocking on an external dependency
#   👌 ok   – step succeeded
#   🤙 skip – nothing to do, already in the desired state
#   🤔 ask  – prompting the user for a decision
#   🤬 fail – fatal error, exit 1

info() {
	echo "🫱 $1"
}

wait_for() {
	echo "✋ $1"
}

ok() {
	echo "👌 $1"
}

skip() {
	echo "🤙 $1"
}

fail() {
	echo "🤬 $1" >&2
	exit 1
}

# confirm QUESTION LABEL
#   Interactive y/n prompt; single keypress, no Enter needed. On non-TTY stdin
#   (CI / headless) defaults to yes without prompting. On "no": prints
#   "🤙 skipping LABEL". Returns 0 = yes, 1 = no (POSIX convention).
confirm() {
	local question="$1"
	local label="$2"
	local _stty_saved
	local _reply

	if [ ! -t 0 ]; then
		return 0
	fi

	_stty_saved="$(stty -g)"
	trap 'stty "${_stty_saved}"' EXIT INT TERM
	stty -echo -icanon min 1 time 0

	printf '🤔 %s (Y/n) ' "${question}"
	while true; do
		_reply="$(dd bs=1 count=1 2>/dev/null)"
		case "${_reply}" in
		[yYnN] | '')
			printf '%s\n' "${_reply}"
			break
			;;
		*) printf '\r\033[2K🤔 %s (Y/n) ' "${question}" ;;
		esac
	done

	stty "${_stty_saved}"
	trap - EXIT INT TERM

	case "${_reply}" in
	[nN])
		skip "skipping ${label}"
		return 1
		;;
	esac
}

command -v docker >/dev/null 2>&1 || fail "docker is not installed or not on PATH"
[ -f "${COMPOSE_FILE}" ] || fail "compose file not found: \`${COMPOSE_FILE}\`"

if docker compose -f "${COMPOSE_FILE}" ps --quiet --status running 2>/dev/null | grep -q .; then
	skip "infra stack is already running"
	exit 0
fi

case "${INFRA_AUTOSTART:-}" in
0 | n | no | false | False)
	skip "INFRA_AUTOSTART=${INFRA_AUTOSTART}, skipping infra start"
	exit 0
	;;
1 | y | yes | true | True)
	info "INFRA_AUTOSTART=${INFRA_AUTOSTART}, starting infra without prompt"
	;;
"")
	confirm "Start the infra (PostgreSQL, Temporal, RustFS)?" "infra start" || exit 0
	;;
*)
	fail "unsupported INFRA_AUTOSTART value: \`${INFRA_AUTOSTART}\`"
	;;
esac

wait_for "starting the infra stack (this may take some time on first run)"
docker compose -f "${COMPOSE_FILE}" up -d || fail "failed to start the infra stack"
ok "infra stack is up"
