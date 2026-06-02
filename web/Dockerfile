# mem-mcp web UI (Next.js 15) image.
#
# Server-side code reaches the API via MEM_MCP_API_URL (runtime env), so the
# same image works in any environment without a rebuild. Build with:
#   docker build -t mem-mcp-web web/
#
# syntax=docker/dockerfile:1.7

ARG NODE_VERSION=20

# ---------------------------------------------------------------------------
# Stage 1: deps — install node_modules (cached unless lockfile changes)
# ---------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ---------------------------------------------------------------------------
# Stage 2: build — compile the Next.js production bundle
# ---------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 3: runner — production server
# ---------------------------------------------------------------------------
FROM node:${NODE_VERSION}-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=8081 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 10002 nextjs \
    && adduser --system --uid 10002 --ingroup nextjs nextjs

# Copy only what `next start` needs to run.
COPY --from=build --chown=nextjs:nextjs /app/.next ./.next
COPY --from=build --chown=nextjs:nextjs /app/node_modules ./node_modules
COPY --from=build --chown=nextjs:nextjs /app/package.json ./package.json
COPY --from=build --chown=nextjs:nextjs /app/next.config.mjs ./next.config.mjs

USER nextjs
EXPOSE 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD wget -qO- http://127.0.0.1:8081/ >/dev/null 2>&1 || exit 1

CMD ["node", "node_modules/next/dist/bin/next", "start", "--hostname", "0.0.0.0", "--port", "8081"]
