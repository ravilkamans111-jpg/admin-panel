# --- deps -------------------------------------------------------------
FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

# --- build --------------------------------------------------------------
FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* vars are inlined into the JS bundle at build time, so the public
# API URL must be supplied when the image is built (--build-arg).
ARG NEXT_PUBLIC_API_BASE_URL
RUN test -n "$NEXT_PUBLIC_API_BASE_URL" || (echo "build-arg NEXT_PUBLIC_API_BASE_URL is required" && exit 1)
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
RUN npm run build

# --- run ------------------------------------------------------------------
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# next.config.mjs sets `output: "standalone"`, which produces a minimal
# server bundle in .next/standalone that doesn't need the full node_modules.
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

EXPOSE 3000
CMD ["node", "server.js"]
