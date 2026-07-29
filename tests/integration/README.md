# Integration Tests

Integration tests verify behavior spanning multiple real components, such as an
application service with SQLite, a server with PostgreSQL, or a CLI with a real
RemoteSession. Tests in this directory carry the `integration` marker and own
all temporary processes, ports, files, and services they allocate.

Resource requirements use the orthogonal `requires_network`,
`requires_postgres`, and `requires_uv` markers. Tests must remain deterministic,
bounded, and safe to run in parallel unless they document a narrower
constraint.

Complete user-facing workflows belong in the
[golden journey directory](../e2e/golden/README.md).
