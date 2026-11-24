#!/bin/sh
# Writes the Odoo configuration from the environment, builds the demo farm on first start, then runs the server.
set -eu
: "${DB_HOST:=db}" "${DB_PORT:=5432}" "${DB_USER:=odoo}" "${DB_NAME:=alfia_demo}"
: "${DB_PASSWORD:?Set DB_PASSWORD in deploy/.env}" "${MASTER_PASSWORD:?Set MASTER_PASSWORD in deploy/.env}"
: "${ALFIA_ADMIN_PASSWORD:?Set ADMIN_PASSWORD in deploy/.env (the administrator must not keep admin/admin)}"
case "$ALFIA_ADMIN_PASSWORD" in admin|change-me-admin) echo "ERROR: choose a real ADMIN_PASSWORD." >&2; exit 1;; esac
if [ "${#ALFIA_ADMIN_PASSWORD}" -lt 12 ]; then echo "ERROR: ADMIN_PASSWORD must be at least 12 characters." >&2; exit 1; fi
CONF=/var/lib/alfia/odoo.conf
cat > "$CONF" <<CFG
[options]
db_host = $DB_HOST
db_port = $DB_PORT
db_user = $DB_USER
db_password = $DB_PASSWORD
db_name = $DB_NAME
dbfilter = ^$DB_NAME\$
list_db = False
admin_passwd = $MASTER_PASSWORD
data_dir = /var/lib/alfia/data
addons_path = /opt/alfia/addons,/opt/alfia/odoo/addons,/opt/alfia/alfia_addons
proxy_mode = True
http_interface = 0.0.0.0
http_port = 8069
workers = 0
max_cron_threads = 1
without_demo = True
CFG
chmod 600 "$CONF"
export DB_HOST DB_PORT DB_USER DB_PASSWORD

cd /opt/alfia
echo "Waiting for the database…"
python deploy/dbcheck.py wait
LVL="${INIT_LOG_LEVEL:-warn}"
if python deploy/dbcheck.py exists "$DB_NAME"; then
  if [ "${INIT_ONLY:-0}" = "1" ]; then
    # An existing database (a redeploy): bring the Alfia modules up to this image's code, keeping every record.
    echo "$(date -u +%H:%M:%S) Existing database: upgrading the Alfia modules…"
    # -i adds modules this image brings for the first time (installed ones are left as they are by -i).
    python odoo-bin -c "$CONF" -d "$DB_NAME" -i alfia_controls -u alfia_security,alfia_equine,alfia_equine_ops,alfia_finance,alfia_hr,alfia_controls \
      --stop-after-init --log-level="$LVL"
  fi
else
  echo "$(date -u +%H:%M:%S) First start: installing the base module…"
  python odoo-bin -c "$CONF" -d "$DB_NAME" -i base --stop-after-init --log-level="$LVL" || true
  python odoo-bin shell -c "$CONF" -d "$DB_NAME" --log-level=error < demo/set_country.py || true
  echo "$(date -u +%H:%M:%S) Installing the Alfia modules (several minutes)…"
  python odoo-bin -c "$CONF" -d "$DB_NAME" -i alfia_hr,alfia_controls --stop-after-init --log-level="$LVL" || true
  if ! python deploy/dbcheck.py installed "$DB_NAME" alfia_hr || ! python deploy/dbcheck.py installed "$DB_NAME" alfia_controls; then
    echo "ERROR: installing the Alfia modules failed (see the messages above). Removing the half-built database so the next start retries." >&2
    python deploy/dbcheck.py drop "$DB_NAME"
    exit 1
  fi
  if [ "${LOAD_DEMO_FARM:-1}" = "1" ]; then
    echo "$(date -u +%H:%M:%S) Loading the demonstration farm…"
    if ! python odoo-bin shell -c "$CONF" -d "$DB_NAME" --log-level=error < demo/seed_demo.py | tee /tmp/seed.log; then :; fi
    if ! grep -q "Demo farm ready" /tmp/seed.log; then
      echo "ERROR: loading the demonstration farm failed. Removing the database so the next start retries." >&2
      python deploy/dbcheck.py drop "$DB_NAME"
      exit 1
    fi
  fi
fi
# The administrator never keeps Odoo's default password, whether or not the demo farm was loaded.
python odoo-bin shell -c "$CONF" -d "$DB_NAME" --log-level=error < deploy/admin_password.py || { echo "ERROR: could not secure the administrator account" >&2; exit 1; }
# Data corrections that later releases need (safe to run again; does nothing once applied).
if [ "${INIT_ONLY:-0}" = "1" ]; then
  python odoo-bin shell -c "$CONF" -d "$DB_NAME" --log-level=error < demo/data_fixes.py || { echo "ERROR: data fixes failed" >&2; exit 1; }
fi
# INIT_ONLY=1 (a one-off job, e.g. on Azure Container Apps): build the database and stop, without serving.
if [ "${LOAD_DEMO_FARM:-1}" = "1" ]; then
  # The trial power user (every business function, no administration); safe to run on every start.
  python odoo-bin shell -c "$CONF" -d "$DB_NAME" --log-level=error < demo/power_user.py || echo "WARNING: could not create the power user" >&2
  # TEST records inside each role's sites, for retests the role accounts must be able to reach (safe to run again).
  python odoo-bin shell -c "$CONF" -d "$DB_NAME" --log-level=error < demo/retest_fixtures.py || echo "WARNING: could not create the retest fixtures" >&2
fi
if [ "${INIT_ONLY:-0}" = "1" ]; then
  echo "Initialisation finished."
  exit 0
fi
exec python odoo-bin -c "$CONF"
