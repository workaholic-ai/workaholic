# ADR 0001: Package and Executable Naming

- Status: Accepted
- Decision date: 2026-07-29
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

Workaholic AI needs stable identifiers for its repository, Python distribution,
import package, executable, documentation, and automation examples before the
package skeleton is created. Python distribution names may contain a hyphen,
while Python import names may not. The product must also support `uvx`, which
needs an explicit distribution name when that name differs from its executable.

Using different undocumented names at each boundary would create installation
errors, inconsistent examples, and unnecessary release rework.

## Decision

Use these identifiers:

| Boundary | Identifier |
| --- | --- |
| Product | Workaholic AI |
| GitHub organization | `workaholic-ai` |
| Repository | `workaholic` |
| Repository path | `workaholic-ai/workaholic` |
| Python distribution | `workaholic-ai` |
| Python import package | `workaholic` |
| Console executable | `workaholic` |
| Initial internal version | `0.0.0` |

The installed console entry point must be:

```text
workaholic = workaholic.cli.main:main
```

The module entry point must also support:

```bash
python -m workaholic
```

Source code must import from `workaholic`; it must not introduce a
`workaholic_ai` compatibility package or a second executable alias.

Installation examples must identify the distribution explicitly:

```bash
uvx --from workaholic-ai workaholic --version
uv tool install workaholic-ai
```

The identifiers are accepted for development and packaging. Any change before
public release requires a superseding ADR and coordinated updates to package
metadata, lockfiles, documentation, command tests, repository settings, and
release configuration.

## Alternatives considered

### Use `workaholic` for the distribution and executable

This would produce the shortest installation command, but it assumes the same
name is available and suitable in every registry. Keeping the selected
distribution name explicit also distinguishes the project from unrelated uses
of the generic word.

### Use `workaholic-ai` as the import package

Python cannot import a hyphenated package name. Translating it to
`workaholic_ai` would add an identifier without improving the user-facing
command.

### Use a shortened executable

A short alias would be easier to type but less discoverable and more likely to
conflict with another command. Agents pin and invoke the full executable name,
so a stable descriptive name is preferred.

### Publish separate client and server distributions immediately

Separate distributions could reduce client dependencies later, but doing so
before the server and remote client exist would complicate release engineering.
V1 uses optional dependency groups and explicit import boundaries within one
distribution.

## Consequences

- `uvx` examples must use `--from workaholic-ai` because the distribution and
  executable names differ.
- Package metadata, tests, README examples, and release automation have one
  canonical naming table.
- A future native client may retain the `workaholic` executable without
  preserving Python packaging internals.
- Name or trademark clearance can still require a coordinated rename before
  public release. That cost is explicit and must be handled through a
  superseding ADR rather than silent aliases.

## References

- [Product scope](../product-scope.md)
- [Compatibility policy](../compatibility-policy.md)
- [Architecture](../../ARCHITECTURE.md)
- [Delivery roadmap](../../ROADMAP.md)
