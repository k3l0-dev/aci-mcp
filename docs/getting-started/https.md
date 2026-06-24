# HTTPS with Caddy

Production deployment — Caddy terminates TLS and proxies to the MCP server.
The MCP container is never exposed directly on a host port.

---

## Architecture

```mermaid
graph LR
    subgraph internet["Internet / LAN"]
        client["LLM Client"]
    end

    subgraph host["Docker host"]
        subgraph compose["docker-compose stack"]
            caddy["Caddy\nports 80, 443\nTLS termination"]
            mcp["aci-mcp\nport 8000 (internal only)"]
        end
        net["internal bridge network"]
    end

    subgraph apic_net["Network"]
        apic["Cisco APIC\nHTTPS"]
    end

    client -->|"HTTPS :443"| caddy
    caddy -->|"HTTP :8000 (internal)"| mcp
    caddy -.->|"ACME challenge (public domain)"| internet
    mcp -->|"HTTPS"| apic
    caddy --- net
    mcp --- net
```

---

## Quick start

### 1 — Prepare .env

```dotenv
APIC_HOST=https://your-apic.example.com
APIC_USER=admin
APIC_PASSWORD=your_password

MCP_API_KEYS=your-generated-token-here
MCP_DOMAIN=mcp.yourdomain.com
```

Generate a token:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 2 — Start the stack

```bash
docker compose -f mcp/deploy/docker-compose.yml up -d
```

Caddy waits for the MCP container to pass its healthcheck (`GET /health`) before accepting traffic.

### 3 — Verify

```bash
# Check both containers are up and healthy
docker compose -f mcp/deploy/docker-compose.yml ps

# Test through Caddy
curl -H "Authorization: Bearer your-generated-token-here" \
     https://mcp.yourdomain.com/health
```

---

## Certificate modes

### Public domain — Let's Encrypt (automatic)

Set `MCP_DOMAIN` to a real public hostname. Caddy obtains and renews certificates automatically via ACME.

Requirements:
- Ports 80 and 443 reachable from the internet
- DNS A record for `MCP_DOMAIN` pointing to the host

No extra configuration needed — the `Caddyfile` handles it.

### Internal / LAN — Caddy built-in CA

Set `MCP_DOMAIN` to an internal FQDN (e.g. `mcp.corp.internal`). Caddy issues a certificate from its own CA.

Add the CA to your trust store **once**:

```bash
# Trust Caddy's CA on the Docker host (system-wide)
docker compose -f mcp/deploy/docker-compose.yml exec caddy caddy trust

# macOS client machines
security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain caddy_root.crt

# Windows client machines (PowerShell)
Import-Certificate -FilePath caddy_root.crt -CertStoreLocation Cert:\LocalMachine\Root
```

---

## Security headers

The `Caddyfile` adds these headers to every response:

| Header | Value |
|---|---|
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Server` | *(removed)* |

---

## Persistent volumes

Caddy stores TLS certificates and ACME state in Docker volumes:

| Volume | Purpose |
|---|---|
| `caddy_data` | TLS certificates, ACME account keys |
| `caddy_config` | Caddy runtime config |

These volumes persist across `docker compose down` and restarts. **Do not delete `caddy_data`** — it forces certificate reissuance and may hit Let's Encrypt rate limits.

---

## Logs

```bash
# Caddy access logs (structured JSON)
docker compose -f mcp/deploy/docker-compose.yml logs caddy

# MCP server logs
docker compose -f mcp/deploy/docker-compose.yml logs mcp

# Follow both
docker compose -f mcp/deploy/docker-compose.yml logs -f
```

---

## Updating

```bash
# Rebuild aci-mcp image with latest source
docker compose -f mcp/deploy/docker-compose.yml build mcp

# Restart only the MCP container (Caddy stays up — zero-downtime for TLS)
docker compose -f mcp/deploy/docker-compose.yml up -d --no-deps mcp
```
