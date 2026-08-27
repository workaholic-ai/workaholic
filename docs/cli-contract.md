# Workaholic AI CLI Automation Contract

- Status: Accepted v1 contract through Phase 4 with Phase 4 implementation
- Decision date: 2026-07-29
- Contract family: `workaholic.cli/v1`
- Public surface: Documented JSON output of the `workaholic` executable

## Current implementation notice

This document specifies the accepted v1 automation contract through its Phase 8
freeze. The current `0.4.0a1` development package implements the versioned
envelopes and all 24 Phase 4 operations through an injected Session boundary.
Its default executable composes the embedded `LocalSession`, trusted local
profiles, canonical upward Workspace discovery, and SQLite schema version `4`.
It includes existing-Task mutations, dependencies, readiness views, Human
Results and review, exclusive Human and Agent Claims, bounded Leases, Agent
Attempts, heartbeat, progress, release and submission, and TaskEvent history.
No compatibility guarantee applies before `1.0.0`.

The alpha reuses one bootstrap Subject for Human and Agent command paths. It
does not issue Tokens, distinguish Agent identities, use remote profiles or
credentials, use `RemoteSession`, start a server, schedule by capability,
archive Projects, force-interrupt execution, migrate schemas, or select JSON or
PostgreSQL adapters. Human Results always carry a null Attempt identity; a
non-null Attempt identifies local Agent execution. Proposed follow-ups never
create Tasks automatically.

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

## Optimistic existing-Task mutations

Every documented mutation of an existing Task accepts:

```text
--expected-version INTEGER
```

The positive integer is required by the Session, application, and persistence
operation. The `--expected-version` option is therefore mandatory for
automation. JSON mode, `--non-interactive`, and an invocation whose stdin is not
a terminal require the explicit CLI option. Omission returns `INVALID_INPUT`
before any mutation or structured-input side effect.

In default Human-readable mode with terminal stdin, omission enables one
convenience read. The CLI displays the selected Task key, current stored state,
current version, and intended semantic action, then asks for confirmation once.
Acceptance submits that exact version. Declining exits zero after a
Human-readable `No changes made.` message and performs no mutation. Supplying
the option skips the convenience read and prompt.

A `VERSION_CONFLICT` is rendered as returned. The CLI, LocalSession, and future
RemoteSession must not fetch a new version and silently retry. One successful
semantic mutation increments the Task version once, including submission or
approval operations that append two TaskEvents.

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

Phase 3 JSON documents are at most 1,048,576 bytes before decoding, at most 16
containers deep, and contain at most 128 members in an object or 500 items in a
generic array. Field-specific limits below may be narrower. A UTF-8 byte-order
mark, duplicate object key, non-finite number, trailing data, unknown field, or
top-level value other than an object is invalid. Reading a file never follows a
directory and never executes, expands, fetches, or resolves a value from its
contents.

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
- Capability-based scheduling is outside v1 and, if later introduced, must not
  affect authorization.
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

This section is the implemented normative contract for the `0.2.0a1`
Multi-project Alpha and its public README.

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

## Phase 3 command contract

Phase 3 adds Human-operated Task lifecycle and audit behavior to the embedded
Session. This section is the implemented normative contract for `0.3.0a1`.
Every command retains `--json`, `--non-interactive`, and optional
`--project KEY` where one selected Project is required.

### Shared Phase 3 Task object

Phase 3 Task serialization retains every Phase 2 field and adds the complete
definition and derived view:

```json
{
  "uid": "tsk_01...",
  "project_id": "prj_01...",
  "number": 42,
  "key": "ACME-42",
  "title": "Analyze cancellation reasons",
  "objective": "Identify the three most actionable causes.",
  "state": "open",
  "priority": 70,
  "available_at": null,
  "approval": "human",
  "acceptance": [
    {
      "id": "ac_categories",
      "text": "At least 80% of records are categorized.",
      "required": true
    }
  ],
  "context": [
    {
      "uri": "workspace://repo/data/cancellations.csv",
      "version": "git:8f31c12"
    }
  ],
  "depends_on": ["tsk_00..."],
  "blocking_reason": null,
  "current_result_id": null,
  "version": 4,
  "created_by": "sub_01...",
  "created_at": "2026-07-29T09:00:00Z",
  "updated_at": "2026-07-29T09:20:00Z",
  "views": {
    "ready": true,
    "running": false,
    "scheduled": false,
    "stale": false,
    "awaiting_review": false
  },
  "readiness_reasons": []
}
```

All fields are required; nullable fields are explicit JSON `null`. Acceptance,
context, dependencies, and readiness reasons are arrays even when empty.
Dependencies order by stable Task key. Readiness reasons are stable enum
strings and order by `state`, `availability`, then prerequisite Task key.

Acceptance contains at most 100 entries. An ID matches
`ac_[A-Za-z0-9][A-Za-z0-9_-]{0,63}`, is unique within the Task, and never
changes meaning through reordering. Text contains 1 through 1,000 printable
Unicode characters after trimming. `required` is a real boolean.

Context contains at most 100 entries. `uri` contains 1 through 2,048 trimmed
printable characters and is treated as an inert reference. `version` is JSON
`null` or 1 through 256 trimmed printable characters. Duplicate `(uri,
version)` pairs are invalid. Workaholic does not open, fetch, or execute a
context URI.

Approval is exactly `none` or `human`. Phase 3 running and stale views are
always false because Attempts do not yet exist.

### Phase 3 Task definition input

`task add --input-file` accepts this closed object, with every field optional:

```json
{
  "objective": "Identify actionable causes.",
  "priority": 70,
  "available_at": "2026-08-02T09:00:00Z",
  "approval": "human",
  "acceptance": [],
  "context": []
}
```

`task update --input-file` accepts the same object plus optional `title`.
At least one update field is required. JSON `null` is accepted only for
`available_at`, where it clears scheduling. Acceptance and context replace the
complete ordered set; an empty array intentionally clears it.

Inline and file values may be combined only when they name disjoint fields.
Supplying the same field from both sources returns `INVALID_INPUT`. Unknown
fields and identity, state, dependency, blocking, Result, version, actor,
request, event, Attempt, timestamp, or cursor fields are invalid.

### `workaholic task add` in Phase 3

```text
workaholic task add TITLE [--objective TEXT] [--priority INTEGER]
  [--available-at TIMESTAMP] [--approval none|human]
  [--input-file PATH|-] [--project KEY] [--idempotency-key KEY]
  [--json] [--non-interactive]
```

Creation remains version `1`, state `open`, and has no dependencies, blocking
reason, or current Result. Omitted availability is null; approval defaults to
`none`; acceptance and context default to empty arrays. Success remains:

```json
{"task": {}}
```

where `task` is the complete shared Phase 3 Task object. The title remains a
required positional argument and cannot appear in the add input file.

### `workaholic task update`

```text
workaholic task update TASK
  [--title TEXT] [--objective TEXT] [--priority INTEGER]
  [--available-at TIMESTAMP | --clear-available-at]
  [--approval none|human] [--input-file PATH|-]
  [--expected-version INTEGER] [--idempotency-key KEY]
  [--project KEY] [--json] [--non-interactive]
```

Update is allowed only in `open` or `blocked`. It changes only the documented
definition fields and appends one `task_updated` event. It never accepts or
changes stored state, dependency edges, blocking reason, Result, identity,
version directly, or attribution. A no-op or empty update returns
`INVALID_INPUT`. Success data is:

```json
{
  "task": {},
  "events": [{}]
}
```

The Task and TaskEvent use their complete shared objects.

### Stored-state commands

```text
workaholic task block TASK --reason TEXT
  [--expected-version INTEGER] [--idempotency-key KEY]
  [--project KEY] [--json] [--non-interactive]
workaholic task unblock TASK
  [--expected-version INTEGER] [--idempotency-key KEY]
  [--project KEY] [--json] [--non-interactive]
workaholic task cancel TASK [--reason TEXT]
  [--expected-version INTEGER] [--idempotency-key KEY]
  [--project KEY] [--json] [--non-interactive]
```

Block permits `open -> blocked` and requires 1 through 1,000 printable Unicode
reason characters after trimming. Unblock permits `blocked -> open` and clears
the reason. Cancel permits `open|blocked|review -> cancelled`; its reason is
null or 1 through 1,000 characters. `done` and `cancelled` are terminal.

Each command increments the Task version once, appends exactly its corresponding
`task_blocked`, `task_unblocked`, or `task_cancelled` event, and returns the
same `task` plus `events` object used by update.

### Dependencies and readiness views

```text
workaholic task add-dependency TASK PREREQUISITE
  [--expected-version INTEGER] [--idempotency-key KEY]
  [--project KEY] [--json] [--non-interactive]
workaholic task remove-dependency TASK PREREQUISITE
  [--expected-version INTEGER] [--idempotency-key KEY]
  [--project KEY] [--json] [--non-interactive]
workaholic task list [--project KEY | --all-projects]
  [--view all|ready|scheduled|blocked|review|done|cancelled]
  [--cursor CURSOR] [--limit INTEGER] [--json] [--non-interactive]
```

Dependency mutations are allowed only for a dependant Task in `open` or
`blocked`. Both Tasks must exist in the same-Project scope. Self edges, duplicate
additions, absent removals, and cycles are conflicts. Success versions only the
dependant, appends one `task_updated`, and returns `task` plus `events`.

A prerequisite is satisfied only by `done`. A cancelled prerequisite leaves
the dependant unchanged: a prerequisite state transition does not mutate
dependant Tasks. It adds `UNSATISFIABLE_DEPENDENCY` to readiness reasons until
the graph changes. Human submission requires all prerequisites done; future
availability does not prohibit deliberate Human submission.

View defaults to `all`, which retains Phase 2 ordering. `ready` orders priority
descending, availability ascending with null first, then Task number; an
all-Project page inserts Project key before Task number as the final tie-breaker.
Other views order by Task number, or `(Project key, Task number)` across
Projects. `review` corresponds to the derived `awaiting_review` view.

Phase 3 Task cursors begin with `v3.` and bind the Phase 2 profile, Instance,
Subject, Project/selection scope plus view name and exact view ordering
position. Cross-view reuse returns `INVALID_INPUT` without mutation.

### Shared Phase 3 Result object

A Phase 3 Result is:

```json
{
  "id": "res_01...",
  "task_uid": "tsk_01...",
  "submitted_by": "sub_01...",
  "attempt_id": null,
  "submitted_at": "2026-08-01T12:00:00Z",
  "comment": "Implemented manually.",
  "summary": "The three causes are categorized.",
  "criteria": [
    {
      "criterion_id": "ac_categories",
      "status": "passed",
      "evidence": "86.5% categorized"
    }
  ],
  "artifacts": [
    {
      "uri": "workspace://repo/report.md",
      "media_type": "text/markdown",
      "sha256": null
    }
  ],
  "proposed_follow_ups": [
    {"title": "Validate against support tickets"}
  ],
  "review": {
    "status": "pending",
    "reviewed_by": null,
    "reviewed_at": null,
    "comment": null,
    "reason": null
  }
}
```

Every field is required and nullable fields use JSON `null`. Human Phase 3
Results always have null `attempt_id`. `comment`, `summary`, criterion evidence,
review comment, and review reason are null or bounded printable strings.
Criterion status is `passed`, `failed`, or `not_applicable` and IDs must match
the Task's acceptance set. Arrays contain at most 100 entries each.

Artifact URI follows the context URI contract. Media type is null or a valid
lowercase type/subtype token of at most 127 ASCII characters. SHA-256 is null
or exactly 64 lowercase hexadecimal characters. Artifact contents are never
read or stored. Proposed follow-up titles follow Task-title validation but do
not create Tasks, dependencies, or hierarchy.

Review status is `not_required`, `pending`, `approved`, or `rejected`.
Reviewer identity and timestamp are non-null only after approval or rejection;
comment is for approval and reason is for rejection.

### Submission and review commands

```text
workaholic task submit TASK [--comment TEXT] [--result-file PATH|-]
  [--expected-version INTEGER] [--idempotency-key KEY]
  [--project KEY] [--json] [--non-interactive]
workaholic task approve TASK [--comment TEXT]
  [--expected-version INTEGER] [--idempotency-key KEY]
  [--project KEY] [--json] [--non-interactive]
workaholic task reject TASK --reason TEXT
  [--expected-version INTEGER] [--idempotency-key KEY]
  [--project KEY] [--json] [--non-interactive]
```

Submit is a Human operation in Phase 3 and does not accept `--attempt`.
Comment and Result file are independently optional; submitting neither is a
valid manual completion. The Result file is a closed object containing optional
`summary`, `criteria`, `artifacts`, and `proposed_follow_ups` fields. It cannot
supply the CLI comment or any Result, Task, actor, Attempt, review, request,
event, timestamp, or cursor identity.

Submit requires `open` with satisfied dependencies. Approval `none` changes
the Task to `done`, sets review status `not_required`, and appends
`result_submitted` then `task_completed`. Approval `human` changes it to
`review`, sets status `pending`, and appends `result_submitted`.

Approve requires `review`, changes it to `done`, and appends
`review_approved` then `task_completed`. Reject requires `review`, retains the
Result with status `rejected`, clears the Task's current Result selection,
changes it to `open`, and appends `review_rejected`. Each command increments the
Task version once regardless of event count.

Success data is:

```json
{
  "task": {},
  "result": {},
  "events": [{}]
}
```

### Shared Phase 3 TaskEvent object

```json
{
  "id": "evt_01...",
  "cursor": 17,
  "task_uid": "tsk_01...",
  "project_id": "prj_01...",
  "actor_subject_id": "sub_01...",
  "actor_kind": "human",
  "attempt_id": null,
  "request_id": "req_01...",
  "type": "task_updated",
  "occurred_at": "2026-08-01T12:00:00Z",
  "payload": {}
}
```

All fields are required. Phase 3 actor kind is `human` and Attempt is null.
The event type is one of `task_created`, `task_updated`, `task_blocked`,
`task_unblocked`, `result_submitted`, `review_approved`, `review_rejected`,
`task_completed`, or `task_cancelled`. Payload is a bounded closed object for
that event type and never contains credentials or artifact contents.

### `workaholic task events`

```text
workaholic task events TASK [--after INTEGER] [--limit INTEGER] [--follow]
  [--project KEY] [--json] [--non-interactive]
```

`after` is an optional nonnegative Instance cursor and is exclusive. It
defaults to `0`. Limit defaults to `100` and ranges from 1 through 500. A
snapshot success is:

```json
{
  "events": [],
  "next_cursor": 0
}
```

Events order by cursor ascending. `next_cursor` is the greatest returned cursor
or the supplied `after` value for an empty page. Clients poll by passing it as
the next `after` value.

`--follow` is a Human-readable polling convenience. It emits each new event
once until interrupted and resumes from the greatest cursor. It cannot be
combined with `--json` or `--non-interactive`; automation uses bounded snapshot
polling. Normal interruption exits zero and does not mutate state.

### Phase 3 errors and exits

Phase 3 adds these exact safe errors. They expose no current value, input
payload, URI, path, SQL, or raw exception.

| Error code | Exit | Retryable | Exact message |
| --- | ---: | :---: | --- |
| `VERSION_CONFLICT` | 4 | false | `The Task changed after the expected version.` |
| `INVALID_TRANSITION` | 4 | false | `The Task cannot perform the requested lifecycle transition.` |
| `DEPENDENCY_CONFLICT` | 4 | false | `The dependency change conflicts with the current Task graph.` |
| `DEPENDENCY_CYCLE` | 4 | false | `The dependency change would create a cycle.` |
| `UNSATISFIABLE_DEPENDENCY` | 4 | false | `The Task has a cancelled prerequisite and cannot be completed.` |
| `RESULT_INVALID` | 2 | false | `The submitted Result is invalid.` |

`INVALID_INPUT` covers missing explicit expected version in JSON,
non-interactive, or non-terminal operation; malformed structured input;
ambiguous file/inline fields; empty update; invalid bounds; unsupported
`--follow` combinations; and raw state input.

The command-specific additions are:

| Command | Additional Phase 3 errors |
| --- | --- |
| `task update` | `VERSION_CONFLICT`, `INVALID_TRANSITION`, `IDEMPOTENCY_CONFLICT` |
| `task block`, `task unblock`, `task cancel` | `VERSION_CONFLICT`, `INVALID_TRANSITION`, `IDEMPOTENCY_CONFLICT` |
| `task add-dependency`, `task remove-dependency` | `VERSION_CONFLICT`, `INVALID_TRANSITION`, `DEPENDENCY_CONFLICT`, `DEPENDENCY_CYCLE`, `IDEMPOTENCY_CONFLICT` |
| `task submit` | `VERSION_CONFLICT`, `INVALID_TRANSITION`, `UNSATISFIABLE_DEPENDENCY`, `RESULT_INVALID`, `IDEMPOTENCY_CONFLICT` |
| `task approve`, `task reject` | `VERSION_CONFLICT`, `INVALID_TRANSITION`, `RESULT_INVALID`, `IDEMPOTENCY_CONFLICT` |
| `task list`, `task show`, `task events` | no mutation errors; retain selection, input, permission, storage, schema, and missing-Task errors |

Phase 3 embedded commands require exact SQLite schema version `3`. A version
`2` store returns `SCHEMA_UNSUPPORTED` unchanged. Agent identities, Attempts,
Leases, claims, heartbeat, progress, release, `--attempt`, Tokens, remote
profiles, and servers are not Phase 3 command surfaces.

## Phase 4 Claim and execution contract

This section is the normative implementation contract for the Phase 4 Local
Agent Alpha implemented by `0.4.0a1`. It extends the Phase 3 embedded command
contract and is exercised by the public quick start, cumulative conformance
suites, and enabled Phase 4 golden journey.

Phase 4 commands retain the `workaholic.cli/v1` envelope, JSON-only stdout,
diagnostics-only stderr, non-interactive behavior, Project selection, bounded
input, and safe error rules defined above. All objects below are closed: every
listed field is required, nullable values are explicit JSON `null`, and unknown
input fields are invalid.

### Lease duration contract

Lease text matches the complete regular expression
`^[1-9][0-9]*(s|m|h|d)$`. Compound, fractional, signed, zero,
whitespace-padded, and unitless values are invalid.

| Command path | Default | Minimum | Maximum |
| --- | ---: | ---: | ---: |
| Human claim or renew | `8h` | `1m` | `30d` |
| Agent claim or heartbeat | `15m` | `1s` | `24h` |

The application resolves duration text before persistence. Renewal sets
`lease_expires_at = authoritative_now + resolved_duration`; it never adds to
the previous expiry. Omitting `--lease` on a renewal uses the applicable
default rather than the previous duration.

### Shared Claim object

Every successful Claim operation returns this exact `TaskClaim` object:

```json
{
  "task_uid": "tsk_01K9Q...",
  "task_key": "ACME-42",
  "subject_id": "sub_01K9...",
  "attempt_id": null,
  "claimed_at": "2026-08-02T09:00:00Z",
  "lease_expires_at": "2026-08-02T17:00:00Z"
}
```

An existing Claim with `attempt_id = null` is Human-owned. A non-null Attempt
identifies Agent execution. Claim absence, rather than null Attempt alone,
means unclaimed. Phase 4 uses the sole bootstrap Subject for both command paths;
the ownership token is `(subject_id, attempt_id)`. `TaskEvent.actor_kind`
therefore remains `human`; a non-null `attempt_id` is the Agent attribution.
Human Lease windows are longer than Agent Lease windows.

Every Agent operation also returns this exact `TaskAttempt` object:

```json
{
  "id": "atm_01K9R...",
  "task_uid": "tsk_01K9Q...",
  "subject_id": "sub_01K9...",
  "status": "active",
  "lease_expires_at": "2026-08-02T09:15:00Z",
  "started_at": "2026-08-02T09:00:00Z",
  "ended_at": null
}
```

Status is exactly `active`, `released`, `expired`, or `submitted`. `ended_at`
is null only for `active`; every terminal Attempt has a non-null `ended_at`.
The Attempt and its Claim always contain the same Task, Subject, and Lease
expiry while active.

Successful claim and renewal data is:

```json
{
  "task": {},
  "claim": {},
  "attempt": null,
  "events": [{}]
}
```

`task` is the complete shared Task object, `claim` is `TaskClaim`, and
`attempt` is null for a Human or `TaskAttempt` for an Agent. Claim appends one
`task_claimed`; renew or heartbeat appends one `claim_renewed`. An idempotent
replay returns the original closed object and events without appending again.

Successful release data is:

```json
{
  "task": {},
  "claim": null,
  "attempt": null,
  "events": [{}]
}
```

Human release returns null Attempt. Agent release returns the terminal
`released` Attempt. Explicit release appends exactly one `claim_released`.
Submission and cancellation end a Claim through their existing events and do
not also append `claim_released`.

### Human Claim commands

```text
workaholic task claim TASK [--lease DURATION]
  [--project KEY] [--idempotency-key KEY]
workaholic task renew TASK [--lease DURATION]
  [--project KEY] [--idempotency-key KEY]
workaholic task release TASK
  [--project KEY] [--idempotency-key KEY]
```

Human claiming is optional and targets one ready Task. It returns a Claim with
null Attempt attribution. Human renewal and release derive ownership from the
active Session Subject and never accept or display an Attempt ID. Repeating
`task claim TASK` for an already owned current Claim returns it without
extending the Lease and returns no new events. Only `task renew` extends it;
reads and other mutations do not renew implicitly.

The Human owner may update, block, unblock, change dependencies, release,
cancel, or submit. Update, block/unblock, and dependency changes retain the
Claim. Release, expiry, cancel, and submit end it.

### Agent Claim and execution commands

```text
workaholic task claim [--lease DURATION]
  [--project KEY] [--idempotency-key KEY]
workaholic task heartbeat TASK --attempt ATTEMPT [--lease DURATION]
  [--project KEY] [--idempotency-key KEY]
workaholic task progress TASK --attempt ATTEMPT --input-file PATH|-
  [--project KEY] [--idempotency-key KEY]
workaholic task release TASK --attempt ATTEMPT
  [--project KEY] [--idempotency-key KEY]
workaholic task submit TASK --attempt ATTEMPT --expected-version INTEGER
  --result-file PATH|- [--project KEY] [--idempotency-key KEY]
```

Omitting the Task operand from `task claim` selects Agent pull-next behavior.
It atomically selects the highest-ranked ready Task and returns its packet,
current Task version, new Attempt, and Agent Claim. Capability filtering is
outside v1; the claimant is assumed capable of its selected Task.

An Agent owner may only heartbeat, report progress, release, or submit through
the current Attempt. It cannot update, block, cancel, or change dependencies on
the claimed Task. Agent submission requires both the current Attempt and the
expected Task version returned by claim.

Agent pull is scoped to one selected Project. It does not search all Projects
and does not accept capabilities. Ready selection remains priority descending,
availability ascending with absent availability first, then Task number
ascending. No eligible Task returns `NO_TASK_AVAILABLE`.

### Structured progress input and success

`task progress --input-file` accepts this closed object:

```json
{
  "message": "Implemented persistence; running integration tests.",
  "percent_complete": 70,
  "observations": [
    {
      "kind": "risk",
      "text": "The upstream schema may change."
    }
  ]
}
```

At least one field must be present. `message` is optional and contains 1
through 4,000 printable Unicode characters after trimming.
`percent_complete` is an optional real JSON integer from 0 through 100.
`observations` is optional and contains at most 50 ordered closed objects.
Every observation has `kind` exactly `note`, `risk`, `blocker`, or `question`
and `text` of 1 through 4,000 printable characters after trimming.

Observations are inert audit data. A `blocker` observation does not block or
otherwise mutate the Task. Clients cannot provide Task, Subject, actor,
Attempt, Result, request, event, cursor, or timestamp identity in this input.

Progress success data is:

```json
{
  "task": {},
  "claim": {},
  "attempt": {},
  "events": [{}, {}]
}
```

The request appends `progress_reported` first, even when only observations are
provided, then one `observation_added` per observation in input order. It does
not create a progress table or change Task state, version, or `updated_at`.

### Agent submission success

Agent submission uses the Phase 3 Result input and returns:

```json
{
  "task": {},
  "result": {},
  "claim": null,
  "attempt": {},
  "events": [{}]
}
```

The Result has the submitted Attempt ID. The returned Attempt is `submitted`
with non-null `ended_at`, including when the Task enters `review`. Successful
submission increments Task version exactly once, stores one Result, ends the
Claim, and appends the existing submission events. Review never changes the
terminal Attempt; rejection requires a new Claim and Attempt.

### Lock, renewal, version, and terminal rules

An unexpired Claim is an exclusive mutation lock. A non-owner mutation fails
without changing Task, Claim, Attempt, Result, version, idempotency, or event
state. Reads remain available. Workaholic AI has no force-interrupt command for
an external Agent process.

Human `task renew` and Agent heartbeat share one semantic renewal operation.
Lease validity uses authoritative transaction time and
`now < lease_expires_at`; expiry requires no daemon.

Pure reads never materialize Lease expiry or append events. An expired stored
Claim is projected as stale and non-owning, does not block readiness, and may
make the Task both `ready` and `stale`. The next successful write that needs
that Task first
materializes expiry in its transaction. It removes the Claim, changes an Agent
Attempt to `expired` with `ended_at = lease_expires_at`, and appends
`claim_expired` using the expired Attempt where present. The event occurs at
the authoritative transaction time and carries `lease_expires_at` in its
payload. An expired Agent request returns `LEASE_LOST` without committing its
requested operation; a later successful claim or Human mutation may
materialize the expiry.

Claim, renew, heartbeat, progress, release, and expiry do not change the Task
version. Existing Human Task mutations retain the expected-version convenience
and increment rules. Successful Human or Agent submission ends the Claim and
increments the Task version once.

Agent Attempt states are exactly `active`, `released`, `expired`, and
`submitted`. The last three are terminal and populate `ended_at`. Submission
always produces `submitted`, including when the Task enters review. Approval
and rejection operate on the Result and never revive the Attempt; rejection
requires a new Claim and Attempt.

### Phase delivery boundary

Phase 4 embedded Human and Agent commands reuse the sole bootstrap Subject.
Human command shape and null Attempt attribution distinguish the Human path;
Attempt identity distinguishes Agent processes. Phase 5 introduces additional
Human and Agent Subjects, Tokens, ProjectGrants, and authenticated ownership.

### Phase 4 events and idempotency

Phase 4 adds `task_claimed`, `claim_renewed`, `claim_released`,
`claim_expired`, `progress_reported`, and `observation_added` to the exact
TaskEvent type set. Claim-related payloads contain the resulting
`lease_expires_at`; `claim_expired` contains the expired
`lease_expires_at`; `progress_reported` contains only supplied progress fields;
and `observation_added` contains one observation. Payloads never contain
credentials or artifact contents.

Claim and execution idempotency fingerprints include Project, Task selector
when present, nullable Attempt, resolved Lease duration, expected version when
present, and the complete structured payload. Equivalent replay returns the
original outcome. Reuse with any different semantic value returns the existing
`IDEMPOTENCY_CONFLICT`. Failed validation, `TASK_LOCKED`, `LEASE_LOST`, and
`NO_TASK_AVAILABLE` do not consume an idempotency key.

### Phase 4 errors and persistence boundary

Phase 4 adds these exact safe errors:

| Error code | Exit | Retryable | Exact message |
| --- | ---: | :---: | --- |
| `NO_TASK_AVAILABLE` | 3 | true | `No ready Task is available to claim.` |
| `TASK_LOCKED` | 4 | true | `The Task has a current Claim owned by another execution.` |
| `LEASE_LOST` | 4 | false | `The Claim is no longer current.` |

Missing Attempt flags, malformed durations, invalid structured progress, and
mutually inconsistent operands return `INVALID_INPUT`. An unknown, foreign,
expired, released, submitted, or superseded Attempt returns `LEASE_LOST`
without exposing execution history. Phase 4 embedded commands require exact
SQLite schema version `4`. Version `3` and every other version return
`SCHEMA_UNSUPPORTED` unchanged. Phase 4 provides no migration, conversion,
import, export, or silent reset.

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
- [ADR 0011: Phase 3 Task Mutation and Human Submission](adr/0011-phase-three-task-mutation-and-human-submission.md)
- [ADR 0012: Phase 4 Local Claim and Execution Model](adr/0012-phase-four-local-claim-and-execution-model.md)
- [ADR 0002: Local and Remote Sessions](adr/0002-local-and-remote-sessions.md)
- [ADR 0004: Private Versioned Client/Server Protocol](adr/0004-private-versioned-client-server-protocol.md)
- [Compatibility policy](compatibility-policy.md)
- [Threat model](threat-model.md)
- [Architecture](architecture.md)
