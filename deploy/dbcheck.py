"""Database checks for the entrypoint, without needing the PostgreSQL client tools.
  dbcheck.py wait            wait until the server accepts connections (up to 5 minutes)
  dbcheck.py exists <name>   exit 0 if database <name> exists, 1 if not"""
import os
import sys
import time

import psycopg2

params = dict(host=os.environ.get('DB_HOST', 'db'), port=int(os.environ.get('DB_PORT', '5432')),
              user=os.environ.get('DB_USER', 'odoo'), password=os.environ['DB_PASSWORD'], dbname='postgres', connect_timeout=5)

if sys.argv[1] == 'wait':
    deadline = time.time() + 300
    while True:
        try:
            psycopg2.connect(**params).close()
            sys.exit(0)
        except psycopg2.OperationalError:
            if time.time() > deadline:
                sys.exit("The database did not become reachable within 5 minutes.")
            time.sleep(2)
elif sys.argv[1] == 'exists':
    with psycopg2.connect(**params) as conn, conn.cursor() as cr:
        cr.execute("SELECT 1 FROM pg_database WHERE datname = %s", [sys.argv[2]])
        sys.exit(0 if cr.fetchone() else 1)
elif sys.argv[1] == 'installed':
    # dbcheck.py installed <db> <module>: exit 0 if the module is installed in that database
    with psycopg2.connect(**dict(params, dbname=sys.argv[2])) as conn, conn.cursor() as cr:
        cr.execute("SELECT 1 FROM ir_module_module WHERE name = %s AND state = 'installed'", [sys.argv[3]])
        sys.exit(0 if cr.fetchone() else 1)
elif sys.argv[1] == 'drop':
    # dbcheck.py drop <db>: remove a database left half-built by a failed first start
    conn = psycopg2.connect(**params)
    conn.autocommit = True
    with conn.cursor() as cr:
        cr.execute('DROP DATABASE IF EXISTS "%s"' % sys.argv[2].replace('"', ''))
    conn.close()
