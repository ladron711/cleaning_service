import os

bind = "0.0.0.0:8000"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
timeout = 60
graceful_timeout = 30
errorlog = "-"
# Nginx handles access logs without query strings or request bodies.
accesslog = None
# Only Nginx can reach this container's private frontend network.
forwarded_allow_ips = "*"
