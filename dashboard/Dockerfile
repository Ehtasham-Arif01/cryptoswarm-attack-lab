# Static dashboard + reverse proxy to the coordinator. Runs as non-root (uid 101) on port 8080.
FROM nginxinc/nginx-unprivileged:1.31-alpine-slim
# Pull in Alpine security fixes released after the base image was built (Trivy found musl + zlib CVEs).
USER root
RUN apk upgrade --no-cache
USER 101
ENV DNS_RESOLVER=127.0.0.11 COORDINATOR_HOST=coordinator
COPY dashboard/nginx.conf.template /etc/nginx/templates/default.conf.template
COPY dashboard/index.html dashboard/app.js dashboard/style.css /usr/share/nginx/html/
COPY dashboard/vendor /usr/share/nginx/html/vendor
COPY dashboard/fonts /usr/share/nginx/html/fonts
EXPOSE 8080
