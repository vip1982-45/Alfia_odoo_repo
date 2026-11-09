#!/bin/sh
# Writes the Odoo configuration from the environment, builds the demo farm on first start, then runs the server.
set -eu
: "${DB_HOST:=db}" "${DB_PORT:=5432}" "${DB_USER:=odoo}" "${DB_NAME:=alfia_demo}"
: "${DB_PASSWORD:?Set DB_PASSWORD in deploy/.env}" "${MASTER_PASSWORD:?Set MASTER_PASSWORD in deploy/.env}"
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
if ! python deploy/dbcheck.py exists "$DB_NAME"; then
  echo "First start: installing the Alfia modules (several minutes)…"
  python odoo-bin -c "$CONF" -d "$DB_NAME" -i base --stop-after-init --log-level=warn || true
  python odoo-bin shell -c "$CONF" -d "$DB_NAME" --log-level=error < demo/set_country.py || true
  python odoo-bin -c "$CONF" -d "$DB_NAME" -i alfia_hr --stop-after-init --log-level=warn || true
  if ! python deploy/dbcheck.py installed "$DB_NAME" alfia_hr; then
    echo "ERROR: installing the Alfia modules failed (see the messages above). Removing the half-built database so the next start retries." >&2
    python deploy/dbcheck.py drop "$DB_NAME"
    exit 1
  fi
  if [ "${LOAD_DEMO_FARM:-1}" = "1" ]; then
    echo "Loading the demonstration farm…"
    if ! python odoo-bin shell -c "$CONF" -d "$DB_NAME" --log-level=error < demo/seed_demo.py | tee /tmp/seed.log; then :; fi
    if ! grep -q "Demo farm ready" /tmp/seed.log; then
      echo "ERROR: loading the demonstration farm failed. Removing the database so the next start retries." >&2
      python deploy/dbcheck.py drop "$DB_NAME"
      exit 1
    fi
  fi
fi
exec python odoo-bin -c "$CONF"
