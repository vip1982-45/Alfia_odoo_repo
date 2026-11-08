# Deploying the Alfia stud farm demo

One server, one command. The package runs three containers: PostgreSQL, the Alfia system (Odoo 20 with the Alfia
modules, Arabic and right-to-left ready) and Caddy, which obtains an HTTPS certificate automatically.

## What you need

- A Linux server (cloud VM or on-premises): 4 vCPU, 8 GB RAM, 40 GB disk is comfortable for an evaluation.
- Docker Engine with the Compose plugin (`docker compose version` works).
- A domain name for the demo, e.g. `stud-demo.example.com`, whose DNS A record points to the server.
- Ports 80 and 443 open to the evaluators (80 is used for the certificate and redirects to HTTPS).

## Start it

    git clone <this repository> alfia && cd alfia
    git checkout claude/alfia-hardening        # or 20.0 once merged
    cd deploy
    cp .env.example .env
    # edit .env: SITE_ADDRESS, and four strong passwords (openssl rand -base64 24)
    docker compose up -d --build

The first start builds the image (about 10 minutes) and then the demonstration farm (about 3 minutes):

    docker compose logs -f odoo        # wait for "Demo farm ready"

Open `https://<SITE_ADDRESS>` and sign in. Evaluators use the logins in `demo/README.md` with `DEMO_PASSWORD`;
the `admin` account uses `ADMIN_PASSWORD` and should stay with the project team.

## Look after it

| Task | Command |
|---|---|
| See what is happening | `docker compose logs -f odoo` |
| Stop / start | `docker compose stop` / `docker compose start` |
| Back up the database | `docker compose exec db pg_dump -U odoo alfia_demo > alfia_demo_$(date +%F).sql` |
| Update to a newer version of the code | `git pull && docker compose up -d --build` then `docker compose exec odoo python odoo-bin -c /var/lib/alfia/odoo.conf -d alfia_demo -u alfia_security,alfia_equine,alfia_equine_ops,alfia_finance --stop-after-init` and `docker compose restart odoo` |
| Start the demo again from scratch | `docker compose down -v && docker compose up -d` (deletes everything evaluators entered) |

## Security of the demo server

- Odoo's database manager is switched off on the web; the master password (`MASTER_PASSWORD`) is not used for sign-in.
- The administrator's password is separate from the evaluators' shared password.
- All data is invented. Do not enter real client, financial or personal data on the demo server; for production,
  give every person their own account and password (the security officer creates them from access profiles) and
  switch off `LOAD_DEMO_FARM`.
- Keep the server's operating system and Docker updated, and restrict SSH to the IT team.

## Notes

- `LOAD_DEMO_FARM=0` starts an empty system (Qatar chart of accounts, all Alfia modules) instead of the demo farm.
- Arabic right-to-left screens need `rtlcss`, which the image includes.
- LDAP sign-in is not included in the image (its system libraries are left out); everything else is.
