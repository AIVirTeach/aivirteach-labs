#!/bin/sh
##
## 10-configure-cors.sh
##
## Writes an nginx server block that reverse-proxies to the `guacamole`
## container and adds Access-Control-Allow-Origin (and friends) for origins
## listed in AIVIRTEACH_GUACAMOLE_CORS_ORIGINS - see compose.yaml for why
## this lives in front of Guacamole instead of inside it.
##
## Runs automatically: the official nginx image executes every executable
## *.sh file under /docker-entrypoint.d/ before starting nginx.
##
set -e

: "${AIVIRTEACH_GUACAMOLE_CORS_ORIGINS:?Set AIVIRTEACH_GUACAMOLE_CORS_ORIGINS (comma-separated list of origins allowed to call the Guacamole REST API cross-origin, e.g. https://app.aivirteach.com)}"

# "https://a.com,http://b.com:3001" -> "https://a\.com|http://b\.com:3001",
# used inside an nginx map's regex key below.
CORS_ORIGIN_PATTERN=$(printf '%s' "$AIVIRTEACH_GUACAMOLE_CORS_ORIGINS" | sed -e 's/[.]/\\./g' -e 's/,/|/g')

cat > /etc/nginx/conf.d/guacamole.conf <<EOF
map \$http_origin \$cors_allow_origin {
    default "";
    "~^(${CORS_ORIGIN_PATTERN})\$" \$http_origin;
}

map \$http_upgrade \$connection_upgrade {
    default upgrade;
    ''      close;
}

server {
    listen 8080;

    location / {
        proxy_pass http://guacamole:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection \$connection_upgrade;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;

        add_header Access-Control-Allow-Origin \$cors_allow_origin always;
        add_header Access-Control-Allow-Methods "GET, POST, HEAD, OPTIONS" always;
        add_header Access-Control-Allow-Headers "Content-Type, X-Requested-With, Accept, Origin, Guacamole-Cookies-Disabled" always;
        add_header Vary "Origin" always;

        if (\$request_method = OPTIONS) {
            add_header Access-Control-Allow-Origin \$cors_allow_origin always;
            add_header Access-Control-Allow-Methods "GET, POST, HEAD, OPTIONS" always;
            add_header Access-Control-Allow-Headers "Content-Type, X-Requested-With, Accept, Origin, Guacamole-Cookies-Disabled" always;
            add_header Content-Length 0;
            return 204;
        }
    }
}
EOF
