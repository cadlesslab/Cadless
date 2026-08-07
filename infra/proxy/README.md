# Cadless reverse proxy

The bundled `Caddyfile` listens on `:8080` and owns all `/apps/cadless` routing:

```
/apps/cadless/api/*  ->  strip /apps/cadless/api  ->  api:8000   (no re-prepend; SSE flushed)
/apps/cadless        ->  308 redirect to /apps/cadless/
/apps/cadless/*      ->  frontend:80   (no strip — SPA built with base /apps/cadless/)
everything else     ->  404
```

Only the `proxy` container is published on the host (`${CADLESS_PROXY_PORT:-8800}`).
`api`, `frontend`, and `worker` are internal to the compose network.

## Edge integration (the platform Caddy that owns your public domain)

Your public domain resolves to the platform Caddy, which terminates TLS and routes
by path. It must forward `/apps/cadless*` to this stack's bundled Caddy **without**
stripping the prefix (the bundled Caddy strips `/apps/cadless` internally).

### Single box (current): edge and stack on the same host

Add this block inside the `<your-domain> { … }` site in `/etc/caddy/Caddyfile`,
alongside your other `/apps/*` blocks:

```caddy
# Cadless — /apps/cadless* -> localhost:8800 (no prefix strip at the edge)
handle /apps/cadless* {
    reverse_proxy localhost:8800 {
        flush_interval -1
        header_up Connection {>Connection}
        header_up Upgrade {>Upgrade}
    }
}
```

Caddy orders `handle` blocks by matcher specificity, so `/apps/cadless*` is
evaluated before the catch-all and never affects any other `/apps/*` blocks you have.

### Separate VM

Point the edge at this host's IP and restrict inbound `8800` to the
platform-Caddy IP only:

```caddy
handle /apps/cadless* {
    reverse_proxy http://<CADLESS_HOST_IP>:8800 { flush_interval -1 }
}
```

> **`flush_interval -1` is REQUIRED, not optional.** The chat (`POST …/chat`)
> and generation (`GET …/generate/stream`) endpoints are Server-Sent Events.
> Caddy buffers proxied response bodies by default and **ignores** the
> `X-Accel-Buffering: no` header the API sets (that header is an nginx
> convention). Without `flush_interval -1` on **every** Caddy hop in front of
> the API (this bundled proxy AND the platform edge), SSE frames are withheld
> until the turn ends and the reply appears "all at once" instead of streaming
> token-by-token.

### Apply safely — additive, zero-downtime, reversible

```bash
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.pre-cadless.$(date +%Y%m%d-%H%M%S)
caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Roll back: remove the `handle /apps/cadless*` block (or restore the backup) and
`sudo systemctl reload caddy`.

## Security

The app has **no authentication** (single-user PoC). Keep `:8800` off the public
internet — on a single box the edge→stack hop stays on localhost; on a separate
VM, firewall `8800` to the platform-Caddy IP only.
