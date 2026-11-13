"""Bounded DB readiness check; prepare schema/assets only for the web server."""
import os
import subprocess
import sys
import time

import psycopg2


def wait_for_database():
    deadline = time.monotonic() + 60
    while True:
        try:
            with psycopg2.connect(
                dbname=os.environ["POSTGRES_DB"], user=os.environ["POSTGRES_USER"],
                password=os.environ["POSTGRES_PASSWORD"], host=os.environ["POSTGRES_HOST"],
                port=os.environ["POSTGRES_PORT"], connect_timeout=3,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
            return
        except psycopg2.OperationalError:
            if time.monotonic() >= deadline:
                raise SystemExit("Database unavailable after 60 seconds")
            time.sleep(1)


if __name__ == "__main__":
    wait_for_database()
    command = sys.argv[1:]
    if not command:
        raise SystemExit("A command is required")
    if command[0] == "gunicorn":
        subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True)
        subprocess.run([sys.executable, "manage.py", "collectstatic", "--noinput"], check=True)
    os.execvp(command[0], command)
