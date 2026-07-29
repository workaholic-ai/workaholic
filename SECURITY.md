# Security Policy

## Supported versions

Workaholic AI is pre-alpha foundation software and has no supported production
release. Security fixes are applied only to the current `main` branch during
Phase 0.

| Version | Security fixes |
| --- | --- |
| `main` | Yes |
| Published releases | None |

Do not use version `0.0.0` to protect production workloads or sensitive data.

## Reporting a vulnerability

Report suspected vulnerabilities privately to
[pg@ithesion.com](mailto:pg@ithesion.com). Do not open a public issue, pull
request, or discussion for an unpatched vulnerability.

Include the affected revision, impact, prerequisites, and minimal reproduction
steps. Do not send credentials, private keys, production data, or unnecessary
personal information. If sensitive supporting material is required, use the
initial report to coordinate an appropriate transfer method.

The maintainer will acknowledge receipt, validate the report, assess affected
surfaces, and coordinate remediation and disclosure. This pre-alpha project
does not yet promise a fixed response-time service level.

Do not publicly disclose an unpatched vulnerability or exposed credential.
Revoke exposed credentials immediately through the system that issued them;
reporting them here does not revoke them.

## Current security boundary

Each planned v1 instance serves one organization. The instance administrator
and deployment infrastructure are trusted, while human and agent subjects must
be constrained by project permissions. Cross-organization tenant isolation and
a public multi-tenant hosted service are outside v1.

The current Phase 0 package does not implement authentication, persistence,
project context, agent execution, or network services. See the
[product scope](docs/product-scope.md) for the accepted v1 security boundary
and the [threat model](docs/threat-model.md) for trust assumptions, threats,
and required mitigations.
