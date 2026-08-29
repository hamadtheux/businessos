FROM node:24.14.1-bookworm-slim AS build

ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    PORT=8080 \
    BASE_PATH=/

WORKDIR /workspace

RUN corepack enable && corepack prepare pnpm@11.5.2 --activate

COPY package.json pnpm-workspace.yaml pnpm-lock.yaml tsconfig.json tsconfig.base.json ./
COPY artifacts/ai-business-os/package.json ./artifacts/ai-business-os/package.json
RUN pnpm install --frozen-lockfile --filter @workspace/ai-business-os...

COPY artifacts/ai-business-os ./artifacts/ai-business-os
COPY lib/api-client-react ./lib/api-client-react

ARG VITE_API_BASE_URL=""
ARG VITE_WIDGET_API_BASE_URL=""
ARG VITE_WIDGET_APP_URL=""
ARG VITE_ENABLE_DEMO_DATA="false"
ENV NODE_ENV=production \
    VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_WIDGET_API_BASE_URL=$VITE_WIDGET_API_BASE_URL \
    VITE_WIDGET_APP_URL=$VITE_WIDGET_APP_URL \
    VITE_ENABLE_DEMO_DATA=$VITE_ENABLE_DEMO_DATA

RUN pnpm --filter @workspace/ai-business-os build

FROM nginxinc/nginx-unprivileged:1.29.1-alpine

ENV NGINX_API_UPSTREAM=http://api:8000

COPY artifacts/ai-business-os/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /workspace/artifacts/ai-business-os/dist/public /usr/share/nginx/html

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["wget", "--quiet", "--tries=1", "--spider", "http://127.0.0.1:8080/healthz"]
