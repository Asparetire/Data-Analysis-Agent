# Multi-stage: build the SPA in node, serve the static dist/ with nginx.
# This image also reverse-proxies /api to the backend, so compose has one
# fewer service than the old `npm run dev` setup.

FROM node:18-alpine AS builder

WORKDIR /app

# Copy lockfile + manifest first to leverage layer caching.
COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build


FROM nginx:alpine AS runtime

# Static assets served from the nginx default root.
COPY --from=builder /app/dist /usr/share/nginx/html

# nginx config — serves SPA + proxies /api to the backend service.
# The compose network resolves `backend` to the backend container's IP.
# The file is a template: ${METRICS_ALLOW_CIDR} is substituted at container
# start from the env var (defaults to 127.0.0.1/32 so /metrics is locked
# down to loopback unless the operator opens it up).
COPY nginx/frontend.conf /etc/nginx/templates/default.conf.template

# nginx:alpine ships with the official entrypoint that runs envsubst on
# /etc/nginx/templates/*.template → /etc/nginx/conf.d/*. The default
# METRICS_ALLOW_CIDR here matches the deny-all-but-loopback posture; the
# compose file overrides it via the environment block.
ENV METRICS_ALLOW_CIDR=127.0.0.1/32

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://localhost/ >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
