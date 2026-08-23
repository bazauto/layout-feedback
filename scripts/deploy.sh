#!/usr/bin/env bash
# Deploy one node to its Pico on the bench machine.
#
#   bash scripts/deploy.sh io-node
#   bash scripts/deploy.sh io-node --dry-run
#
# The Pico is not plugged into this machine. Every deploy stages the files on the bench
# over rsync, then drives mpremote there over USB.
set -uo pipefail

BENCH="${LAYOUT_FEEDBACK_BENCH:-pbarrett@172.18.10.240}"
STAGING="/tmp/layout-feedback-deploy"

# Address the board by its by-id string, never ttyACM*. The numbers move between reboots
# and two of the three USB serial devices on that box belong to other projects — one of
# them is PicoDCC's debug probe.
BOARD_ID="${LAYOUT_FEEDBACK_BOARD_ID:-0d9a62134acbf42d}"

NODE="${1:-}"
DRY_RUN=0
[ "${2:-}" = "--dry-run" ] && DRY_RUN=1

usage() {
  echo "usage: bash scripts/deploy.sh <node> [--dry-run]" >&2
  echo "nodes:" >&2
  for d in src/apps/*/; do [ -d "$d" ] && echo "  $(basename "$d")" >&2; done
  exit 2
}

[ -n "$NODE" ] || usage
APP_DIR="src/apps/$NODE"
[ -d "$APP_DIR" ] || { echo "no such node: $NODE" >&2; usage; }
[ -f "$APP_DIR/main.py" ] || { echo "$APP_DIR has no main.py" >&2; exit 1; }

ROOT=$(git rev-parse --show-toplevel) || exit 1
cd "$ROOT" || exit 1

# --- Refuse to deploy something the tests reject ----------------------------
#
# The suite validates the very config being deployed — sensor ids, pin clashes, the
# allow-list. Deploying past a red suite means finding out on the broker instead, where
# the symptom is silence.
echo "== host tests =="
if ! python -m pytest; then
  echo "tests are failing — refusing to deploy" >&2
  exit 1
fi

echo "== staging to $BENCH:$STAGING/$NODE =="
if [ "$DRY_RUN" = 1 ]; then
  echo "(dry run) would rsync:"
  echo "   src/lib/        -> $STAGING/$NODE/lib/"
  echo "   $APP_DIR/  -> $STAGING/$NODE/app/"
  echo "(dry run) would then copy to the board and soft-reset:"
  echo "   /lib/*.py from lib/, /main.py and /config.py from app/"
  exit 0
fi

ssh "$BENCH" "bash -lc 'rm -rf $STAGING/$NODE && mkdir -p $STAGING/$NODE/lib $STAGING/$NODE/app'" || exit 1
rsync -az --delete src/lib/ "$BENCH:$STAGING/$NODE/lib/" || exit 1
rsync -az --delete "$APP_DIR/" "$BENCH:$STAGING/$NODE/app/" || exit 1

echo "== copying to the board =="
# Note on sys.path: MicroPython searches '' (the filesystem root) BEFORE /lib. A stale
# copy of a module at the root therefore shadows the one just deployed to /lib, silently
# running old code. The remote block below removes the root .py files it does not own
# before copying, which is what stops that.
ssh "$BENCH" 'bash -s' <<REMOTE
set -uo pipefail
cd "$STAGING/$NODE" || exit 1
MP="mpremote connect id:$BOARD_ID"

echo "-- clearing shadowing modules from the board root --"
# Everything the node needs lives in /lib or is main.py/config.py. Any other .py at the
# root is left over from an earlier layout and can only shadow or confuse.
for f in \$(\$MP fs ls 2>/dev/null | awk '{print \$2}' | grep '\.py\$'); do
  case "\$f" in
    main.py|config.py) continue ;;
  esac
  echo "   rm :\$f"
  \$MP fs rm ":\$f" >/dev/null 2>&1 || true
done

echo "-- /lib --"
\$MP fs mkdir :/lib >/dev/null 2>&1 || true
for f in lib/*.py; do
  echo "   \$f -> :/lib/\$(basename \$f)"
  \$MP fs cp "\$f" ":/lib/\$(basename \$f)" || exit 1
done

echo "-- app --"
for f in app/*.py; do
  echo "   \$f -> :/\$(basename \$f)"
  \$MP fs cp "\$f" ":/\$(basename \$f)" || exit 1
done

echo "-- reset --"
\$MP reset
REMOTE
rc=$?

if [ "$rc" != 0 ]; then
  echo "deploy FAILED (rc=$rc)" >&2
  exit "$rc"
fi

echo
echo "Deployed $NODE. The board is running it now."
echo "Watch what it publishes:"
echo "  ssh $BENCH 'bash -lc \"mosquitto_sub -h localhost -t layout/# -v -W 40\"'"
