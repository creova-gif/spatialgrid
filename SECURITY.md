# Security Policy

## Supported Versions

| Version | Supported |
| --- | --- |
| `0.2.x` | Yes |
| `0.1.x` | Security fixes only |

## Reporting a Vulnerability

Please **do not** report security vulnerabilities through public GitHub issues.

Instead, report vulnerabilities privately via [GitHub Security Advisories](https://github.com/creova-gif/spatialgrid/security/advisories/new). Include:

- A clear description of the issue and potential impact
- Steps to reproduce or a minimal proof of concept
- Affected version(s) and configuration details

We will acknowledge receipt within 48 hours and work with you on a coordinated fix and disclosure timeline.

## Built-in Security Controls

SpatialGrid enforces defense-in-depth controls across the API gateway, storage layer, and CI pipeline (see [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md)):

- **Strict `org_id` Tenant Isolation:** Every index lookup, coverage check, dispatch solve, and SQLite row (`(org_id, facility_id)` composite primary key) is scoped to `org_id`, raising `TenantIsolationError` on cross-tenant access attempts.
- **Constant-Time Bearer Token Auth:** When `SPATIALGRID_API_KEYS` is configured (or `--require-auth` is enabled on `serve-http`), `Authorization: Bearer <token>` headers are verified using `hmac.compare_digest` and bound to the token's `org_id`.
- **`2 MiB` Payload Limit:** HTTP request bodies are bounded at `2 MiB` (`2,097,152` bytes), rejecting oversized requests with HTTP `413 PayloadTooLarge`.
- **`gitleaks` CI Scanning:** Every push and pull request runs `gitleaks/gitleaks-action@v2` across full git history in [CI](.github/workflows/ci.yml).
- **Zero-Secret Commits:** `.env` files, API keys, and PII/PHI fixtures must never be committed to the repository.
