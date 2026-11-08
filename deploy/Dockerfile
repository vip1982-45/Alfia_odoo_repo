# Alfia stud farm system: Odoo 20 with the Alfia modules, Arabic and right-to-left ready.
# Built from the repository root:  docker compose -f deploy/docker-compose.yml build
# Needs only Docker Hub, PyPI and the npm registry (no operating-system package downloads).
FROM node:22-bookworm-slim AS node

FROM python:3.13-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# Node.js, for rtlcss: Odoo uses it to build the right-to-left (Arabic) screen styles.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && npm install -g rtlcss@4.3.0 && rm -rf /root/.npm

WORKDIR /opt/alfia
COPY requirements.txt .
# python-ldap is left out: it needs LDAP system libraries and is used only by LDAP sign-in, which this system does not use.
RUN grep -viE '^python-ldap' requirements.txt > /tmp/requirements.txt && pip install -r /tmp/requirements.txt

COPY odoo-bin setup.cfg ./
COPY odoo odoo
COPY addons addons
COPY alfia_addons alfia_addons
COPY demo demo
COPY deploy/entrypoint.sh deploy/dbcheck.py /opt/alfia/deploy/

# Translations: Arabic only (English is the source language). Keeps the image smaller.
RUN find addons odoo/addons alfia_addons -path '*/i18n*/*.po' ! -name 'ar.po' -delete \
 && useradd --system --create-home --home-dir /var/lib/alfia alfia \
 && mkdir -p /var/lib/alfia/data && chown -R alfia /var/lib/alfia && chmod 755 /opt/alfia/deploy/entrypoint.sh

USER alfia
EXPOSE 8069
ENTRYPOINT ["/opt/alfia/deploy/entrypoint.sh"]
