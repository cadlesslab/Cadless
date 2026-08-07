# Frontend image: build the Vite app under a subpath, serve via nginx.
FROM node:22-slim AS build
WORKDIR /app
# The subpath this image is built for. Must end in '/'.
ARG BASE_PATH=/apps/cadless/
# Derived, not written again. As a separate literal it could be forgotten when
# BASE_PATH moved, and the result is worse than a broken page: the app loads
# and sends every request — including the POST /settings that carries the
# user's API key — to the old prefix, which on a shared host is somewhere else.
ARG VITE_API_BASE=${BASE_PATH}api
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN BASE_PATH="$BASE_PATH" VITE_API_BASE="$VITE_API_BASE" npm run build

FROM nginx:1.31-alpine
# Re-declared because an ARG does not cross a stage boundary. Omitting this
# would expand to the empty string and put the bundle at the server root,
# which serves a page that 404s every asset rather than failing the build.
ARG BASE_PATH=/apps/cadless/
# One parameter drives everything that has to agree: vite's base and the API
# base above, where the bundle lands, and the nginx location that serves it.
# They were four separate literals, so serving anywhere else meant editing
# files and hoping none was missed.
COPY --from=build /app/dist /usr/share/nginx/html${BASE_PATH}
COPY infra/nginx.conf /etc/nginx/conf.d/default.conf
# nginx.conf carries the default prefix so it stays a valid config to read on
# its own; a non-default BASE_PATH is substituted in here rather than becoming
# another place the prefix is written down.
#
# Both rejections below describe builds that would otherwise succeed and serve.
# The trailing slash is load-bearing because the replaced text ends in one, so
# `/apps/foo` splices `try_files ... /apps/fooindex.html`. And `&`, `|` and `\`
# are sed's own syntax in a replacement: `&` expands to the whole match, `|` is
# this expression's delimiter, `\` escapes. Any of them silently rewrites the
# config into something else.
RUN case "${BASE_PATH}" in \
	*[\\\&\|]*) echo "BASE_PATH must not contain \\ & or |: got '${BASE_PATH}'" >&2; exit 1 ;; \
	esac; \
	case "${BASE_PATH}" in \
	*/) ;; \
	*) echo "BASE_PATH must end with '/': got '${BASE_PATH}'" >&2; exit 1 ;; \
	esac; \
	sed -i "s|/apps/cadless/|${BASE_PATH}|g" /etc/nginx/conf.d/default.conf
EXPOSE 80
