#!/bin/sh
set -eu
case "$SITE_DOMAIN" in ''|*[!a-zA-Z0-9.-]*) echo 'Invalid SITE_DOMAIN' >&2; exit 1;; esac
case "$HTTPS_PORT" in ''|*[!0-9]*) echo 'Invalid HTTPS_PORT' >&2; exit 1;; esac
case "$TLS_MODE" in local|letsencrypt) ;; *) echo 'TLS_MODE must be local or letsencrypt' >&2; exit 1;; esac
if [ "$TLS_MODE" = letsencrypt ] && { [ "$SITE_DOMAIN" = localhost ] || [ -z "$ACME_EMAIL" ]; }; then
    echo 'Let’s Encrypt requires a public domain and ACME_EMAIL' >&2
    exit 1
fi
mkdir -p /etc/letsencrypt/local /etc/letsencrypt/active /var/www/acme
rm -f /tmp/tls-ready
make_local_certificate() {
    openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
        -keyout /etc/letsencrypt/local/privkey.pem \
        -out /etc/letsencrypt/local/fullchain.pem \
        -subj "/CN=$SITE_DOMAIN" \
        -addext "subjectAltName=DNS:$SITE_DOMAIN,DNS:localhost,IP:127.0.0.1" 2>/dev/null
    chmod 600 /etc/letsencrypt/local/privkey.pem
}
if ! openssl x509 -checkend 2592000 -noout -in /etc/letsencrypt/local/fullchain.pem >/dev/null 2>&1; then
    make_local_certificate
fi
activate() {
    ln -sf "$1/fullchain.pem" /etc/letsencrypt/active/fullchain.pem
    ln -sf "$1/privkey.pem" /etc/letsencrypt/active/privkey.pem
}
activate /etc/letsencrypt/local
if [ "$TLS_MODE" = letsencrypt ] && [ -f "/etc/letsencrypt/live/$SITE_DOMAIN/fullchain.pem" ]; then
    activate "/etc/letsencrypt/live/$SITE_DOMAIN"
fi
envsubst '${SITE_DOMAIN} ${HTTPS_PORT}' < /etc/nginx/site.conf.template > /etc/nginx/conf.d/default.conf
nginx -t
nginx -g 'daemon off;' &
nginx_pid=$!
renew_certificates() {
    while :; do
        if [ "$TLS_MODE" = letsencrypt ]; then
            if certbot certonly --non-interactive --agree-tos --webroot -w /var/www/acme \
                --email "$ACME_EMAIL" --cert-name "$SITE_DOMAIN" -d "$SITE_DOMAIN" --keep-until-expiring; then
                activate "/etc/letsencrypt/live/$SITE_DOMAIN"
                nginx -t && nginx -s reload
                touch /tmp/tls-ready
            else
                echo 'Certificate issuance/renewal failed; retrying in one hour' >&2
                sleep 3600 & wait $!
                continue
            fi
        else
            if ! openssl x509 -checkend 2592000 -noout -in /etc/letsencrypt/local/fullchain.pem >/dev/null 2>&1; then
                make_local_certificate
                nginx -t && nginx -s reload
            fi
            touch /tmp/tls-ready
        fi
        sleep 43200 & wait $!
    done
}
renew_certificates &
renew_pid=$!
stop() { kill "$renew_pid" 2>/dev/null || true; kill -QUIT "$nginx_pid" 2>/dev/null || true; }
trap stop TERM INT
wait "$nginx_pid"
stop
