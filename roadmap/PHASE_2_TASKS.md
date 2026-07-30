# Phase 2 Implementation Tasks

## Purpose

Deliver the Multi-project Alpha: one embedded Workaholic Instance can contain
several Projects, each Workspace discovers its Project from safe local context,
and operators can create, bind, inspect, and list work across Projects without
changing the agent-facing CLI boundary.

Tasks are ordered by dependency. Each task is independently reviewable and is
intended to be implemented by a separate developer after all listed inputs have
merged.

## Repository state at planning time

The following deliverables already exist and must be extended rather than
recreated:

- production package metadata, Python 3.14 lock state, pre-commit hooks, strict
  linting and typing, dependency-boundary checks, least-privilege CI, and
  clean-install smoke tests;
- the Phase 1 dependency-free domain, Pydantic application boundaries,
  `WorkaholicSession`, embedded `LocalSession`, SQLite adapter, and explicit
  composition root;
- strict, bounded, non-executable exact-directory `.workaholic.env` parsing,
  atomic context writes, and safe conventional `.git/info/exclude` updates;
- schema-version rejection, atomic Task creation, stable Project-local task
  numbering, attributable TaskEvents, idempotency, and project-bound opaque
  pagination;
- versioned `workaholic.cli/v1` success and error envelopes and the Phase 1
  Project and Task commands;
- persistence and Session contract-test infrastructure, real-process golden
  journey infrastructure, and a skipped Phase 2 multi-project specification;
- public README, architecture, security, compatibility, and delivery
  documentation with tests that prevent capability overclaims.

No duplicate quality-control, package-bootstrap, licensing, community,
repository-management, or CI-foundation task is required for Phase 2. The
existing controls remain mandatory for every task below.

## Confirmed Phase 2 decisions

Implementation and documentation must consistently encode these owner-approved
decisions:

- Phase 2 supports configurable, trusted, user-level **embedded SQLite
  profiles only**. Remote profiles, URLs, credentials, login, Tokens,
  `RemoteSession`, and network transport remain unavailable until their later
  roadmap phases.
- A trusted profile selects exactly one embedded data store and Instance. The
  nearest valid Workspace context selects the default Project. An explicit
  `--project` may select another authorized Project only inside that same
  Instance. `--all-projects` means all Projects authorized for the active local
  Subject in that Instance.
- `project create` operates on a resolved profile and automatically grants the
  bootstrap local Human the Owner role. `up` remains the operation that
  initializes an empty profile; additional Projects use `project create`.
- `project bind` is naturally idempotent for an equivalent binding and never
  silently replaces a different Project, Instance, or profile binding. An
  intentional replacement requires an explicit `--replace` option and may
  replace only an otherwise valid regular context file.
- Upward discovery starts from the canonical physical working directory and
  walks to the filesystem root. Git repository and worktree boundaries do not
  stop discovery. The nearest context file is authoritative; if it is invalid,
  resolution fails instead of falling back to a parent.
- A context file must be a bounded regular non-symlink file. Its relative
  Workspace root resolves from the context file's directory to an existing
  directory and must not escape that directory through `..` or symlink
  traversal.
- The repository-local context remains untrusted. It cannot provide an
  endpoint, credential, Token, executable path, storage path, or profile
  definition. Instance, Project, and Project-key values from context must
  match authoritative state before a read or mutation.
- Phase 2 introduces disposable SQLite schema version `2`. Version `1` stores
  fail safely with `SCHEMA_UNSUPPORTED` and require an explicit development
  reset; no migration, conversion, import, or export path is added.

## Phase 2 behavioral baseline

The implementation tasks use these concrete defaults:

- Profile names match `[a-z][a-z0-9_-]{0,31}`.
- Project display names contain 1 through 200 Unicode characters after
  trimming. Project keys retain the Phase 1
  `[A-Z][A-Z0-9]{1,15}` contract.
- Omitting `--project-name` from `up` preserves the Phase 1 invocation and
  uses the normalized Project key as the initial display name.
- The trusted profile file is `profiles.toml` in the operating system's
  Workaholic user-configuration directory. `WORKAHOLIC_CONFIG_DIR` may select
  an absolute test- or operator-owned configuration directory.
- If `profiles.toml` is absent, the built-in `local` profile remains available
  and uses the existing `WORKAHOLIC_DATA_DIR` or platform user-data default.
- A profile file has exact version `1`, an optional `default_profile`, and
  `[profiles.NAME]` tables containing only `mode = "embedded"` and an absolute
  `data_directory`. Unknown keys, duplicate semantic values, unsupported
  versions, unsafe files, and non-embedded modes fail explicitly.
- Configured profile names map one-to-one to canonical data directories; two
  names cannot alias the same embedded Instance.
- Profile precedence is explicit `--profile`, trusted
  `WORKAHOLIC_PROFILE`, discovered context, configured default profile, then
  built-in `local`.
- Project precedence is explicit `--project`, discovered context, then a
  structured `CONTEXT_NOT_FOUND` or `PROJECT_NOT_FOUND` failure. Commands that
  do not require one Project, such as `project create` and `project list`, need
  only a resolved profile.
- Project lists order by immutable Project key. One-Project Task lists order by
  task number. `task list --all-projects` orders by `(project key, task number)`
  ascending and uses a versioned opaque cursor bound to the Instance, Subject,
  selection scope, and ordering position.
- Project creation is idempotent only when an idempotency key is supplied.
  Binding the same Project to the same canonical path is naturally idempotent.
- Phase 2 does not expose Project rename, archival, deletion, Task movement,
  additional Subjects, or new Project roles. Because Projects cannot be
  deleted or archived through Phase 2, Project-key reuse remains impossible.

### Task 1: Align Phase 2 architecture and command contracts

- Deliverables:
  - `docs/roadmap.md`
  - `docs/architecture.md`
  - `docs/cli-contract.md`
  - `docs/persistence-contract.md`
  - `docs/threat-model.md`
  - `docs/adr/0006-project-context-trust-model.md`
  - `tests/unit/docs/test_phase_two_contracts.py`
- Description: Reconcile the accepted Phase 2 decisions with all normative
  documents before implementation begins. Specify exact command signatures,
  success objects, error codes, profile-file grammar, precedence, Project
  selection, path resolution, replacement behavior, ordering, cursor binding,
  schema reset, and Phase 5-6 deferrals. Keep the README on verified Phase 1
  behavior until the Phase 2 acceptance journey passes.
- Public interface changes:
  - Commands:

    ```text
    workaholic up --project-key KEY [--project-name NAME] [--profile PROFILE]
    workaholic status [--profile PROFILE] [--project KEY]
    workaholic context [--profile PROFILE] [--project KEY]
    workaholic project create --key KEY --name NAME
      [--profile PROFILE] [--idempotency-key KEY]
    workaholic project bind KEY [PATH] [--profile PROFILE] [--replace]
    workaholic project list [--profile PROFILE]
    workaholic task add TITLE [--project KEY]
    workaholic task list [--project KEY | --all-projects]
    workaholic task show TASK [--project KEY]
    ```

  - Every command continues to accept `--json` and `--non-interactive`.
  - `PATH` for `project bind` defaults to the current directory.
  - `context` success data contains `mode`, `profile`, `schema_version`,
    `instance`, `project`, `workspace_root`, `subject`, and
    `context_source`.
  - Serialized Projects add required `name`; existing Project objects retain
    `id` and `key`.
  - Add exact error contracts for `PROFILE_NOT_FOUND`, `PROFILE_INVALID`,
    `PROFILE_UNSUPPORTED`, `PROJECT_NOT_FOUND`, and
    `WORKSPACE_BINDING_CONFLICT`, using existing exit categories.
  - Define `--project` and `--all-projects` as mutually exclusive and reject a
    cursor emitted for a different profile, Instance, Subject, Project, or
    selection scope.
- Inputs:
  - Accepted Phase 2 recommendations and existing Phase 1 contracts.
- Outputs:
  - One noncontradictory implementation contract that later tasks can encode
    without reopening product or security decisions.
- Tests:
  - Assert the five documents agree on delivery phase, schema version, profile
    modes, precedence, path behavior, error names, and deferred remote
    capabilities.
  - Assert no normative document claims that Phase 2 reads credentials,
    connects to remote endpoints, migrates schema version `1`, or changes
    shared `.gitignore`.
- Acceptance criteria:
  - Every public Phase 2 command and failure has one exact documented shape and
    ownership boundary before production code changes.

### Task 2: Extend dependency-free domain models for named Projects and profiles

- Deliverables:
  - `src/workaholic/domain/models.py`
  - `src/workaholic/domain/rules.py`
  - `src/workaholic/domain/__init__.py`
  - `tests/unit/domain/test_models.py`
  - `tests/unit/domain/test_rules.py`
- Description: Extend the dependency-free core with normalized Project display
  names and safe profile names while preserving immutable keys, stable Task
  identity, and the absence of adapter or filesystem imports. Generalize
  `WorkspaceBinding` from the Phase 1 built-in profile restriction to a
  validated named embedded profile and safe relative Workspace-root value.
- Public interface changes:
  - `Project` fields become `id`, `instance_id`, `key`, `name`, and
    `created_at`.
  - Add:

    ```python
    def normalize_project_name(value: object) -> str: ...
    def validate_profile_name(value: object) -> str: ...
    def validate_workspace_root(value: object) -> str: ...
    ```

  - `WorkspaceBinding.profile` accepts a validated profile name.
  - `WorkspaceBinding.workspace_root` accepts a normalized relative value but
    rejects absolute paths, nulls, empty values, and lexical parent escape.
- Inputs:
  - Exact value constraints established in Task 1.
- Outputs:
  - Immutable domain values reusable by application, context, persistence, and
    Session code.
- Tests:
  - Cover Unicode normalization and bounds, exact profile-name grammar,
    absolute and parent-traversing Workspace roots, false integer/boolean
    categories, and preservation of existing Project-key and Task-key rules.
- Acceptance criteria:
  - `workaholic.domain` remains dependency-free and contains no profile file,
    environment, SQLite, CLI, or path-discovery logic.

### Task 3: Define Phase 2 application, Session, and error boundaries

- Deliverables:
  - `src/workaholic/application/commands.py`
  - `src/workaholic/application/results.py`
  - `src/workaholic/application/ports.py`
  - `src/workaholic/application/errors.py`
  - `src/workaholic/application/__init__.py`
  - `src/workaholic/session/base.py`
  - `src/workaholic/session/models.py`
  - `src/workaholic/session/__init__.py`
  - `tests/unit/application/test_commands.py`
  - `tests/unit/application/test_errors.py`
  - `tests/unit/session/test_phase_two_models.py`
- Description: Define strict Pydantic inputs, semantic repository operations,
  result models, Session requests, and safe errors before implementing their
  adapters. Keep profile and Project selectors explicit and prevent CLI,
  filesystem, or SQL values from leaking into application commands.
- Public interface changes:
  - Add `ProjectCreateRequest`, `ProjectBindRequest`, and `ContextRequest`.
  - Add optional `profile` to profile-aware requests, optional `project` to
    Project-scoped requests, and `all_projects` only to `TaskListRequest`.
  - Add `CreateProjectInput`, `ProjectCreationMutation`,
    `GetProjectByKey`, and an Instance-scoped Task-list command.
  - Add `ProjectCreationResult` containing the committed `Project` and
    creator's `ProjectGrant`.
  - Add `ContextResult` containing the effective local selection and safe
    source paths, with no storage URI or profile-file contents.
  - Replace the Phase 1-specific repository protocol with cumulative
    `WorkaholicRepository`, retaining semantic methods and adding:

    ```python
    def create_project(
        self, mutation: ProjectCreationMutation
    ) -> ProjectCreationResult: ...

    def get_project_by_key(self, command: GetProjectByKey) -> Project: ...

    def list_tasks_for_instance(
        self, command: ListInstanceTasks
    ) -> TaskPage: ...
    ```

  - Extend `WorkaholicSession` with:

    ```python
    def create_project(
        self, request: ProjectCreateRequest
    ) -> ProjectCreationResult: ...
    def bind_project(self, request: ProjectBindRequest) -> ContextResult: ...
    def context(self, request: ContextRequest) -> ContextResult: ...
    ```

  - Add the five Task 1 error codes with fixed safe messages,
    retryability, and exit mappings.
- Inputs:
  - Task 1 contracts and Task 2 domain rules.
- Outputs:
  - Transport-neutral, runtime-validated boundaries for every Phase 2 use
    case.
- Tests:
  - Cover unknown fields, coercion rejection, mutually exclusive selection,
    selector bounds, profile names, canonical mutation fingerprints, result
    consistency, and exact error-code mappings.
- Acceptance criteria:
  - Later adapters can implement Phase 2 without importing presentation types
    into application or domain packages.

### Task 4: Implement trusted embedded profile configuration

- Deliverables:
  - `.env.example`
  - `src/workaholic/context/models.py`
  - `src/workaholic/context/paths.py`
  - `src/workaholic/context/profiles.py`
  - `src/workaholic/context/errors.py`
  - `src/workaholic/context/__init__.py`
  - `tests/unit/context/test_paths.py`
  - `tests/unit/context/test_profiles.py`
- Description: Load a bounded, versioned, data-only `profiles.toml` from a
  trusted user configuration directory. Preserve the built-in `local` profile
  when no file exists. Accept only embedded SQLite profile definitions and
  resolve their absolute data directories without opening storage.
- Public interface changes:

  ```python
  @dataclass(frozen=True, slots=True)
  class EmbeddedProfile:
      name: str
      data_directory: Path
      database_path: Path

  @dataclass(frozen=True, slots=True)
  class ProfileRegistry:
      default_profile: str
      profiles: Mapping[str, EmbeddedProfile]

  def resolve_local_config_paths(
      environment: Mapping[str, str],
  ) -> LocalConfigPaths: ...

  def load_profile_registry(
      paths: LocalConfigPaths,
      environment: Mapping[str, str],
  ) -> ProfileRegistry: ...
  ```

  - `.env.example` documents safe examples for
    `WORKAHOLIC_CONFIG_DIR`, `WORKAHOLIC_DATA_DIR`, and
    `WORKAHOLIC_PROFILE`; it contains no real secret or remote URL.
  - Continue using standard-library `tomllib`; add no configuration framework.
- Inputs:
  - Trusted profile grammar and precedence from Tasks 1-3.
- Outputs:
  - An immutable registry mapping approved names to exact embedded database
    paths.
- Tests:
  - Cover absent configuration, explicit defaults, multiple profiles,
    environment precedence, platform default failures, relative overrides,
    missing defaults, duplicates, unknown keys, invalid UTF-8, oversized
    files, symlinks, directories, remote modes, URL/credential/token keys, and
    no filesystem writes during reads.
- Acceptance criteria:
  - A repository-controlled file can name a profile but cannot define or
    redirect its storage, endpoint, or credentials.

### Task 5: Implement safe upward Workspace-context discovery

- Deliverables:
  - `src/workaholic/context/local.py`
  - `src/workaholic/context/models.py`
  - `src/workaholic/context/__init__.py`
  - `tests/unit/context/test_local_context.py`
  - `tests/unit/context/test_discovery.py`
- Description: Separate strict serialization/parsing from discovery and add
  canonical physical upward traversal. Return the binding, context source, and
  contained absolute Workspace root as one validated result. Preserve atomic
  writes and safe Git-exclude behavior.
- Public interface changes:

  ```python
  @dataclass(frozen=True, slots=True)
  class DiscoveredWorkspace:
      binding: WorkspaceBinding
      context_file: Path
      workspace_root: Path

  def discover_workspace_context(start: Path) -> DiscoveredWorkspace: ...

  def write_workspace_context(
      directory: Path,
      binding: WorkspaceBinding,
      *,
      replace: bool = False,
  ) -> Path: ...
  ```

  - The discovery walk uses canonical physical ancestors through filesystem
    root and does not call Git.
  - A nearer invalid or unreadable context terminates resolution.
  - `replace=True` can atomically replace only a valid regular context file;
    it never follows or replaces a symlink, directory, malformed file, or file
    that changed during validation.
- Inputs:
  - Task 2 binding rules and Task 4 trusted profile names.
- Outputs:
  - Deterministic safe discovery usable from nested repositories and deep
    directories.
- Tests:
  - Cover nearest-file wins, parent discovery, filesystem root termination,
    malformed-nearer hard failure, nested Git repositories, canonical
    directory aliases, context-file symlinks, root symlink escape, relative
    roots, concurrent replacement, equivalent idempotent writes, and
    preservation of `.gitignore`.
- Acceptance criteria:
  - Discovery cannot execute content, fall through an invalid nearer file, or
    resolve a Workspace outside the context directory.

### Task 6: Create and validate the Phase 2 SQLite schema

- Deliverables:
  - `src/workaholic/persistence/sqlite/schema.py`
  - `src/workaholic/persistence/sqlite/_records.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `src/workaholic/persistence/sqlite/__init__.py`
  - `tests/integration/persistence/test_sqlite_schema.py`
  - `tests/unit/persistence/test_sqlite_records.py`
- Description: Define clean-store schema version `2`, add the Project display
  name required by Phase 2, and rename Phase 1-specific adapter symbols to
  cumulative names. Do not implement `ALTER`, migrations, imports, or
  best-effort version interpretation.
- Public interface changes:
  - `SCHEMA_VERSION = 2`.
  - `projects` stores non-null normalized `name` in addition to existing
    identity and allocation fields.
  - Idempotency records support the `project.create` semantic operation.
  - `SQLitePhaseOneRepository` becomes internal cumulative
    `SQLiteRepository`; no compatibility alias is required for this
    pre-release internal class.
- Inputs:
  - Task 2 Project model and Task 3 repository protocol.
- Outputs:
  - One atomically initialized exact-version store capable of multiple named
    Projects.
- Tests:
  - Verify a complete empty-store schema, constraints, indexes, foreign keys,
    Project-name round trips, and initialization races.
  - Verify version `1`, malformed, missing, and future versions fail without
    changing any byte or schema object.
- Acceptance criteria:
  - SQLite never reads or mutates a Phase 1 store as though it were Phase 2.

### Task 7: Implement atomic and idempotent Project creation

- Deliverables:
  - `src/workaholic/application/projects.py`
  - `src/workaholic/application/__init__.py`
  - `src/workaholic/persistence/sqlite/_projects.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/application/test_projects.py`
  - `tests/integration/persistence/test_sqlite_projects.py`
  - `tests/integration/persistence/test_sqlite_concurrency.py`
- Description: Add a Project application service and one SQLite transaction
  that validates the target Instance and creator, allocates the Project
  identity, inserts its immutable key and display name, grants the creator
  Owner, and records an optional idempotent outcome. Project creation does not
  fabricate a TaskEvent.
- Public interface changes:

  ```python
  class ProjectApplication:
      def create(
          self, command: CreateProjectInput
      ) -> ProjectCreationResult: ...
  ```

  - Same idempotency key and normalized input returns the original Project and
    grant; different input returns `IDEMPOTENCY_CONFLICT`.
  - Any existing or historically reserved key returns
    `PROJECT_KEY_CONFLICT`, including when its display name differs.
- Inputs:
  - Tasks 2, 3, and 6.
- Outputs:
  - Multiple independently numbered Projects in one Instance, all initially
    owned by the local bootstrap Human.
- Tests:
  - Cover authorization, disabled/mismatched creator, normalization,
    duplicate keys, idempotent replay, conflicting reuse, rollback, concurrent
    same-key creation, and concurrent different-key creation.
- Acceptance criteria:
  - A failed Project creation leaves no Project, grant, idempotency outcome, or
    consumed visible Project key.

### Task 8: Implement multi-project lookup and Task pagination

- Deliverables:
  - `src/workaholic/application/queries.py`
  - `src/workaholic/application/results.py`
  - `src/workaholic/persistence/sqlite/_queries.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/application/test_queries.py`
  - `tests/integration/persistence/test_sqlite_queries.py`
- Description: Extend read operations to resolve a Project by immutable key,
  return all Projects authorized in one Instance, and page Tasks across those
  Projects. Preserve the one-Project query path and use one explicit cursor
  codec shared by both scopes.
- Public interface changes:
  - `get_project_by_key` is scoped to `instance_id` and `subject_id`.
  - `list_tasks_for_instance` includes only authorized Projects in the
    selected Instance and orders by `(project.key, task.number)`.
  - Phase 2 cursors use a new version prefix and bind their canonical payload
    to Instance, Subject, selection kind, selected Project when present, and
    last ordering tuple.
- Inputs:
  - Tasks 3, 6, and 7.
- Outputs:
  - Deterministic Project override and `--all-projects` query primitives.
- Tests:
  - Cover two independent Project sequences, empty Projects, page boundaries,
    gaps, authorization filtering, duplicate-key impossibility, malformed and
    noncanonical cursors, cursor reuse across scopes/Projects/Instances/
    Subjects, and unchanged-record no-duplicate/no-omission traversal.
- Acceptance criteria:
  - Human task keys remain stable and unambiguous inside an Instance while
    cross-Instance operations always carry Instance identity.

### Task 9: Implement the Project binding workflow

- Deliverables:
  - `src/workaholic/application/queries.py`
  - `src/workaholic/context/local.py`
  - `src/workaholic/session/local.py`
  - `tests/unit/session/test_project_binding.py`
  - `tests/integration/context/test_project_binding.py`
- Description: Resolve an existing authorized Project in a selected embedded
  profile, build an authoritative `WorkspaceBinding`, and atomically write it
  at a canonical existing target directory. Update local Git exclusion only
  after the binding is durable. Do not add a persisted Workspace table.
- Public interface changes:
  - `LocalSession.bind_project(ProjectBindRequest)` returns `ContextResult`.
  - Equivalent binding is a successful no-op.
  - A different valid binding returns `WORKSPACE_BINDING_CONFLICT` unless
    `replace=True`.
  - `replace=True` still requires the existing file to pass hostile-input and
    regular-file validation.
- Inputs:
  - Tasks 4, 5, 7, and 8.
- Outputs:
  - Any number of local directories may safely bind to one Project.
- Tests:
  - Cover missing Project, cross-Instance key, profile mismatch, unauthorized
    Subject, same Project at two paths, equivalent retry, explicit
    replacement, malformed/symlink refusal, non-Git directories,
    conventional Git excludes, and storage success followed by filesystem
    failure.
- Acceptance criteria:
  - A binding never duplicates database state and never silently changes a
    Workspace's profile, Instance, or Project.

### Task 10: Generalize LocalSession selection and context reporting

- Deliverables:
  - `src/workaholic/session/base.py`
  - `src/workaholic/session/local.py`
  - `src/workaholic/session/models.py`
  - `src/workaholic/application/results.py`
  - `tests/unit/session/test_local_session.py`
  - `tests/unit/session/test_phase_two_selection.py`
- Description: Replace exact-directory, one-store assumptions with a shared
  selection pipeline inside `LocalSession`. Resolve the trusted profile first,
  open its embedded runtime, validate any discovered binding against
  authoritative state, apply an explicit same-Instance Project override, and
  then invoke the existing application services.
- Public interface changes:
  - Add explicit ports for profile resolution and opening one
    profile-selected local application runtime; `LocalSession` still does not
    import SQLite.
  - `context()` returns the effective profile, Instance, Project, Subject,
    schema version, canonical Workspace root, and context source.
  - `up()` initializes only the selected empty profile and writes context in
    the exact current directory.
  - `create_project()` and `list_projects()` can operate with a resolved
    profile and no Workspace context.
  - Task operations require discovered context or explicit `--project`;
    explicit Project selection never changes profile or Instance.
- Inputs:
  - Tasks 3-9.
- Outputs:
  - One transport-neutral Session behavior shared by all Phase 2 CLI commands.
- Tests:
  - Use strict fakes to cover every precedence level, no-context Project
    creation, profile not found/invalid/unsupported, context-authority
    mismatches, explicit Project override, all-Projects selection, invalid
    adapter results, and no repository call after selection failure.
- Acceptance criteria:
  - Session requests contain no caller-supplied actor or database path and all
    repository operations use one verified Instance and local Subject.

### Task 11: Wire profile-aware embedded composition

- Deliverables:
  - `src/workaholic/composition.py`
  - `src/workaholic/context/__init__.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/test_composition.py`
- Description: Compose profile configuration, upward context discovery,
  canonical working-directory paths, profile-selected SQLite repositories,
  local actor selection, application services, clock, and identifier factory
  without adding import-time I/O. Keep the CLI lightweight and the
  presentation boundary dependent only on `WorkaholicSession`.
- Public interface changes:
  - `create_local_session(...)` accepts injectable environment, current
    directory, config-path resolver, and factories for deterministic tests.
  - Production `main()` reads the process environment and current directory
    only when a command acquires its Session.
- Inputs:
  - Tasks 4-10.
- Outputs:
  - A real source-checkout runtime able to select multiple isolated embedded
    profiles and Projects.
- Tests:
  - Cover lazy construction, two profile databases, process restart,
    unavailable working directory/config directory, unsupported schema,
    missing context, and redaction of paths or TOML content from unexpected
    failures.
- Acceptance criteria:
  - Importing `workaholic.cli.main`, requesting help, or printing the version
    opens no profile file or database and imports no server or remote-client
    module.

### Task 12: Expose Project creation, binding, and context commands

- Deliverables:
  - `src/workaholic/cli/main.py`
  - `src/workaholic/cli/options.py`
  - `src/workaholic/cli/project.py`
  - `src/workaholic/cli/context.py`
  - `src/workaholic/cli/up.py`
  - `src/workaholic/cli/status.py`
  - `src/workaholic/cli/serialization.py`
  - `tests/unit/cli/test_project.py`
  - `tests/unit/cli/test_context.py`
  - `tests/unit/cli/test_up.py`
  - `tests/unit/cli/test_status.py`
- Description: Add the Phase 2 Project mutations and effective-context
  diagnostic through `WorkaholicSession`. Extend existing bootstrap, status,
  and Project-list commands with profile-aware options and exact JSON shapes.
- Public interface changes:
  - Add `project create`, `project bind`, and root `context`.
  - Add reusable `--profile`, `--project`, and `--replace` option aliases with
    non-interactive behavior and no implicit prompts.
  - Project JSON includes `id`, `key`, and `name`.
  - Human output remains deterministic and never prints profile contents,
    storage paths beyond documented safe context paths, or raw exceptions.
- Inputs:
  - Tasks 1, 3, and 10-11.
- Outputs:
  - Public CLI access to Project administration and context diagnosis.
- Tests:
  - Assert exact request mapping, envelopes, human output, exit categories,
    option conflicts, default bind path, spaces and Unicode in names/paths,
    replacement behavior, JSON-only stdout, and error redaction.
- Acceptance criteria:
  - A user can initialize ACME, create DOCS, bind DOCS elsewhere, and inspect
    both effective contexts without direct application or database access.

### Task 13: Expose explicit and all-Project Task selection

- Deliverables:
  - `src/workaholic/cli/options.py`
  - `src/workaholic/cli/task.py`
  - `src/workaholic/cli/serialization.py`
  - `tests/unit/cli/test_task_add.py`
  - `tests/unit/cli/test_task_list.py`
  - `tests/unit/cli/test_task_show.py`
  - `tests/integration/cli/test_local_cli.py`
- Description: Extend existing Task commands with explicit same-Instance
  Project selection and cross-Project listing. Preserve context-selected
  behavior as the normal path and preserve the existing Task JSON object.
- Public interface changes:
  - `task add` and `task show` accept optional `--project KEY`.
  - `task list` accepts optional `--project KEY` or `--all-projects`.
  - `--project` and `--all-projects` are mutually exclusive at the validated
    request boundary.
  - Cross-Project human output is ordered exactly like JSON and continues to
    display stable Project-prefixed Task keys.
- Inputs:
  - Tasks 8, 10, and 11.
- Outputs:
  - Agents and Humans can select another Project explicitly or inspect one
    deterministic page across the active Instance.
- Tests:
  - Cover context default, explicit override, no context with explicit
    Project, all-Projects ordering and pagination, selector conflicts,
    cross-Instance refusal, wrong-prefix Task lookup, empty results, and cursor
    scope rejection through real SQLite and fresh CLI processes.
- Acceptance criteria:
  - Normal bound-Workspace usage requires no `--project`, while every override
    remains explicit and Instance-contained.

### Task 14: Add reusable Phase 2 persistence and Session conformance suites

- Deliverables:
  - `tests/contract/phase_two.py`
  - `tests/contract/test_phase_two_persistence.py`
  - `tests/contract/test_phase_two_session.py`
  - `tests/contract/README.md`
  - `tests/contract/fixtures/README.md`
- Description: Add cumulative, adapter-neutral contracts for multi-Project
  identity, creation, selection, pagination, profile isolation, binding, and
  context authority. Reuse Phase 1 assertions where their behavior remains
  valid instead of copying tests.
- Public interface changes:
  - `PhaseTwoRepositoryFactory` creates one isolated exact-version repository
    and supports deterministic IDs and clock.
  - `PhaseTwoSessionFactory` creates isolated profiles and Workspace
    directories without relying on the operator's real configuration.
- Inputs:
  - Tasks 6-13.
- Outputs:
  - Executable behavioral specifications that future JSON and PostgreSQL
    adapters and `RemoteSession` must eventually satisfy where applicable.
- Tests:
  - Cover independent number sequences, immutable keys, duplicate and
    idempotency races, profile/Instance isolation, same key in unrelated
    Instances, binding consistency, hostile context, explicit selection,
    all-Projects cursors, authorization, rollback, restart, and no read-side
    mutation.
- Acceptance criteria:
  - SQLite and LocalSession pass the full Phase 1 and Phase 2 cumulative
    contract suites with no adapter-specific branches in expected outcomes.

### Task 15: Enable the multi-project golden journey

- Deliverables:
  - `tests/e2e/golden/test_multi_project_journey.py`
  - `tests/golden.py`
  - `tests/conftest.py`
  - `tests/e2e/golden/README.md`
  - `tests/unit/test_golden_contract_helpers.py`
  - `tests/unit/test_golden_journey_inventory.py`
- Description: Update the existing skipped specification to use the accepted
  bootstrap-only `up`, `project create`, and `project bind` flow, then remove
  only its Phase 2 skip. Run every operation through fresh real CLI processes
  and isolated trusted configuration and data directories.
- Public interface changes:
  - Golden runner permits only the documented Phase 2 trusted environment
    overrides; it still rejects URL, Token, credential, Python-path, and
    arbitrary environment injection.
  - Journey:

    ```text
    initialize ACME → create DOCS → bind two directories
      → create ACME-1 and DOCS-1 from deep descendants
      → list each context → list both Projects together
    ```

- Inputs:
  - Tasks 11-14.
- Outputs:
  - Executable evidence for the Phase 2 exit gate and dogfooding baseline.
- Tests:
  - Include nearest nested context, two paths bound to one Project, independent
    sequences, explicit override, all-Projects ordering, restart persistence,
    malformed-nearer rejection, and same key in isolated profiles.
- Acceptance criteria:
  - `uv run pytest -m golden` passes solo and multi-project journeys and reports
    exactly four future-phase skips.

### Task 16: Publish Phase 2 documentation and alpha metadata

- Deliverables:
  - `README.md`
  - `CHANGELOG.md`
  - `pyproject.toml`
  - `src/workaholic/__init__.py`
  - `.env.example`
  - `docs/architecture.md`
  - `docs/cli-contract.md`
  - `docs/persistence-contract.md`
  - `docs/threat-model.md`
  - `tests/unit/docs/test_public_documentation.py`
  - `tests/unit/docs/test_phase_two_contracts.py`
  - `tests/unit/test_package_metadata.py`
- Description: Replace Phase 1's single-Project notices with the verified
  multi-project workflow, document trusted local profiles and hostile-context
  behavior, explain the schema version `2` reset, and set pre-release metadata
  to `0.2.0a1`. Preserve prominent deferrals for Agents, Tokens, remote
  operation, and alternate persistence adapters.
- Public interface changes:
  - Package version: `0.2.0a1`.
  - README quick start demonstrates initial bootstrap, second Project creation,
    binding, deep-directory discovery, and Task creation without a Project
    flag.
  - Document `profiles.toml`, `WORKAHOLIC_CONFIG_DIR`,
    `WORKAHOLIC_DATA_DIR`, and `WORKAHOLIC_PROFILE` without showing secrets or
    remote endpoints.
- Inputs:
  - Verified behavior and golden evidence from Tasks 12-15.
- Outputs:
  - Public-facing documentation that accurately describes the Multi-project
    Alpha and can be maintained in every later command-changing pull request.
- Tests:
  - Execute the README journey in isolated directories.
  - Assert package version, command inventory, config grammar, schema/reset
    notices, and implementation boundaries agree.
  - Assert README does not claim remote profiles, credentials, Agents,
    `RemoteSession`, server operation, JSON/PostgreSQL adapters, migration,
    Project archival, or Task updates exist.
- Acceptance criteria:
  - A new developer can complete the multi-project quick start without reading
    internal architecture documents or touching their real Workaholic data.

### Task 17: Execute the Phase 2 clean-state acceptance gate

- Deliverables:
  - `scripts/verify-phase-2.sh`
  - `scripts/smoke-phase-2-wheel.sh`
  - `tests/e2e/test_phase_2_distribution.py`
  - `.pre-commit-config.yaml`
  - `README.md`
  - `CHANGELOG.md`
  - Phase 2 GitHub epic and implementation issues
- Description: Add one fail-fast aggregate acceptance command and execute it
  from a fresh clone with empty temporary config, data, and Workspace
  directories. Validate source and installed-wheel behavior without reading or
  writing the operator's actual profiles or databases.
- Public interface changes:
  - Acceptance command: `scripts/verify-phase-2.sh`.
  - Required clean-state journey:

    ```bash
    uv sync --frozen
    uv run pre-commit run --all-files
    uv run pytest
    uv build
    scripts/smoke-install.sh dist/*.whl
    scripts/smoke-phase-2-wheel.sh dist/*.whl
    ```

  - The wheel smoke script creates a temporary virtual environment,
    `WORKAHOLIC_CONFIG_DIR`, `WORKAHOLIC_DATA_DIR`, profile file, and multiple
    Workspaces; it exercises discovery and both Project number sequences across
    fresh processes.
- Inputs:
  - All Phase 2 implementation and documentation from Tasks 1-16.
- Outputs:
  - Reproducible evidence for the Multi-project Alpha exit gate.
  - A closed Phase 2 epic and milestone only after protected `main` is green.
- Tests:
  - Prove malformed profile, remote profile, malformed nearer context,
    cross-Instance binding, version `1` store, cursor-scope mismatch, failing
    test, and malformed wheel fail at their documented boundaries.
  - Assert the gate rejects an active virtual environment, pre-existing build
    output, dirty tracked files, or any config/data path outside its owned
    temporary root.
- Acceptance criteria:
  - Required `quality`, `tests`, `build`, and `wheel-smoke` checks pass on the
    merge commit on `main`.
  - Solo and multi-project golden journeys pass; exactly four future journeys
    remain skipped.
  - Two bound directories create `ACME-1` and `DOCS-1` without Project flags,
    and source and wheel runs produce the same observable results.
  - Acceptance evidence is linked from the Phase 2 epic before closing
    milestone `2 - Multi-project Alpha`.

## Operational instructions

1. Before Task 1 begins, create one Phase 2 epic and issues for Tasks 1-17 in
   GitHub milestone `2 - Multi-project Alpha`. Copy each task's acceptance
   criteria into its issue and preserve task order through explicit dependency
   links.
2. Implement and merge Tasks 1-17 in order. Tasks may run in parallel only
   after every shared input has merged and their deliverables are disjoint.
3. Every developer installs and runs the repository's existing quality stages:

   ```bash
   uv sync --frozen
   uv run pre-commit install --hook-type pre-commit --hook-type pre-push
   uv run pre-commit run --all-files
   uv run pytest
   uv build
   ```

4. Every manual, integration, smoke, and acceptance run uses absolute,
   test-owned `WORKAHOLIC_CONFIG_DIR` and `WORKAHOLIC_DATA_DIR` values. Never
   load the operator's default `profiles.toml` or database in a test.
5. Phase 2 schema version `2` intentionally rejects Phase 1 version `1`.
   Preserve any development data needed outside Workaholic, then remove only
   the explicitly selected disposable test/development data after verifying
   its path. Do not add a migration, broad recursive cleanup, or silent reset.
6. Every pull request that changes commands, JSON objects, errors, profile
   grammar, environment variables, context discovery, prerequisites, storage,
   or support status updates README and the relevant contract documentation in
   the same change. README must never describe a capability before its
   acceptance tests pass.
7. Keep `.workaholic.env` nontracked. Binding may update only a safe local
   `.git/info/exclude`; changing a shared `.gitignore` requires a separate
   explicit repository-policy decision.
8. Do not add URL, credential, keyring, Token, HTTP, server, or remote-client
   dependencies in Phase 2. A non-embedded profile fails explicitly.
9. The `0.2.0a1` version is pre-release metadata, not a compatibility promise.
   Do not upload to PyPI or create a GitHub release without separate explicit
   owner authorization. Built artifacts remain CI and acceptance evidence.
10. After Task 17 merges, rerun the gate from the protected `main` merge
    commit, attach clean-state and CI evidence to the Phase 2 epic, and only
    then close the epic and milestone.
