# Pin the official Node base by immutable multi-arch digest so a rebuild cannot
# silently pick up a different image under the same mutable tag.
ARG NODE_IMAGE=node:22-slim@sha256:c29352e5563688e726158e23480bc2d4bd0af23bbdd055f8b837a12ecfd6a2a1

# ---- deps ----
FROM ${NODE_IMAGE} AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci --ignore-scripts \
    && npm cache clean --force

# ---- build ----
FROM ${NODE_IMAGE} AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000/api
ARG NEXT_PUBLIC_SENTRY_DSN=
ARG NEXT_PUBLIC_APP_RELEASE=development
ARG NEXT_PUBLIC_APP_ENVIRONMENT=development
ARG NEXT_PUBLIC_SENTRY_ENABLE_LOGS=0
ARG NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE=0
ARG SENTRY_ORG=
ARG SENTRY_PROJECT=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_SENTRY_DSN=$NEXT_PUBLIC_SENTRY_DSN
ENV NEXT_PUBLIC_APP_RELEASE=$NEXT_PUBLIC_APP_RELEASE
ENV NEXT_PUBLIC_APP_ENVIRONMENT=$NEXT_PUBLIC_APP_ENVIRONMENT
ENV NEXT_PUBLIC_SENTRY_ENABLE_LOGS=$NEXT_PUBLIC_SENTRY_ENABLE_LOGS
ENV NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE=$NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE
ENV SENTRY_ORG=$SENTRY_ORG
ENV SENTRY_PROJECT=$SENTRY_PROJECT
# The upload token exists only for this RUN invocation and is never an ARG,
# ENV layer, copied artifact, or runtime-container variable.
RUN --mount=type=secret,id=sentry_auth_token,required=false \
    if [ -f /run/secrets/sentry_auth_token ]; then \
      export SENTRY_AUTH_TOKEN="$(cat /run/secrets/sentry_auth_token)"; \
    fi; \
    npm run build

# ---- run ----
FROM ${NODE_IMAGE} AS runner
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/public ./public
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
