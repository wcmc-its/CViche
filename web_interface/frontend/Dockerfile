# ---- Stage 1: Build ----
FROM node:20-alpine AS build

WORKDIR /app

# Copy package files first for layer caching
COPY package.json package-lock.json ./
RUN npm ci

# Copy source and build
COPY . .
RUN npm run build

# ---- Stage 2: Serve with nginx ----
FROM nginx:1.27-alpine AS production

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Copy custom nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy built assets from build stage
COPY --from=build /app/dist /usr/share/nginx/html

# Run unprivileged (#109): the pid file moves to /tmp, the only writable path
# under k8s's readOnlyRootFilesystem, and the `user` directive goes because it
# only applies to a root master. nginx.conf moves the temp paths to /tmp too.
RUN sed -i -e 's|^pid .*|pid /tmp/nginx.pid;|' -e '/^user /d' /etc/nginx/nginx.conf
USER nginx

# Expose HTTP port (unprivileged, so not 80)
EXPOSE 8080

# Health check: verify nginx responds
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q --spider http://localhost:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
