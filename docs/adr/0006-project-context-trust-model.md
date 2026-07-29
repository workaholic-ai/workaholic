# ADR 0006: Project Context Trust Model

- Status: Accepted
- Decision date: 2026-07-29
- Deciders: Pavels Gurskis
- Supersedes: None
- Superseded by: None

## Context

Workaholic AI must select the correct Project naturally from multiple
repositories, worktrees, and Agent Workspaces. Repository-local context is
convenient and can travel with a checkout accidentally, which also makes it
attacker-controlled input. If that file could supply credentials, commands, or
an arbitrary remote endpoint, opening a repository could expose a Token or
execute code.

Credentials and server trust belong to a user or runtime security boundary, not
to repository content. Context discovery must also remain deterministic in
nested projects and monorepos.

## Decision

Use a strict, nontracked `.workaholic.env` file to bind a Workspace to a Project.
The accepted v1 keys identify:

- context version;
- trusted profile name;
- Instance identifier;
- Project identifier;
- Project key;
- relative Workspace root.

The parser uses an explicit key allowlist and data parsing only. It never
sources the file, invokes a shell, expands commands, loads executable paths, or
performs command substitution. Unknown keys and unsupported context versions
fail explicitly.

The file must not contain Tokens, database credentials, private keys, secret
references, or arbitrary server URLs. A profile name resolves through trusted
user-level configuration. Ephemeral runtime environment variables may supply a
URL and Token because the process launcher, not the repository, controls that
boundary.

Context resolution order is:

1. explicit command arguments;
2. process environment;
3. nearest `.workaholic.env` while walking upward;
4. trusted user profile defaults;
5. a structured `CONTEXT_NOT_FOUND` failure.

Relative Workspace paths resolve from the context file's directory. The nearest
context file wins. Resolved Instance, Project, and Project-key information must
be mutually consistent with authoritative state.

`workaholic project bind` should add the generated file to
`.git/info/exclude` when appropriate. It must not modify a shared `.gitignore`
unless the user explicitly requests that repository change.

## Alternatives considered

### Store the Token and URL in `.workaholic.env`

This would make a checkout self-contained but would expose credentials to
repository history and allow Token redirection to attacker-controlled hosts.

### Use an executable shell environment file

Sourcing a file would reuse shell syntax but turn context discovery into code
execution and make parsing platform-dependent.

### Require explicit Project flags for every command

This would avoid discovery risk but make ordinary human and Agent workflows
verbose and error-prone across many invocations.

### Store context only in a central user configuration

Central mappings would keep repositories clean but would not travel across
worktrees or Agent Workspaces and would require brittle absolute-path
management.

## Consequences

- Entering a bound Workspace can select its Project without exposing a Token.
- Context parsing requires hostile-input and path-resolution tests.
- Trusted profile and runtime configuration need separate storage and
  permission handling.
- Nested context is deterministic because the nearest file wins.
- Binding tools must prevent accidental tracking without silently changing
  shared repository policy.
- A future context format change requires an explicit version and compatibility
  decision.

## References

- [Threat model](../threat-model.md)
- [Architecture](../architecture.md)
- [Product scope](../product-scope.md)
- [Glossary](../glossary.md)
- [ADR 0007: Human and Agent Identity Model](0007-human-and-agent-identity-model.md)

