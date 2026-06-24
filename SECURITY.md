# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 1.x | ✅ |
| < 1.0 | ✗ |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report vulnerabilities by email to **monark.aiops@pm.me** with the subject
line `[aci-mcp] Security Vulnerability`.

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (optional)

You will receive an acknowledgement within **48 hours** and a resolution
timeline within **7 days**. We will notify you when the fix is released.

## Scope

In scope:
- Authentication bypass
- Token leakage or exposure
- APIC credential exposure
- Prompt injection via MCP tool responses
- Dependency vulnerabilities with a known CVE

Out of scope:
- Vulnerabilities in the APIC itself
- Issues requiring physical access to the deployment host
- Denial of service against the Cisco sandbox (public shared resource)
