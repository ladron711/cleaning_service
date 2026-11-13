FROM nginx:stable-alpine
RUN apk add --no-cache certbot openssl
COPY default.conf.template /etc/nginx/site.conf.template
COPY start.sh /usr/local/bin/start-nginx
ENTRYPOINT ["/bin/sh", "/usr/local/bin/start-nginx"]
