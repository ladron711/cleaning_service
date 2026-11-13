#!/bin/sh
set -eu
# Pre-create only the test DB; the runtime role retains NOCREATEDB.
docker compose exec -T db psql -U postgres -d postgres -v ON_ERROR_STOP=1 <<'SQL'
\getenv app_db APP_DB_NAME
\getenv app_user APP_DB_USER
SELECT format('CREATE DATABASE %I OWNER %I', 'test_' || :'app_db', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'test_' || :'app_db') \gexec
SQL
# Django test client uses HTTP; overrides affect only the test process.
docker compose exec -T -e DJANGO_HTTPS=False -e DJANGO_HSTS_SECONDS=0 web \
    python manage.py test --keepdb "$@"
