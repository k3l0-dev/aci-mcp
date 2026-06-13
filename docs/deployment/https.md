# HTTPS with Caddy

Production deployment — Caddy terminates TLS and proxies to the MCP server. The MCP container is never exposed directly on a host port.

---

## Architecture

```mermaid
graph LR
    subgraph internet["Internet / LAN"]
        client["LLM Client"]
    end

    subgraph host["Docker host"]
        subgraph compose["docker-compose stack"]
            caddy["Caddy container<br/>ports: 80, 443<br/>TLS termination"]
            mcp["aci-mcp container<br/>port 8000 (internal only)"]
        end
        net["internal bridge network"]
    end

    subgraph apic_net["Network"]
        apic["Cisco APIC<br/>HTTPS"]
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
APIC_HOST=10.41.71.11
APIC_USER=admin
APIC_PASSWORD=Cisco1234!

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

### 3 — Verify

```bash
# Check both containers are healthy
docker compose -f mcp/deploy/docker-compose.yml ps

# Test authentication
curl -H "Authorization: Bearer your-generated-token-here" \
     https://mcp.yourdomain.com/mcp
```

---

## Certificate modes

### Public domain — Let's Encrypt (automatic)

Set `MCP_DOMAIN` to a real public hostname. Caddy obtains and renews certificates automatically via ACME.

Requirements:
- Ports 80 and 443 reachable from the internet
- DNS A record for `MCP_DOMAIN` pointing to the host

No extra configuration needed — the `Caddyfile` handles it.

### Internal / LAN — Caddy's built-in CA

Set `MCP_DOMAIN` to an internal FQDN (e.g. `mcp.corp.internal`). Caddy issues a certificate from its own CA.

Add the CA to your trust store **once**:

```bash
# On the Docker host — trusts Caddy's CA system-wide
docker compose -f mcp/deploy/docker-compose.yml exec caddy caddy trust

# On each client machine
# macOS
security add-trusted-cert -d -r trustRoot \
  -k /Library/Keychains/System.keychain caddy_root.crt

# Windows (PowerShell)
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

These volumes persist across `docker compose down` and container restarts. **Do not delete them** — deleting `caddy_data` forces certificate reissuance and may hit Let's Encrypt rate limits.

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
# Pull latest images
docker compose -f mcp/deploy/docker-compose.yml pull

# Rebuild aci-mcp image
docker compose -f mcp/deploy/docker-compose.yml build mcp

# Rolling restart (Caddy stays up, zero downtime for TLS)
docker compose -f mcp/deploy/docker-compose.yml up -d --no-deps mcp
```
