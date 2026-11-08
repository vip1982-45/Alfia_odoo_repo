#!/usr/bin/env bash
# Build (or rebuild) the Alfia demonstration database from scratch.
#
#   demo/build_demo.sh <odoo-config-file> [database-name]
#
# Needs: PostgreSQL reachable with the credentials in the config file, Python with Odoo's requirements installed
# (python3 -m pip install -r requirements.txt), and `alfia_addons` on the config's addons_path.
# WARNING: an existing database with the same name is dropped.
set -euo pipefail
CONF=${1:?usage: demo/build_demo.sh <odoo-config-file> [database-name]}
DB=${2:-alfia_demo}
PY=${PYTHON:-python3}
cd "$(dirname "$0")/.."

if ! command -v rtlcss >/dev/null 2>&1; then
  echo "WARNING: 'rtlcss' is not installed, so Arabic screens will be laid out left to right."
  echo "         Install it once on the server (needs Node.js):  npm install -g rtlcss@4.3.0"
fi

$PY odoo-bin db -c "$CONF" drop "$DB" 2>/dev/null || true
echo "Creating $DB for a company in Qatar (so the Qatar chart of accounts is used)…"
$PY odoo-bin -c "$CONF" -d "$DB" -i base --stop-after-init --log-level=warn
$PY odoo-bin shell -c "$CONF" -d "$DB" --log-level=error < demo/set_country.py
echo "Installing the Alfia modules (English; the seed adds Arabic)…"
$PY odoo-bin -c "$CONF" -d "$DB" -i alfia_finance --stop-after-init --log-level=warn
echo "Loading the demonstration farm…"
$PY odoo-bin shell -c "$CONF" -d "$DB" --log-level=error < demo/seed_demo.py
echo "Done. Start the server with:"
echo "  $PY odoo-bin -c $CONF -d $DB --db-filter='^$DB\$'"
