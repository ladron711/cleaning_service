#!/bin/sh
set -eu
# psql reads credentials from the environment, never shell command arguments.
psql --username "$POSTGRES_USER" --dbname postgres --set ON_ERROR_STOP=1 <<'SQL'
\getenv app_user APP_DB_USER
\getenv app_password APP_DB_PASSWORD
\getenv app_db APP_DB_NAME
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD %L', :'app_user', :'app_password') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user') \gexec
SQL
