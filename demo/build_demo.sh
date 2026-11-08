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

$PY odoo-bin db -c "$CONF" drop "$DB" 2>/dev/null || true
echo "Installing the Alfia modules into $DB (English; the seed adds Arabic)…"
$PY odoo-bin -c "$CONF" -d "$DB" -i alfia_equine_ops --stop-after-init --log-level=warn
echo "Loading the demonstration farm…"
$PY odoo-bin shell -c "$CONF" -d "$DB" --log-level=error < demo/seed_demo.py
echo "Done. Start the server with:"
echo "  $PY odoo-bin -c $CONF -d $DB --db-filter='^$DB\$'"
