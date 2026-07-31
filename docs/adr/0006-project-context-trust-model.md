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
user-level configuration. Phase 2 does not accept a URL, credential, Token,
remote mode, `RemoteSession`, or network transport from any source. Those
capabilities begin with authenticated remote operation in Phases 5 and 6.

Phase 2 trusted configuration is the bounded regular non-symlink
`profiles.toml` in the operating system's Workaholic user-configuration
directory. `WORKAHOLIC_CONFIG_DIR` may select a different directory only
through an absolute operator- or test-owned path. The exact grammar is:

```toml
version = 1
default_profile = "local"

[profiles.local]
mode = "embedded"
data_directory = "/absolute/path/to/workaholic-data"
```

The top level allows only integer `version = 1`, optional
`default_profile`, and the `profiles` table. Each profile table contains
exactly `mode = "embedded"` and one absolute `data_directory`. Profile names
match `[a-z][a-z0-9_-]{0,31}` and map one-to-one to canonical data directories.
Unsafe files, unknown keys, aliases, unsupported versions or modes, relative
paths, URLs, credentials, and Token fields fail explicitly. If
`profiles.toml` is absent, the built-in `local` profile uses the trusted
absolute `WORKAHOLIC_DATA_DIR` override or platform user-data default.

Profile resolution order is:

1. explicit `--profile`;
2. trusted `WORKAHOLIC_PROFILE`;
3. the discovered `.workaholic.env`;
4. configured `default_profile`;
5. built-in `local`.

Project resolution happens only after a profile has fixed one embedded store
and Instance. It uses:

1. explicit `--project`;
2. the discovered `.workaholic.env`;
3. a structured `CONTEXT_NOT_FOUND` failure when a command requires one
   Project.

An explicit key that does not identify an authorized Project in the selected
Instance returns `PROJECT_NOT_FOUND`. It cannot change the profile or
Instance. Commands that do not require one Project, including
`project create` and `project list`, need only a resolved initialized profile.

Discovery begins at the canonical physical current directory and visits every
physical parent through the filesystem root. Git repository and worktree
boundaries do not stop the walk. The nearest context is authoritative; an
invalid or unreadable nearer context fails instead of falling back to a parent.

The context source must be a bounded regular non-symlink file. Its
`WORKAHOLIC_WORKSPACE_ROOT` is relative to the context file's directory,
resolves to an existing directory, and remains contained by that directory
after lexical and symlink resolution. Resolved profile, Instance, Project, and
Project-key values must be mutually consistent with trusted configuration and
authoritative persistence before any read or mutation.

`workaholic project bind` should add the generated file to
`.git/info/exclude` when appropriate. It must not modify a shared `.gitignore`
unless the user explicitly requests that repository change.

Binding an equivalent profile, Instance, Project, and canonical Workspace is a
successful no-op. A different valid binding returns
`WORKSPACE_BINDING_CONFLICT` unless `--replace` is explicit. Replacement may
atomically replace only an otherwise valid regular non-symlink context that
remains unchanged during validation. It never replaces a malformed file,
directory, symlink, or concurrently changed file.

### Phase delivery boundary

Phase 1 `workaholic up` writes the strict file in the exact current directory.
Every other Phase 1 command inspects only
`<current-working-directory>/.workaholic.env`; it does not search a parent
directory. The only accepted profile value is `local`, which names a built-in
embedded SQLite selection rather than a user-configurable profile. Phase 1
does not read a user profile, accept a remote endpoint, or select
RemoteSession.

Phase 2 implements the full upward resolution order and trusted configurable
embedded profiles described above. It extends the Phase 1 file format rather
than introducing repository context for the first time. It also introduces
disposable SQLite schema version `2`; schema version `1` is rejected unchanged
with no migration or conversion path.

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
- Trusted profile configuration and repository context have separate storage
  and ownership boundaries.
- Nested context is deterministic because the nearest physical file wins and
  an invalid nearer file is a hard failure.
- Phase 2 configuration cannot cause network access or redirect credentials.
- Every configured profile selects one canonical embedded data directory and
  cannot alias another profile.
- Phase 1 callers must invoke commands from the bound directory; upward
  discovery begins in Phase 2.
- Binding tools require explicit safe replacement and prevent accidental
  tracking without silently changing shared repository policy.
- A future context format change requires an explicit version and compatibility
  decision.

## References

- [Threat model](../threat-model.md)
- [Architecture](../architecture.md)
- [Product scope](../product-scope.md)
- [Glossary](../glossary.md)
- [ADR 0007: Human and Agent Identity Model](0007-human-and-agent-identity-model.md)
