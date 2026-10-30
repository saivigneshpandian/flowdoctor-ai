# The Next.js frontend. Build from the repository root.
FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# These are compiled into the build, so setting them only at runtime has no effect:
# Next.js fixes its rewrites at build time and inlines NEXT_PUBLIC_* into the browser code.
ARG BACKEND_URL=http://backend:8000
ARG NEXT_PUBLIC_USE_MOCKS=false
ARG NEXT_PUBLIC_DEMO_HINT=false
ARG NEXT_PUBLIC_DEMO_EMAIL=
ARG NEXT_PUBLIC_DEMO_PASSWORD=
ENV BACKEND_URL=$BACKEND_URL \
    NEXT_PUBLIC_USE_MOCKS=$NEXT_PUBLIC_USE_MOCKS \
    NEXT_PUBLIC_DEMO_HINT=$NEXT_PUBLIC_DEMO_HINT \
    NEXT_PUBLIC_DEMO_EMAIL=$NEXT_PUBLIC_DEMO_EMAIL \
    NEXT_PUBLIC_DEMO_PASSWORD=$NEXT_PUBLIC_DEMO_PASSWORD \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_OPTIONS=--max-old-space-size=1536
RUN npm run build

FROM node:22-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
