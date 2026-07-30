# Workaholic AI CLI Automation Contract

- Status: Accepted v1 contract with Phase 1 implementation
- Decision date: 2026-07-29
- Contract family: `workaholic.cli/v1`
- Public surface: Documented JSON output of the `workaholic` executable

## Current implementation notice

This document specifies the accepted v1 automation contract through its Phase 8
freeze. The current `0.1.0a1` development package implements the versioned
envelopes and exposes all six Phase 1 commands through an injected Session
boundary. Its default executable composes the embedded LocalSession and SQLite
adapter, so the durable exact-directory local journey is available. No
compatibility guarantee applies before `1.0.0`.

The alpha does not discover context upward, select multiple active Projects,
run Agents, issue Tokens, use RemoteSession, start a server, or select JSON or
PostgreSQL adapters. Those later command contracts remain normative roadmap
requirements, not current implementation claims.

## Normative language

The terms **must**, **must not**, **should**, and **may** describe requirements
for documented agent-facing commands. Examples illustrate those requirements
but do not add undocumented fields to the contract.

Canonical domain terminology is defined in the [glossary](glossary.md).

## Contract boundary

The supported agent automation interface is the versioned JSON emitted by the
`workaholic` CLI. The same interface applies whether the CLI selects
LocalSession or RemoteSession.

The following are not public automation contracts:

- human-readable tables, colors, spacing, help layout, and progress displays;
- Python modules, classes, functions, or Session signatures;
- server routes and private transport request or response models;
- JSON, SQLite, or PostgreSQL physical storage layouts;
- diagnostic prose when a machine-readable field exists.

The private official-client protocol uses the separate `workaholic/v1`
identifier. It is not a supported substitute for CLI JSON.

## Compatibility timeline

The `workaholic.cli/v1` identifier names the intended v1 family during
development; it does not create an early stability promise.

- Through Phase 7, intentional reviewed breaking changes are allowed.
- At the Phase 8 exit gate, the envelope, documented command schemas, error
  codes, identifier formats, and non-interactive behavior freeze.
- The release candidate validates the frozen contract without silent changes.
- The formal backward-compatibility guarantee begins with `1.0.0`.
- Beginning with `1.0.0`, compatible releases in major version 1 must preserve
  documented v1 behavior.

Pre-release automation must pin its Workaholic AI client version and must not
assume scripts or stored data survive upgrades.

## Invocation modes

### Human-readable mode

Human-readable mode is the default for interactive operators. Its presentation
may evolve without a compatibility event when the documented meaning and JSON
contract remain unchanged.

### JSON mode

A documented agent-facing command selects JSON mode with `--json`. A
non-streaming invocation writes exactly one UTF-8 JSON object followed by one
newline to stdout.

No banner, progress indicator, warning, log, prompt, or explanatory prose may
appear on stdout in JSON mode.

### Non-interactive mode

`--non-interactive` must prevent every confirmation, selection prompt,
credential prompt, pager, and terminal-dependent interaction.

If required input is absent or ambiguous, the command returns a structured
error instead of prompting. Non-interactive behavior must not depend on whether
stdin or stdout is attached to a terminal.

Agent-facing examples should use `--json --non-interactive` together.

## JSON envelope

### Success

A successful command returns:

```json
{
  "schema": "workaholic.cli/v1",
  "ok": true,
  "data": {}
}
```

The fields have these meanings:

| Field | Type | Requirement |
| --- | --- | --- |
| `schema` | string | Required and exactly `workaholic.cli/v1` |
| `ok` | boolean | Required and `true` |
| `data` | any JSON value | Required; command-specific documented result |

A success envelope is closed: it contains exactly `schema`, `ok`, and `data`
and must not contain an `error` or any other top-level field.

### Error

A command-level failure in JSON mode returns:

```json
{
  "schema": "workaholic.cli/v1",
  "ok": false,
  "error": {
    "code": "LEASE_LOST",
    "message": "The attempt for ACME-42 is no longer active.",
    "retryable": false
  }
}
```

The fields have these meanings:

| Field | Type | Requirement |
| --- | --- | --- |
| `schema` | string | Required and exactly `workaholic.cli/v1` |
| `ok` | boolean | Required and `false` |
| `error.code` | string | Required machine-readable identifier |
| `error.message` | string | Required nonempty explanation for a Human |
| `error.retryable` | boolean | Required retry guidance for this failure |

An error envelope is closed: it contains exactly `schema`, `ok`, and `error`
and must not contain `data` or any other top-level field. Consumers use
`error.code`, not `error.message`, for control flow. A `retryable` value of
`true` does not make an unsafe mutation safe to repeat without its required
idempotency key.

Documented error codes use uppercase snake case. Their stability begins at the
Phase 8 freeze and receives the formal compatibility guarantee at `1.0.0`.

### Serialization

- Output is valid UTF-8 JSON without a byte-order mark.
- Objects do not contain duplicate keys.
- Numbers must be valid interoperable JSON numbers; `NaN` and infinities are
  forbidden.
- Identifiers are strings and must not be inferred from display names.
- Timestamps are RFC 3339 strings in UTC with an explicit `Z` offset.
- Consumers must ignore unknown object fields unless a command contract
  explicitly declares an object closed.
- Field omission and JSON `null` are distinct and must follow the
  command-specific schema.

Streaming commands require separately documented framing before implementation.
They must not concatenate partial objects or mix human prose with JSON.

## Standard output and diagnostics

In JSON mode:

- stdout contains only the contract payload;
- stderr contains diagnostics, logs, and operational warnings;
- secret values must be redacted from both streams;
- the presence of stderr does not change envelope meaning;
- a successful command exits zero;
- an error envelope is accompanied by a nonzero exit status.

Phase 1 establishes nonzero exit categories `2`, `3`, `4`, `5`, and `10` as
specified below. Additional commands may add error identifiers before the
Phase 8 freeze, but they must map failures into a documented category.

Failures before the executable can establish JSON mode, such as operating-system
startup failures, are outside the envelope guarantee and may report only on
stderr.

## Mutation idempotency

Every documented mutation that may create or change domain state must accept:

```text
--idempotency-key KEY
```

The key is opaque to Workaholic AI. It is scoped to the authenticated Subject
and logical operation. A committed mutation and its idempotency record are one
transaction.

Repeating the same operation with the same key and semantically identical input
returns the original logical outcome without duplicating state, Results, or
TaskEvents. Reusing the key with different input returns a structured
idempotency-conflict error and performs no mutation.

Idempotency does not replace:

- authentication or authorization;
- optimistic Task versions;
- current Attempt identity and ownership;
- Lease validity;
- normal input and state-transition validation.

Durable Agents should generate unique keys, retain them across ambiguous retry
outcomes, and use bounded retries only when the failure and command semantics
permit.

Phase 2 `project bind` is the explicit exception: it changes only verified
local Workspace context, not domain state, and is naturally idempotent for an
equivalent binding. It does not accept an idempotency key. A different binding
requires explicit `--replace` and still must pass hostile-file validation.

## Structured input

Commands with large or nested payloads must accept a documented file input.
The common form is:

```text
--input-file PATH
```

Command-specific aliases such as `--result-file` may be used when their meaning
is clearer, but they obey the same rules.

- Input files are read as UTF-8.
- JSON payloads are validated before any mutation.
- A path of `-` explicitly selects stdin where the command documents stdin
  support.
- Commands never consume stdin implicitly in non-interactive mode.
- File input and inline fields that supply the same value are rejected as
  ambiguous unless the command documents deterministic precedence.
- Inputs are size-bounded and invalid or oversized payloads return structured
  errors without partial writes.
- Credentials are not accepted in task or Result input payloads.

## Identity, context, and security

- Authentication credentials must not appear in normal positional arguments or
  command examples.
- `.workaholic.env` supplies untrusted Project and Workspace context, never
  credentials or arbitrary endpoints.
- Phase 2 trusted profiles own embedded data-directory selection only and
  reject remote modes, endpoints, credentials, and Tokens.
- Later authenticated remote profiles or runtimes own endpoint and credential
  configuration beginning in Phases 5 and 6.
- Every mutation is attributed to the authenticated Subject and request.
- Agent Capabilities affect scheduling, not authorization.
- LocalSession and RemoteSession must enforce the same ProjectGrant and Attempt
  rules.
- Human-readable messages and JSON diagnostics must redact Tokens, backend
  credentials, and other secrets.

## Command-specific schemas

Each agent-facing command specification must define:

- required and optional arguments;
- whether it reads or mutates state;
- authorization requirement;
- `data` shape on success;
- documented `error.code` values;
- idempotency behavior for mutations;
- file or stdin input behavior;
- ordering and pagination for collections;
- whether unknown enum values must be tolerated;
- exit behavior and retry implications.

A command is not contract-complete until executable tests cover those points in
both supported Session modes where applicable.

## Phase 1 command contract

This section is normative for the Phase 1 LocalSession vertical slice. Every
command below accepts `--json` and `--non-interactive`; these flags have the
global behavior defined above. The Phase 1 commands do not start a daemon,
select RemoteSession, search parent directories for context, load configurable
profiles, or require a Token.

### Shared Phase 1 objects

Command data uses these closed Phase 1 shapes. Later pre-freeze phases may add
fields, and consumers must continue to ignore unknown fields as required by the
envelope contract.

| Object | Required fields |
| --- | --- |
| `instance` | `id` as a nonempty string |
| `project` | `id` and `key` as strings |
| `subject` | `id`, `kind`, `display_name`, `is_instance_admin`, and `project_role` |
| `workspace` | absolute `root` and absolute `context_file` paths |

For Phase 1, `subject.kind` is `human`, `subject.display_name` is
`Local operator`, `subject.is_instance_admin` is `true`, and
`subject.project_role` is `owner`. Project keys match
`[A-Z][A-Z0-9]{1,15}` and are immutable.

A serialized `task` contains exactly these required fields:

| Field | JSON type | Phase 1 rule |
| --- | --- | --- |
| `uid` | string | Canonical globally unique Task identity |
| `project_id` | string | Owning Project identity |
| `number` | integer | Positive, monotonic within the Project |
| `key` | string | Immutable `PROJECT-NUMBER` human identity |
| `title` | string | Trimmed length from 1 through 200 Unicode characters |
| `objective` | string | Trimmed length from 1 through 4,000 Unicode characters |
| `state` | string | `open` |
| `priority` | integer | From 0 through 100 |
| `version` | integer | `1` at creation |
| `created_by` | string | Bootstrap Human Subject identity |
| `created_at` | string | RFC 3339 UTC timestamp |
| `updated_at` | string | Same as `created_at` at creation |

### `workaholic up`

```text
workaholic up --project-key KEY
  [--idempotency-key KEY] [--json] [--non-interactive]
```

`--project-key` is required and validated before persistent state changes. The
command initializes the SQLite schema, one local Instance, one Human Subject,
Instance-administrator status, one Project, and an Owner ProjectGrant. It then
writes a strict `.workaholic.env` to the exact current directory. It creates no
Token or TaskEvent.

Success `data` is:

```json
{
  "instance": {"id": "ins_01..."},
  "project": {"id": "prj_01...", "key": "ACME"},
  "subject": {
    "id": "sub_01...",
    "kind": "human",
    "display_name": "Local operator",
    "is_instance_admin": true,
    "project_role": "owner"
  },
  "workspace": {
    "root": "/work/acme",
    "context_file": "/work/acme/.workaholic.env"
  }
}
```

### `workaholic status`

```text
workaholic status [--json] [--non-interactive]
```

The command is read-only. It reads only
`<current-working-directory>/.workaholic.env`; Phase 1 does not search a parent
directory. Success `data` is:

```json
{
  "mode": "local",
  "schema_version": 1,
  "instance": {"id": "ins_01..."},
  "project": {"id": "prj_01...", "key": "ACME"},
  "subject": {
    "id": "sub_01...",
    "kind": "human",
    "display_name": "Local operator",
    "is_instance_admin": true,
    "project_role": "owner"
  }
}
```

### `workaholic project list`

```text
workaholic project list [--json] [--non-interactive]
```

The command is read-only. Success `data` contains `projects`, an array of
`project` objects ordered by project key ascending:

```json
{"projects": [{"id": "prj_01...", "key": "ACME"}]}
```

### `workaholic task add`

```text
workaholic task add TITLE [--objective TEXT] [--priority INTEGER]
  [--idempotency-key KEY] [--json] [--non-interactive]
```

The title is required. Omitted `--objective` defaults to the normalized title;
omitted `--priority` defaults to `50`. The created Task has state `open` and
version `1`. The Task and its attributable `task_created` TaskEvent commit in
one transaction. Success `data` is:

```json
{"task": {"uid": "tsk_01...", "project_id": "prj_01...", "number": 1, "key": "ACME-1", "title": "First task", "objective": "First task", "state": "open", "priority": 50, "version": 1, "created_by": "sub_01...", "created_at": "2026-07-30T10:00:00Z", "updated_at": "2026-07-30T10:00:00Z"}}
```

### `workaholic task list`

```text
workaholic task list [--cursor CURSOR] [--limit INTEGER]
  [--json] [--non-interactive]
```

The command is read-only. `--limit` defaults to `100`, must be positive, and
must not exceed `500`. Tasks are ordered by task number ascending. The cursor
is opaque and bound to the Project and ordering; callers must not construct or
interpret it. Success `data` is:

```json
{"tasks": [], "next_cursor": null}
```

`next_cursor` is a string only when another page exists, otherwise it is JSON
`null`. Paging through unchanged records neither duplicates nor omits a Task.

### `workaholic task show`

```text
workaholic task show TASK [--json] [--non-interactive]
```

`TASK` accepts a canonical Task UID or a human key such as `ACME-1`. The
command is read-only. Success `data` is:

```json
{"task": {"uid": "tsk_01...", "project_id": "prj_01...", "number": 1, "key": "ACME-1", "title": "First task", "objective": "First task", "state": "open", "priority": 50, "version": 1, "created_by": "sub_01...", "created_at": "2026-07-30T10:00:00Z", "updated_at": "2026-07-30T10:00:00Z"}}
```

### Phase 1 idempotency

`up` and `task add` accept an optional `--idempotency-key`. The key and a
canonical fingerprint of the normalized semantic input are stored atomically
with the successful outcome. Repeating the same operation with the same key
and input returns that outcome. Reusing the key with different input returns
`IDEMPOTENCY_CONFLICT` and changes neither state nor events.

`up` is also naturally idempotent without a caller key: repeating it for the
existing Phase 1 Project returns the existing bootstrap entities, and a retry
after context-file failure safely completes the binding. A different Project
key returns `PROJECT_KEY_CONFLICT`. By contrast, omitting the key from
`task add` does not make an ambiguous failed Task creation safe to retry.

### Phase 1 errors and exits

The error message is for Humans and is not an automation discriminator. Phase
1 code and exit mappings are:

| Error code | Exit | Retryable | Meaning |
| --- | ---: | :---: | --- |
| `INVALID_INPUT` | 2 | false | Invalid argument, option, value, or cursor |
| `CONTEXT_NOT_FOUND` | 3 | false | No exact-directory context file |
| `CONTEXT_INVALID` | 3 | false | Context file is malformed or untrusted |
| `NOT_INITIALIZED` | 3 | false | Referenced local Instance is not initialized |
| `TASK_NOT_FOUND` | 3 | false | Task UID or key does not resolve |
| `PROJECT_KEY_CONFLICT` | 4 | false | Project key belongs to another Project |
| `IDEMPOTENCY_CONFLICT` | 4 | false | Key was reused with different input |
| `PERMISSION_DENIED` | 5 | false | Active Subject lacks the required grant |
| `SCHEMA_UNSUPPORTED` | 10 | false | Store schema is missing or unsupported |
| `STORAGE_BUSY` | 10 | true | Bounded SQLite lock acquisition was exhausted |
| `STORAGE_UNAVAILABLE` | 10 | false | Local storage cannot be opened or made durable |
| `INTERNAL_ERROR` | 10 | false | Redacted unexpected operational failure |

The command-to-error surface is:

| Command | Documented errors in addition to storage, schema, permission, and internal errors |
| --- | --- |
| `up` | `INVALID_INPUT`, `CONTEXT_INVALID`, `PROJECT_KEY_CONFLICT`, `IDEMPOTENCY_CONFLICT` |
| `status` | `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED` |
| `project list` | `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED` |
| `task add` | `INVALID_INPUT`, `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED`, `IDEMPOTENCY_CONFLICT` |
| `task list` | `INVALID_INPUT`, `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED` |
| `task show` | `INVALID_INPUT`, `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED`, `TASK_NOT_FOUND` |

Command parser usage failures also use exit `2`. Once JSON mode is established,
the CLI maps them to an `INVALID_INPUT` envelope. A failure before Python can
establish JSON mode remains outside the envelope guarantee.

## Phase 2 command contract

This section is the normative contract for the Multi-project Alpha. Until the
Phase 2 acceptance gate passes, it describes the next implementation rather
than a capability of the current `0.1.0a1` package or README.

Phase 2 remains embedded-only. It does not accept a remote profile, URL,
credential, Token, login, `RemoteSession`, network transport, JSON adapter, or
PostgreSQL adapter. Every Phase 2 command accepts `--json` and
`--non-interactive` with the global behavior above.

Phase 2 uses disposable SQLite schema version `2`. It rejects schema version
`1` unchanged with `SCHEMA_UNSUPPORTED` and provides no migration, conversion,
import, export, or automatic reset.

### Trusted profile grammar and selection

The trusted file is `profiles.toml` in the operating system's Workaholic
user-configuration directory. `WORKAHOLIC_CONFIG_DIR` may select another
absolute trusted configuration directory. The exact version `1` grammar is:

```toml
version = 1
default_profile = "local"

[profiles.local]
mode = "embedded"
data_directory = "/absolute/path/to/workaholic-data"
```

The top level is closed and allows only:

| Field | Required | Phase 2 rule |
| --- | :---: | --- |
| `version` | Yes | Integer exactly `1`; booleans are not integers |
| `default_profile` | No | Valid configured profile name |
| `profiles` | Yes | Tables keyed by unique profile name |

Every `[profiles.NAME]` table is closed and contains exactly:

| Field | Required value |
| --- | --- |
| `mode` | String exactly `embedded` |
| `data_directory` | Absolute path to the profile's trusted data directory |

Profile names match `[a-z][a-z0-9_-]{0,31}`. Data directories are canonical
one-to-one selections: two names cannot alias the same canonical directory.
Unknown keys, duplicate semantic values, invalid UTF-8, oversized or unsafe
files, symlinks, directories, missing defaults, relative paths, URL fields,
credential fields, Token fields, and other malformed input return
`PROFILE_INVALID`. A version other than `1` or a mode other than `embedded`
returns `PROFILE_UNSUPPORTED`.

If the file is absent, the built-in `local` profile uses the absolute trusted
`WORKAHOLIC_DATA_DIR` override or the platform user-data default. Profile
precedence is:

1. explicit `--profile`;
2. trusted `WORKAHOLIC_PROFILE`;
3. the discovered Workspace context;
4. configured `default_profile`;
5. built-in `local`.

The selected profile fixes one embedded data store and Instance before Project
selection. Project precedence is explicit `--project`, then discovered context.
An explicit Project must be authorized and belong to that same Instance.
Neither an explicit Project nor repository context may change the selected
storage or Instance.

`project create` and `project list` require a resolved initialized profile but
not a Workspace context. Other Project-scoped commands return
`CONTEXT_NOT_FOUND` when neither explicit Project nor discovered context exists.
An explicit key that does not resolve to an authorized Project in the selected
Instance returns `PROJECT_NOT_FOUND`.

### Workspace discovery and trust

Workspace discovery starts from the canonical physical current directory and
visits every physical parent through the filesystem root. Git repository and
worktree boundaries do not stop the walk. The nearest `.workaholic.env` is
authoritative; a malformed, unreadable, unsafe, or unsupported nearer file
returns `CONTEXT_INVALID` instead of falling back to a parent.

The context source must be a bounded regular non-symlink file. Its
`WORKAHOLIC_WORKSPACE_ROOT` is relative to the context file's directory,
resolves to an existing directory, and remains contained by that directory
after lexical and symlink resolution. The strict parser accepts only the
documented identity and relative-root keys and never invokes a shell or
performs substitution. It rejects endpoints, credentials, Tokens, executable
paths, storage paths, and profile definitions.

Context-supplied profile, Instance, Project, and Project-key values must match
trusted configuration and authoritative persistence before any read or
mutation. Binding never replaces a malformed file, symlink, directory, or
concurrently changed file and never changes shared `.gitignore`.

### Shared Phase 2 objects

Serialized Phase 2 Projects are closed and contain:

```json
{"id": "prj_01...", "key": "ACME", "name": "Acme"}
```

`id` remains the canonical opaque identity. `key` is immutable and matches
`[A-Z][A-Z0-9]{1,15}`. `name` contains 1 through 200 Unicode characters after
trimming. Existing Task and Subject objects retain their Phase 1 fields.

The closed effective-context object contains every field below:

```json
{
  "mode": "embedded",
  "profile": "local",
  "schema_version": 2,
  "instance": {"id": "ins_01..."},
  "project": {"id": "prj_01...", "key": "ACME", "name": "Acme"},
  "workspace_root": "/work/acme",
  "subject": {
    "id": "sub_01...",
    "kind": "human",
    "display_name": "Local operator",
    "is_instance_admin": true,
    "project_role": "owner"
  },
  "context_source": "/work/acme/.workaholic.env"
}
```

`mode` is exactly `embedded` and `schema_version` is exactly `2`.
`workspace_root` and `context_source` are absolute paths when discovered or
bound context supplies them. Both are JSON `null` when an explicit Project
succeeds without Workspace context. No result contains the profile-file path,
profile contents, data directory, database path, URL, or credential.

### `workaholic up`

```text
workaholic up --project-key KEY [--project-name NAME] [--profile PROFILE]
  [--idempotency-key KEY] [--json] [--non-interactive]
```

`up` initializes only an empty selected profile with SQLite schema version `2`,
the bootstrap local Human, and the first Project. Omitting `--project-name`
uses the normalized Project key as its name. Additional Projects use
`project create`.

Success retains the Phase 1 `instance`, `project`, `subject`, and `workspace`
fields; the Project adds required `name`. Repeating an equivalent bootstrap is
a successful no-op. A different initial key or name in an initialized profile
returns `PROJECT_KEY_CONFLICT`. Context is written only to the exact current
directory after durable bootstrap succeeds.

### `workaholic status`

```text
workaholic status [--profile PROFILE] [--project KEY]
  [--json] [--non-interactive]
```

The command is read-only. It resolves and validates the effective embedded
profile, Instance, Subject, and Project. Success `data` is:

```json
{
  "mode": "embedded",
  "profile": "local",
  "schema_version": 2,
  "instance": {"id": "ins_01..."},
  "project": {"id": "prj_01...", "key": "ACME", "name": "Acme"},
  "subject": {
    "id": "sub_01...",
    "kind": "human",
    "display_name": "Local operator",
    "is_instance_admin": true,
    "project_role": "owner"
  }
}
```

### `workaholic context`

```text
workaholic context [--profile PROFILE] [--project KEY]
  [--json] [--non-interactive]
```

The command is read-only and returns the exact effective-context object defined
above. It uses the same selection and authority checks as a mutation and never
returns raw profile configuration or storage paths.

### `workaholic project create`

```text
workaholic project create --key KEY --name NAME [--profile PROFILE]
  [--idempotency-key KEY] [--json] [--non-interactive]
```

The command creates one named Project in the selected initialized Instance and
automatically grants the bootstrap local Human the Owner role. It does not
create a TaskEvent. Success `data` is:

```json
{
  "project": {"id": "prj_02...", "key": "DOCS", "name": "Documentation"},
  "grant": {
    "subject_id": "sub_01...",
    "project_id": "prj_02...",
    "role": "owner"
  }
}
```

Creation is idempotent only when `--idempotency-key` is supplied. Equivalent
replay returns the original Project and grant. Different input returns
`IDEMPOTENCY_CONFLICT`. Any existing or reserved key returns
`PROJECT_KEY_CONFLICT` without consuming the key or creating partial state.

### `workaholic project bind`

```text
workaholic project bind KEY [PATH] [--profile PROFILE] [--replace]
  [--json] [--non-interactive]
```

`PATH` defaults to the current directory and must resolve physically to an
existing directory. The command verifies the selected profile, Instance,
Subject, and authorized Project before writing a strict `.workaholic.env`.
Equivalent binding is a successful no-op and returns the effective-context
object.

A different valid binding returns `WORKSPACE_BINDING_CONFLICT`.
`--replace` may replace only a valid regular non-symlink context file that
remains unchanged between validation and atomic replacement. The command never
replaces a malformed file, symlink, directory, or concurrently changed file.
It updates a conventional `.git/info/exclude` only after the context is durable
and never changes shared `.gitignore`.

### `workaholic project list`

```text
workaholic project list [--profile PROFILE]
  [--json] [--non-interactive]
```

The command needs no Project or Workspace context. It returns every Project
authorized for the bootstrap local Human in the selected Instance, ordered by
immutable Project key ascending:

```json
{
  "projects": [
    {"id": "prj_01...", "key": "ACME", "name": "Acme"},
    {"id": "prj_02...", "key": "DOCS", "name": "Documentation"}
  ]
}
```

### Phase 2 Task selection

```text
workaholic task add TITLE [--project KEY]
  [--objective TEXT] [--priority INTEGER] [--idempotency-key KEY]
  [--json] [--non-interactive]
workaholic task list [--project KEY | --all-projects]
  [--cursor CURSOR] [--limit INTEGER] [--json] [--non-interactive]
workaholic task show TASK [--project KEY]
  [--json] [--non-interactive]
```

Existing Phase 1 Task validation, success objects, attribution, and
idempotency remain unchanged. Normal use selects the nearest valid Workspace
context. `--project` explicitly selects another authorized Project only in the
same resolved Instance and also permits use without Workspace context.

Only `task list` accepts `--all-projects`. It is mutually exclusive with
`--project` at the validated request boundary. One-Project lists order by Task
number ascending. All-Project lists include only authorized Projects and order
by `(project key, task number)` ascending. Task human keys remain
Project-prefixed, so JSON and human output are unambiguous.

### Phase 2 cursor contract

Phase 2 Task cursors begin with the exact prefix `v2.` followed by unpadded
URL-safe base64 of one canonical JSON object. Consumers must treat the complete
value as opaque and must not construct or interpret it.

The closed canonical payload binds:

- integer version `2`;
- profile name;
- Instance identity;
- Subject identity;
- selection kind `project` or `all_projects`;
- selected Project identity for `project`, otherwise JSON `null`;
- the last Task number for `project`, or last `(project key, task number)` for
  `all_projects`.

Malformed, padded, noncanonical, unsupported-version, or cross-profile,
cross-Instance, cross-Subject, cross-Project, or cross-selection reuse returns
`INVALID_INPUT` and performs no read-side mutation.

### Phase 2 errors and exits

The five Phase 2 errors have fixed safe messages. Messages never contain
profile contents, data paths, URLs, credentials, SQL, or raw exceptions.

| Error code | Exit | Retryable | Exact message |
| --- | ---: | :---: | --- |
| `PROFILE_NOT_FOUND` | 3 | false | `The selected profile was not found.` |
| `PROFILE_INVALID` | 3 | false | `The trusted profile configuration is invalid.` |
| `PROFILE_UNSUPPORTED` | 3 | false | `The selected profile mode or configuration version is not supported.` |
| `PROJECT_NOT_FOUND` | 3 | false | `The selected Project was not found.` |
| `WORKSPACE_BINDING_CONFLICT` | 4 | false | `The Workspace is already bound to a different Project, Instance, or profile.` |

Existing Phase 1 errors retain their exits. In Phase 2,
`CONTEXT_NOT_FOUND` means that a Project-scoped command received neither
explicit Project nor discovered context. `CONTEXT_INVALID` includes an unsafe
context file, invalid Workspace root, unsupported context version, or mismatch
between context identity and authoritative profile or persistence state.
`NOT_INITIALIZED` means the selected profile has no initialized Instance.
`SCHEMA_UNSUPPORTED` includes every schema version other than exact version
`2`, including Phase 1 version `1`.

The command-specific additions are:

| Command | Documented Phase 2 errors in addition to existing input, permission, storage, schema, and internal errors |
| --- | --- |
| `up` | `PROFILE_NOT_FOUND`, `PROFILE_INVALID`, `PROFILE_UNSUPPORTED`, `CONTEXT_INVALID`, `PROJECT_KEY_CONFLICT`, `IDEMPOTENCY_CONFLICT` |
| `status` | `PROFILE_NOT_FOUND`, `PROFILE_INVALID`, `PROFILE_UNSUPPORTED`, `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED`, `PROJECT_NOT_FOUND` |
| `context` | `PROFILE_NOT_FOUND`, `PROFILE_INVALID`, `PROFILE_UNSUPPORTED`, `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED`, `PROJECT_NOT_FOUND` |
| `project create` | `PROFILE_NOT_FOUND`, `PROFILE_INVALID`, `PROFILE_UNSUPPORTED`, `NOT_INITIALIZED`, `PROJECT_KEY_CONFLICT`, `IDEMPOTENCY_CONFLICT` |
| `project bind` | `PROFILE_NOT_FOUND`, `PROFILE_INVALID`, `PROFILE_UNSUPPORTED`, `CONTEXT_INVALID`, `NOT_INITIALIZED`, `PROJECT_NOT_FOUND`, `WORKSPACE_BINDING_CONFLICT` |
| `project list` | `PROFILE_NOT_FOUND`, `PROFILE_INVALID`, `PROFILE_UNSUPPORTED`, `NOT_INITIALIZED` |
| `task add` | `PROFILE_NOT_FOUND`, `PROFILE_INVALID`, `PROFILE_UNSUPPORTED`, `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED`, `PROJECT_NOT_FOUND`, `IDEMPOTENCY_CONFLICT` |
| `task list` | `PROFILE_NOT_FOUND`, `PROFILE_INVALID`, `PROFILE_UNSUPPORTED`, `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED`, `PROJECT_NOT_FOUND` |
| `task show` | `PROFILE_NOT_FOUND`, `PROFILE_INVALID`, `PROFILE_UNSUPPORTED`, `CONTEXT_NOT_FOUND`, `CONTEXT_INVALID`, `NOT_INITIALIZED`, `PROJECT_NOT_FOUND`, `TASK_NOT_FOUND` |

## Conformance requirements

Contract tests must verify at least:

- exact success and error envelope invariants;
- JSON-only stdout and diagnostics-only stderr;
- valid UTF-8 serialization and one trailing newline;
- no prompt in non-interactive mode;
- missing, invalid, and oversized structured input;
- identical logical outcomes through LocalSession and RemoteSession;
- idempotent retry and conflicting key reuse;
- optimistic-version and stale-Attempt failures;
- authentication and cross-Project authorization failures;
- Token and credential redaction;
- tolerance of documented optional unknown fields;
- pinned pre-release client behavior and frozen v1 fixtures.

## Related decisions and documents

- [ADR 0003: CLI JSON as the Public Automation Contract](adr/0003-cli-json-automation-contract.md)
- [ADR 0002: Local and Remote Sessions](adr/0002-local-and-remote-sessions.md)
- [ADR 0004: Private Versioned Client/Server Protocol](adr/0004-private-versioned-client-server-protocol.md)
- [Compatibility policy](compatibility-policy.md)
- [Threat model](threat-model.md)
- [Architecture](architecture.md)
