FROM node:22.22.2-alpine AS build

RUN corepack enable

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
RUN pnpm fetch --frozen-lockfile

COPY . .
RUN pnpm install --offline --frozen-lockfile

ARG VITE_SERVICE_NAME
ARG VITE_HYPERDX_URL
ARG VITE_HYPERDX_API_KEY

RUN pnpm build

FROM node:22.22.2-alpine

WORKDIR /app

ENV NODE_ENV=production

COPY --from=build --chown=node:node /app/.output ./.output

USER node

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD ["wget", "-q", "--spider", "http://127.0.0.1:3000/"]

CMD ["node", ".output/server/index.mjs"]
