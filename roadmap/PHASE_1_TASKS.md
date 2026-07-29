# Phase 1 Implementation Tasks

## Purpose

Deliver the first genuinely usable Workaholic AI product: one developer can
initialize a local project, create and inspect tasks through the CLI, close the
terminal, and observe the same state in a later process. The implementation
uses an embedded `LocalSession`, SQLite, attributable mutations, stable task
keys, versioned JSON output, and no daemon.

Tasks are ordered by dependency. Each task is independently reviewable and is
intended to be implemented by a separate developer after all listed inputs have
merged.

## Repository state at planning time

The following Phase 0 deliverables already exist and must be extended rather
than recreated:

- the `src/workaholic` package and its domain, application, session, context,
  authentication, persistence, and CLI package boundaries;
- the minimal `workaholic` executable, package metadata, locked environment,
  pre-commit hooks, strict type checking, and least-privilege CI;
- accepted architecture, CLI, persistence, security, compatibility, and ADR
  contracts;
- executable golden-journey infrastructure and the skipped Phase 1 solo
  specification in `tests/e2e/golden/test_solo_journey.py`;
- clean-checkout build and wheel-installation acceptance gates.

No Phase 1 domain entity, application use case, SQLite schema, context file,
`LocalSession`, task command, or persisted application state exists yet.

## Confirmed Phase 1 decisions

Implementation and documentation must consistently encode these
owner-approved decisions:

- Phase 1 writes and reads a versioned `.workaholic.env` only in the exact
  current directory. Upward discovery, multiple projects per active instance,
  and configurable trusted profiles remain Phase 2 work.
- Phase 1 persists a real bootstrap Human Subject, Instance-administrator
  status, and Owner ProjectGrant. Bearer Tokens, credential-store integration,
  identity-management commands, and additional Subjects remain Phase 5 work.
- Phase 1 commands implement `workaholic.cli/v1` JSON envelopes,
  `--non-interactive`, and mutation idempotency. Phase 4 adds Agent execution
  commands to this contract; it does not retrofit these foundations.
- SQLite is the only implemented adapter in Phase 1. Its physical schema is
  internal and disposable, records an exact schema version, and is never
  migrated automatically.
- The default local runtime is embedded and short-lived. No command starts a
  daemon or imports server, PostgreSQL, scheduler, or remote-client code.

## Phase 1 behavioral baseline

The first implementation must use these defaults consistently:

- Project keys match `[A-Z][A-Z0-9]{1,15}` and are immutable.
- A Task created with only a title copies that title into `objective`, starts
  in `open`, receives priority `50`, and starts at optimistic version `1`.
- Priority is an integer from `0` through `100`.
- Task titles contain 1 through 200 Unicode characters after trimming.
- Task objectives contain 1 through 4,000 Unicode characters after trimming.
- Human task keys use the immutable `PROJECT-NUMBER` form.
- Task lists order by task number ascending and paginate with an opaque cursor,
  a default page size of 100, and a maximum page size of 500.
- Reads never mutate persisted domain state.
- Every successful Task mutation and its TaskEvent commit atomically.
- Missing, malformed, older, or newer store versions fail without modifying
  the database.

### Task 1: Align Phase 1 architecture and command contracts

- Deliverables:
  - `docs/roadmap.md`
  - `docs/architecture.md`
  - `docs/cli-contract.md`
  - `docs/persistence-contract.md`
  - `docs/adr/0006-project-context-trust-model.md`
  - `docs/adr/0007-human-and-agent-identity-model.md`
  - `tests/unit/docs/test_phase_one_contracts.py`
- Description: Reconcile the accepted Phase 1 timing decisions with the
  existing documents before implementation begins. Specify exact
  command-specific success data, documented errors, defaults, idempotency
  behavior, ordering, pagination, local bootstrap attribution, exact-directory
  context behavior, and deferrals to Phases 2, 4, and 5. Clarify that "every
  mutation appends an event" means every accepted Task mutation in the Phase 1
  model; bootstrap entities do not fabricate TaskEvents without a Task.
  Clarify that Phase 1 initializes Task version `1`; version increments and
  stale-update rejection begin when Task updates arrive in Phase 3.
- Public interface changes:
  - Commands:

    ```text
    workaholic up --project-key KEY
    workaholic status
    workaholic project list
    workaholic task add TITLE
    workaholic task list
    workaholic task show TASK
    ```

  - Every command accepts `--json` and `--non-interactive`.
  - `up` and `task add` accept optional `--idempotency-key KEY`.
  - `task add` accepts optional `--objective TEXT` and `--priority INTEGER`.
  - `task list` accepts `--cursor CURSOR` and `--limit INTEGER`.
  - JSON success data:
    - `up`: `instance`, `project`, `subject`, and `workspace`;
    - `status`: `mode`, `schema_version`, `instance`, `project`, and `subject`;
    - `project list`: `projects`;
    - `task add` and `task show`: `task`;
    - `task list`: `tasks` and nullable `next_cursor`.
  - A serialized Task contains `uid`, `project_id`, `number`, `key`, `title`,
    `objective`, `state`, `priority`, `version`, `created_by`, `created_at`, and
    `updated_at`.
  - Phase 1 error codes:
    - `INVALID_INPUT`;
    - `CONTEXT_NOT_FOUND`;
    - `CONTEXT_INVALID`;
    - `NOT_INITIALIZED`;
    - `TASK_NOT_FOUND`;
    - `PROJECT_KEY_CONFLICT`;
    - `IDEMPOTENCY_CONFLICT`;
    - `PERMISSION_DENIED`;
    - `SCHEMA_UNSUPPORTED`;
    - `STORAGE_BUSY`;
    - `STORAGE_UNAVAILABLE`;
    - `INTERNAL_ERROR`.
  - Exit categories:
    - `2` for input or command-usage failures;
    - `3` for missing context, initialization, or records;
    - `4` for conflicts;
    - `5` for authorization failures;
    - `10` for storage or unexpected operational failures.
- Inputs:
  - Accepted Phase 0 architecture and ADRs.
  - Confirmed Phase 1 decisions and behavioral baseline in this document.
- Outputs:
  - A self-contained normative contract for every Phase 1 command.
  - No contradictory assignment of Phase 1 behavior to later phases.
- Tests:
  - Assert command names, JSON fields, defaults, error codes, context scope,
    identity scope, and phase deferrals remain consistent across documents.
  - Assert no Phase 1 document requires a Token, keyring, daemon, upward
    context search, or RemoteSession.
- Acceptance criteria:
  - Later tasks can implement every public behavior without inventing a
    command schema or revisiting an owner decision.

### Task 2: Implement dependency-free Phase 1 domain models

- Deliverables:
  - `src/workaholic/domain/identifiers.py`
  - `src/workaholic/domain/models.py`
  - `src/workaholic/domain/rules.py`
  - `src/workaholic/domain/errors.py`
  - `src/workaholic/domain/__init__.py`
  - `tests/unit/domain/test_identifiers.py`
  - `tests/unit/domain/test_models.py`
  - `tests/unit/domain/test_rules.py`
- Description: Implement immutable, standard-library-only domain value objects
  and entities. Constructors must validate their own invariants at runtime;
  type hints alone are insufficient. Keep persistence, Pydantic, CLI, clock,
  filesystem, and identifier-generation concerns outside the domain package.
- Public interface changes:
  - Identifier value objects:
    - `InstanceId`;
    - `ProjectId`;
    - `SubjectId`;
    - `TaskId`;
    - `TaskEventId`;
    - `RequestId`.
  - JSON event scalar:

    ```python
    type JsonScalar = None | bool | int | float | str
    ```

  - Enums:
    - `SubjectKind.HUMAN`;
    - `ProjectRole.OWNER`;
    - `TaskState.OPEN`;
    - `TaskEventType.TASK_CREATED`.
  - Immutable entities:

    ```python
    @dataclass(frozen=True, slots=True)
    class Instance:
        id: InstanceId
        created_at: datetime

    @dataclass(frozen=True, slots=True)
    class Subject:
        id: SubjectId
        kind: SubjectKind
        display_name: str
        enabled: bool
        is_instance_admin: bool

    @dataclass(frozen=True, slots=True)
    class Project:
        id: ProjectId
        instance_id: InstanceId
        key: str
        created_at: datetime

    @dataclass(frozen=True, slots=True)
    class ProjectGrant:
        subject_id: SubjectId
        project_id: ProjectId
        role: ProjectRole

    @dataclass(frozen=True, slots=True)
    class WorkspaceBinding:
        context_version: int
        profile: str
        instance_id: InstanceId
        project_id: ProjectId
        project_key: str
        workspace_root: str

    @dataclass(frozen=True, slots=True)
    class Task:
        uid: TaskId
        project_id: ProjectId
        number: int
        key: str
        title: str
        objective: str
        state: TaskState
        priority: int
        version: int
        created_by: SubjectId
        created_at: datetime
        updated_at: datetime

    @dataclass(frozen=True, slots=True)
    class TaskEvent:
        id: TaskEventId
        cursor: int
        task_uid: TaskId
        project_id: ProjectId
        actor_subject_id: SubjectId
        request_id: RequestId
        event_type: TaskEventType
        occurred_at: datetime
        payload: Mapping[str, JsonScalar]
    ```

  - Pure rules validate project keys, Task input bounds, stable Task keys,
    timezone-aware UTC timestamps, and Owner permission for Phase 1 writes.
  - Constructors defensively copy mutable collections before exposing
    read-only views so frozen entities cannot be mutated through an alias.
- Inputs:
  - Task invariants and field defaults fixed by Task 1.
- Outputs:
  - A dependency-free domain surface shared by application and persistence.
- Tests:
  - Cover every accepted and rejected boundary value.
  - Prove immutable objects cannot be modified after construction.
  - Prove Task keys cannot disagree with Project key and task number.
  - Prove naive or non-UTC timestamps are rejected.
- Acceptance criteria:
  - Importing `workaholic.domain` performs no I/O and imports no package outside
    the Python standard library.

### Task 3: Define validated application commands, results, and ports

- Deliverables:
  - `pyproject.toml`
  - `uv.lock`
  - `src/workaholic/application/commands.py`
  - `src/workaholic/application/results.py`
  - `src/workaholic/application/ports.py`
  - `src/workaholic/application/errors.py`
  - `src/workaholic/application/__init__.py`
  - `tests/unit/application/test_commands.py`
  - `tests/unit/application/test_errors.py`
  - `tests/unit/test_package_metadata.py`
- Description: Add Pydantic v2 at the application boundary and define strict,
  explicit command and result models. Define dependency-inversion ports for
  time, identifiers, and semantic persistence so the application never imports
  SQLite or filesystem code. Pydantic models use `extra="forbid"` and strict
  validation where coercion would be ambiguous.
- Public interface changes:
  - Commands:
    - `BootstrapLocalProjectInput`;
    - `GetLocalStatus`;
    - `ListProjects`;
    - `CreateTaskInput`;
    - `ListTasks`;
    - `GetTask`.
  - Internal semantic mutations:
    - `BootstrapMutation`, containing candidate entity identifiers,
      authoritative timestamp, request ID, Project key, and idempotency key;
    - `TaskCreationMutation`, containing the validated Task fields, allocated
      candidate identifiers, authoritative timestamp, actor, request ID, and
      idempotency key.
  - Results:
    - `BootstrapResult`;
    - `StatusResult`;
    - `TaskPage`;
    - domain `Project` and `Task` entities for single-record results.
  - Ports:

    ```python
    class Clock(Protocol):
        def now(self) -> datetime: ...

    class IdentifierFactory(Protocol):
        def new_instance_id(self) -> InstanceId: ...
        def new_project_id(self) -> ProjectId: ...
        def new_subject_id(self) -> SubjectId: ...
        def new_task_id(self) -> TaskId: ...
        def new_event_id(self) -> TaskEventId: ...
        def new_request_id(self) -> RequestId: ...

    class PhaseOneRepository(Protocol):
        def bootstrap_local_project(
            self,
            mutation: BootstrapMutation,
        ) -> BootstrapResult: ...
        def create_task(self, mutation: TaskCreationMutation) -> Task: ...
        def get_local_status(self, command: GetLocalStatus) -> StatusResult: ...
        def list_projects(self, command: ListProjects) -> tuple[Project, ...]: ...
        def list_tasks(self, command: ListTasks) -> TaskPage: ...
        def get_task(self, command: GetTask) -> Task: ...
    ```

  - Typed application failures carry a stable error code, safe Human message,
    retryability, and exit category without exposing a driver exception.
- Inputs:
  - Domain models from Task 2.
  - Command and error contract from Task 1.
- Outputs:
  - Runtime-validated boundaries for adapters, Sessions, and CLI presentation.
  - A semantic persistence port owned below the concrete adapter boundary.
- Tests:
  - Reject extra fields, invalid cursor/page sizes, malformed idempotency keys,
    invalid priorities, blank titles, and ambiguous identifiers.
  - Assert every application failure maps to a documented Phase 1 error.
- Acceptance criteria:
  - `workaholic.application` imports only domain and declared runtime
    dependencies, never `workaholic.persistence`, `sqlite3`, Typer, or context
    code.

### Task 4: Implement local data paths and exact-directory context

- Deliverables:
  - `pyproject.toml`
  - `uv.lock`
  - `.env.example`
  - `src/workaholic/context/models.py`
  - `src/workaholic/context/local.py`
  - `src/workaholic/context/paths.py`
  - `src/workaholic/context/errors.py`
  - `src/workaholic/context/__init__.py`
  - `tests/unit/context/test_local_context.py`
  - `tests/unit/context/test_paths.py`
  - `tests/unit/test_package_metadata.py`
- Description: Use `platformdirs` to select the user data directory and
  implement the narrow Phase 1 context boundary. `workaholic up` writes one
  strict `.workaholic.env`; other commands inspect only `cwd/.workaholic.env`.
  The parser treats repository content as hostile data, never sources a shell,
  and never accepts credentials, endpoints, storage URLs, or unknown keys.
- Public interface changes:
  - Trusted process override: `WORKAHOLIC_DATA_DIR`. It must resolve to an
    absolute directory after user expansion and is documented in
    `.env.example`. It exists for isolated development, testing, and managed
    local runtimes.
  - Default SQLite path:
    `platformdirs.user_data_path("workaholic", "workaholic-ai") / "local.db"`.
  - Context file fields:

    ```dotenv
    WORKAHOLIC_CONTEXT_VERSION=1
    WORKAHOLIC_PROFILE=local
    WORKAHOLIC_INSTANCE_ID=ins_...
    WORKAHOLIC_PROJECT_ID=prj_...
    WORKAHOLIC_PROJECT_KEY=ACME
    WORKAHOLIC_WORKSPACE_ROOT=.
    ```

  - The parsed logical model is the dependency-free domain
    `WorkspaceBinding`; context code owns text and filesystem translation.
  - Interfaces:

    ```python
    def resolve_local_data_paths(
        environment: Mapping[str, str],
    ) -> LocalDataPaths: ...

    def read_current_workspace_context(directory: Path) -> WorkspaceBinding: ...

    def write_current_workspace_context(
        directory: Path,
        context: WorkspaceBinding,
    ) -> Path: ...

    def exclude_context_from_git(directory: Path) -> None: ...
    ```

- Inputs:
  - Accepted exact-directory and trusted-profile deferrals from Task 1.
- Outputs:
  - Deterministic local database and Workspace context locations.
  - Atomically written UTF-8 context with LF endings and no secret material.
- Tests:
  - Cover missing files, unknown and duplicate keys, malformed lines, command
    substitution text, unsupported versions, inconsistent identifiers,
    symlinks, relative and absolute data overrides, and unwritable targets.
  - Assert a context in a parent directory is not discovered in Phase 1.
  - Assert `.workaholic.env` is added only to a conventional local
    `.git/info/exclude`; non-Git directories remain valid and shared
    `.gitignore` is never modified.
- Acceptance criteria:
  - Parsing or rejecting a context file never executes its content or contacts
    a network endpoint.

### Task 5: Create and validate the Phase 1 SQLite schema

- Deliverables:
  - `src/workaholic/persistence/sqlite/__init__.py`
  - `src/workaholic/persistence/sqlite/connection.py`
  - `src/workaholic/persistence/sqlite/schema.py`
  - `src/workaholic/persistence/sqlite/errors.py`
  - `src/workaholic/persistence/__init__.py`
  - `tests/integration/persistence/test_sqlite_schema.py`
- Description: Implement short-lived SQLite connection management, atomic
  empty-store initialization, and exact schema-version validation. Use the
  standard-library `sqlite3` driver, enable foreign keys on every connection,
  use bounded busy handling, and place each semantic write in an explicit
  transaction. Do not add a migration framework or generic CRUD abstraction.
- Public interface changes:
  - Internal schema version: integer `1`.
  - Physical tables:
    - `store_metadata` (`singleton`, `schema_version`);
    - `instances` (`id`, `created_at`);
    - `subjects` (`id`, `kind`, `display_name`, `enabled`,
      `is_instance_admin`);
    - `projects` (`id`, `instance_id`, `key`, `next_task_number`,
      `created_at`);
    - `project_grants` (`subject_id`, `project_id`, `role`);
    - `tasks` (`uid`, `project_id`, `number`, `key`, `title`, `objective`,
      `state`, `priority`, `version`, `created_by`, `created_at`, `updated_at`);
    - `task_events` (`cursor`, `id`, `task_uid`, `project_id`,
      `actor_subject_id`, `request_id`, `event_type`, `occurred_at`,
      `payload_json`);
    - `idempotency_records` (`subject_scope`, `operation`, `caller_key`,
      `request_fingerprint`, `outcome_json`, `created_at`).
  - Internal adapter interfaces:

    ```python
    def initialize_empty_store(database_path: Path) -> None: ...

    def validate_store_schema(connection: sqlite3.Connection) -> None: ...

    @contextmanager
    def open_read_connection(database_path: Path) -> Iterator[sqlite3.Connection]:
        ...

    @contextmanager
    def open_write_transaction(
        database_path: Path,
    ) -> Iterator[sqlite3.Connection]:
        ...
    ```

  - Timestamps persist as canonical RFC 3339 UTC text.
  - Boolean and enum values use checked constraints.
  - Project key, task number, human key, event identity/cursor, and idempotency
    scope have database uniqueness constraints in addition to application
    validation.
- Inputs:
  - Domain fields from Task 2.
  - Persistence semantics and application port from Tasks 1 and 3.
- Outputs:
  - A clean SQLite database that is either fully initialized at schema version
    1 or not presented as initialized.
- Tests:
  - Cover empty initialization, reopening, concurrent first open, foreign-key
    enforcement, database constraints, and lock timeout mapping.
  - Construct missing, malformed, version `0`, and version `2` stores and prove
    validation fails without changing any byte or schema object.
  - Inject initialization failure and assert no partially valid Instance is
    exposed.
- Acceptance criteria:
  - Normal reads do not create or repair a database.
  - Unsupported schema versions are never interpreted or migrated.

### Task 6: Implement idempotent local bootstrap

- Deliverables:
  - `src/workaholic/application/bootstrap.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/application/test_bootstrap.py`
  - `tests/integration/persistence/test_sqlite_bootstrap.py`
- Description: Implement the semantic bootstrap operation used by
  `workaholic up`. One write transaction creates or locates the single local
  Instance, deterministic local Human Subject, Instance-administrator flag,
  Project, Owner grant, and durable idempotency outcome. The bootstrap display
  name is `Local operator`; no Token or credential is created in Phase 1.
- Public interface changes:
  - Application service:

    ```python
    class BootstrapApplication:
        def __init__(
            self,
            repository: PhaseOneRepository,
            clock: Clock,
            identifiers: IdentifierFactory,
        ) -> None: ...

        def up(self, command: BootstrapLocalProjectInput) -> BootstrapResult: ...
    ```

  - Repeating `up` for the existing Project key returns the existing logical
    bootstrap result without duplicating entities.
  - Reusing an idempotency key with the same semantic input returns the
    recorded result; reuse with a different Project key returns
    `IDEMPOTENCY_CONFLICT`.
  - A second distinct Project key returns `PROJECT_KEY_CONFLICT` in the
    single-project Phase 1 runtime.
- Inputs:
  - Validated application models and SQLite schema from Tasks 3 and 5.
- Outputs:
  - One attributable local identity and Owner grant available to later Task
    operations.
- Tests:
  - Cover first bootstrap, safe retry, conflicting retry, Project-key conflict,
    transaction rollback, exact timestamps, and restart persistence.
  - Assert no Token, secret, or TaskEvent is created by bootstrap.
  - Assert disabled or non-Owner Subjects cannot be selected for writes.
- Acceptance criteria:
  - A successful bootstrap never leaves duplicate Instances, Projects,
    Subjects, grants, or idempotency records.

### Task 7: Implement atomic and idempotent Task creation

- Deliverables:
  - `src/workaholic/application/tasks.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/application/test_create_task.py`
  - `tests/integration/persistence/test_sqlite_create_task.py`
- Description: Implement the first Task mutation as one semantic transaction.
  Validate the active Subject and Owner grant, allocate the next Project task
  number, create a globally unique Task UID and stable human key, create
  optimistic version 1, append `task_created`, and record the idempotency
  outcome before commit.
- Public interface changes:

  ```python
  class TaskApplication:
      def create(self, command: CreateTaskInput) -> Task: ...
  ```

  - Missing `objective` defaults to the normalized title.
  - Missing `priority` defaults to `50`.
  - The `task_created` payload contains the stable Task fields needed to
    explain the initial state, but no secret or backend field.
  - Replaying identical input with one idempotency key returns the original
    Task and emits no additional TaskEvent.
- Inputs:
  - Bootstrap identity and Project from Task 6.
- Outputs:
  - Atomically committed Task, event, task-number allocation, and optional
    idempotency record.
- Tests:
  - Cover field defaults and bounds, authorization, event attribution, request
    identity, timestamp consistency, idempotent replay, conflicting key reuse,
    and rollback after injected failure.
  - Create Tasks concurrently from separate SQLite connections and prove each
    successful Task receives a distinct monotonically increasing number and
    stable key.
  - Assert failed creates commit neither a Task nor a task-number increment;
    consumers must nevertheless continue treating gaps as valid.
- Acceptance criteria:
  - No observer can read a Task without its corresponding `task_created` event
    or an event without its Task.

### Task 8: Implement status, Project, and Task queries

- Deliverables:
  - `src/workaholic/application/queries.py`
  - `src/workaholic/persistence/sqlite/repository.py`
  - `tests/unit/application/test_queries.py`
  - `tests/integration/persistence/test_sqlite_queries.py`
- Description: Implement read-only application queries for local status,
  authorized Projects, Task lookup, and deterministic Task pagination. Queries
  return domain or result models and map expected absence to typed application
  errors; they never expose SQLite rows or driver exceptions.
- Public interface changes:

  ```python
  class QueryApplication:
      def status(self, command: GetLocalStatus) -> StatusResult: ...
      def list_projects(self, command: ListProjects) -> tuple[Project, ...]: ...
      def list_tasks(self, command: ListTasks) -> TaskPage: ...
      def get_task(self, command: GetTask) -> Task: ...
  ```

  - `get_task` accepts either the exact Task UID or stable human key.
  - Task pages are ordered by task number ascending.
  - Cursors are opaque, versioned, validated, and bound to the selected
    Project; malformed or cross-Project cursors return `INVALID_INPUT`.
  - `status` reports local mode and schema version without leaking SQLite SQL
    or unsafe filesystem details.
- Inputs:
  - Persisted bootstrap and Tasks from Tasks 6 and 7.
- Outputs:
  - Stable read models suitable for both Human and JSON presentation.
- Tests:
  - Cover lookup by UID and human key, not-found behavior, Project isolation,
    disabled Subject behavior, empty and multi-page lists, stable ordering, and
    malformed cursors.
  - Snapshot database bytes or logical row counts before and after every query
    to prove reads do not mutate state.
- Acceptance criteria:
  - Reopening the database in a later process returns equivalent query results.

### Task 9: Implement the transport-neutral Session and LocalSession

- Deliverables:
  - `src/workaholic/session/models.py`
  - `src/workaholic/session/base.py`
  - `src/workaholic/session/local.py`
  - `src/workaholic/session/__init__.py`
  - `tests/unit/session/test_local_session.py`
- Description: Define the Phase 1 subset of `WorkaholicSession` and implement
  `LocalSession` as a thin adapter over application services. It receives its
  dependencies explicitly and must not construct SQLite, inspect process
  environment, or parse CLI arguments. It always supplies the selected Human
  Subject and verifies context identity against authoritative state.
- Public interface changes:

  ```python
  class WorkspaceContextGateway(Protocol):
      def read_current(self) -> WorkspaceBinding: ...
      def write_current(self, binding: WorkspaceBinding) -> Path: ...
  ```

  ```python
  class WorkaholicSession(Protocol):
      def up(self, command: UpRequest) -> BootstrapResult: ...
      def status(self, request: StatusRequest) -> StatusResult: ...
      def list_projects(self, request: ProjectListRequest) -> tuple[Project, ...]: ...
      def create_task(self, request: TaskCreateRequest) -> Task: ...
      def list_tasks(self, request: TaskListRequest) -> TaskPage: ...
      def get_task(self, request: TaskGetRequest) -> Task: ...
  ```

  - `LocalSession.up` writes Workspace context only after durable bootstrap
    succeeds. If context writing then fails, retrying `up` safely completes the
    binding without duplicating database state.
  - Non-bootstrap methods require exact-directory context and return
    `CONTEXT_NOT_FOUND` or `CONTEXT_INVALID` before application invocation.
  - Phase 1 selects the one enabled local bootstrap Human without a Token.
  - `LocalSession` receives `WorkspaceContextGateway` and application services
    through its constructor; it does not import `workaholic.context` or
    `workaholic.persistence`.
- Inputs:
  - Application services from Tasks 6-8.
  - Context interfaces from Task 4.
- Outputs:
  - One presentation-independent local boundary usable by CLI and future UI.
- Tests:
  - Use explicit fakes for ports and real validated models to verify delegation,
    context consistency, error propagation, authorization context, and
    bootstrap/context failure ordering.
  - Assert Session imports no Typer, CLI, server, protocol, or remote client
    module.
- Acceptance criteria:
  - CLI work can proceed without importing application or persistence
    implementation modules.

### Task 10: Implement CLI JSON envelopes, options, and error mapping

- Deliverables:
  - `src/workaholic/cli/options.py`
  - `src/workaholic/cli/envelopes.py`
  - `src/workaholic/cli/errors.py`
  - `src/workaholic/cli/rendering.py`
  - `tests/unit/cli/test_envelopes.py`
  - `tests/unit/cli/test_error_mapping.py`
- Description: Implement reusable, explicit CLI boundary helpers before
  commands use them. Pydantic response models validate serialization. Avoid
  decorators that alter command signatures and avoid global mutable Session
  state. Human rendering remains intentionally non-contractual but must be
  deterministic and readable.
- Public interface changes:
  - `JsonSuccess`, `JsonError`, and `JsonErrorDetail` Pydantic models.
  - Typed aliases for `--json`, `--non-interactive`, `--idempotency-key`,
    `--cursor`, and `--limit`.
  - `write_success(data, *, json_mode)` emits exactly one UTF-8 JSON value and
    one newline in JSON mode.
  - `write_failure(error, *, json_mode)` emits a documented error envelope and
    exits with the mapped status.
  - JSON serialization uses RFC 3339 `Z` timestamps, rejects non-finite
    numbers, excludes no required fields, and never prints diagnostics on
    stdout.
- Inputs:
  - Application failures and command schemas from Tasks 1 and 3.
- Outputs:
  - One tested presentation boundary shared by all Phase 1 commands.
- Tests:
  - Cover exact success and error envelopes, one trailing newline, stdout/stderr
    separation, non-ASCII content, timestamp serialization, redaction, unknown
    errors, retryability, and every exit category.
  - Assert `--non-interactive` helpers never read stdin or inspect terminal
    state unless a command explicitly requests input.
- Acceptance criteria:
  - The existing golden JSON assertion helpers accept every successful Phase 1
    envelope without special cases.

### Task 11: Expose bootstrap, status, and Project commands

- Deliverables:
  - `src/workaholic/cli/main.py`
  - `src/workaholic/cli/up.py`
  - `src/workaholic/cli/status.py`
  - `src/workaholic/cli/project.py`
  - `tests/unit/cli/test_up.py`
  - `tests/unit/cli/test_status.py`
  - `tests/unit/cli/test_project.py`
  - `README.md`
- Description: Add the first public command groups using only the
  `WorkaholicSession` interface and CLI presentation helpers. Dependency
  acquisition is injected through an explicit Session provider; command
  modules never import context, application, persistence, or SQLite.
- Public interface changes:
  - `workaholic up --project-key KEY`;
  - `workaholic status`;
  - `workaholic project list`;
  - JSON, non-interactive, and idempotency options documented by Task 1.
- Inputs:
  - Session boundary from Task 9.
  - CLI presentation boundary from Task 10.
- Outputs:
  - Fully parsed commands testable against an injected Session.
  - Updated README command/status content in the same change.
- Tests:
  - Exercise Human and JSON success, every documented error, help, invalid
    Project keys, missing context, non-interactive behavior, and Session call
    arguments.
  - Prove command imports retain the existing lightweight startup and
    CLI-to-Session dependency contracts.
- Acceptance criteria:
  - No command starts a daemon, creates state before `up`, or bypasses Session.

### Task 12: Expose Task add, list, and show commands

- Deliverables:
  - `src/workaholic/cli/main.py`
  - `src/workaholic/cli/task.py`
  - `tests/unit/cli/test_task_add.py`
  - `tests/unit/cli/test_task_list.py`
  - `tests/unit/cli/test_task_show.py`
  - `README.md`
- Description: Expose the Phase 1 Task vertical slice through the same injected
  Session boundary. Keep command signatures explicit and delegate all
  validation with domain meaning to application models rather than duplicating
  it in Typer callbacks.
- Public interface changes:
  - `workaholic task add TITLE [--objective TEXT] [--priority INTEGER]`;
  - `workaholic task list [--cursor CURSOR] [--limit INTEGER]`;
  - `workaholic task show TASK`;
  - all commands accept `--json` and `--non-interactive`;
  - `task add` accepts `--idempotency-key KEY`.
- Inputs:
  - Task Session methods from Task 9.
  - CLI helpers from Task 10.
- Outputs:
  - Human-readable Task summaries and contract-complete JSON Task records.
  - Updated README quick-start and command inventory.
- Tests:
  - Cover defaults, Unicode input, field bounds, priority limits, stable key
    and UID lookup, empty lists, pagination, missing Tasks, conflicts, and
    absence of prompts.
  - Assert JSON mode never leaks Human tables, tracebacks, SQL, database paths,
    or diagnostics to stdout.
- Acceptance criteria:
  - All documented Phase 1 Task operations are reachable only through
    `WorkaholicSession`.

### Task 13: Wire the explicit local composition root

- Deliverables:
  - `src/workaholic/composition.py`
  - `src/workaholic/__main__.py`
  - `pyproject.toml`
  - `tests/contract/test_import_boundaries.py`
  - `tests/contract/test_cli_import_weight.py`
  - `tests/integration/cli/test_local_cli.py`
  - `tests/unit/test_package_metadata.py`
- Description: Add one explicit composition root that constructs paths,
  context access, SQLite repository, clocks, UUID7-based prefixed identifier
  factories, application services, LocalSession, and the Typer application.
  Change the console and module entry points to this root without changing the
  executable name. Do not use dynamic imports, service locators, or reflection.
- Public interface changes:

  ```python
  def create_local_session(
      *,
      cwd: Path,
      environment: Mapping[str, str],
  ) -> WorkaholicSession: ...

  def main() -> None:
      """Compose and run the Workaholic command-line application."""
  ```

  - Console entry point remains `workaholic`.
  - `python -m workaholic` uses the same composition root.
  - Identifier strings are opaque, globally unique, prefixed with `ins_`,
    `prj_`, `sub_`, `tsk_`, `evt_`, or `req_`, and generated from Python 3.14
    UUID7 values.
- Inputs:
  - Real implementations from Tasks 4-12.
- Outputs:
  - A real source-checkout CLI connected end to end to SQLite.
- Tests:
  - Run separate subprocesses with an isolated `WORKAHOLIC_DATA_DIR` and
    Workspace for `up`, status, Project list, Task add/list/show, and
    idempotent retry.
  - Extend import-linter layers with the explicit composition root and prove
    CLI modules still cannot import application or persistence directly.
  - Prove normal local startup imports no server, PostgreSQL, scheduler, or
    remote-client dependency.
- Acceptance criteria:
  - The composition root is the only production location that knows all local
    concrete implementations.

### Task 14: Add reusable Phase 1 persistence and Session conformance suites

- Deliverables:
  - `tests/contract/test_phase_one_persistence.py`
  - `tests/contract/test_phase_one_session.py`
  - `tests/contract/phase_one.py`
  - `tests/integration/persistence/test_sqlite_concurrency.py`
- Description: Consolidate shared observable behavior into reusable contract
  suites so later JSON, PostgreSQL, and RemoteSession implementations inherit
  Phase 1 semantics rather than reimplementing SQLite-specific tests. Tests
  assert domain outcomes and public errors, not physical SQL or private method
  calls.
- Public interface changes:
  - Typed test factories:

    ```python
    class PhaseOneRepositoryFactory(Protocol):
        def create(self, root: Path) -> PhaseOneRepository: ...

    class PhaseOneSessionFactory(Protocol):
        def create(self, root: Path, workspace: Path) -> WorkaholicSession: ...
    ```

- Inputs:
  - Real SQLite repository and LocalSession from Tasks 5-13.
- Outputs:
  - A parameterizable baseline for every future persistence and Session
    implementation.
- Tests:
  - Cover schema initialization and rejection, bootstrap, Project uniqueness,
    authorization, task allocation, UID/key stability, initial optimistic version,
    event atomicity and attribution, idempotent replay/conflict, deterministic
    pagination, restart persistence, concurrent creates, and rollback.
  - Run real concurrent writes through separate connections; do not replace
    concurrency with mocks.
- Acceptance criteria:
  - SQLite and LocalSession pass the complete suite with no adapter-specific
    behavioral exception.

### Task 15: Enable the persistent solo golden journey

- Deliverables:
  - `tests/conftest.py`
  - `tests/golden.py`
  - `tests/e2e/golden/test_solo_journey.py`
  - `tests/e2e/golden/README.md`
  - `tests/unit/test_golden_journey_inventory.py`
- Description: Implement the real subprocess portion of
  `GoldenJourneyRunner`, remove only the Phase 1 solo skip, and run its
  operations through the supported CLI. The harness owns an isolated data
  directory and Workspace and invokes a fresh process for every command.
  Future remote, backend-parity, and registry methods remain explicit
  unsupported harness operations while their tests stay phase-skipped.
- Public interface changes:
  - The solo golden journey changes from skipped to required.
  - Golden runner CLI processes receive only documented trusted environment
    overrides and never use the developer's real user data.
- Inputs:
  - End-to-end local composition from Task 13.
- Outputs:
  - Executable evidence that a Task survives terminal/process closure.
- Tests:
  - Run `up`, Task add, Task list, and Task show from separate processes.
  - Assert exact JSON envelope, `ACME-1`, persisted title/objective/defaults,
    stable UID and key, creator attribution, and no duplicate after retry.
  - Keep the other five golden journeys skipped with their original phase
    reasons.
- Acceptance criteria:
  - `uv run pytest -m golden` passes the solo journey and reports exactly five
    future-phase skips.

### Task 16: Publish Phase 1 documentation and alpha metadata

- Deliverables:
  - `README.md`
  - `CHANGELOG.md`
  - `pyproject.toml`
  - `src/workaholic/__init__.py`
  - `docs/architecture.md`
  - `docs/cli-contract.md`
  - `docs/persistence-contract.md`
  - `tests/unit/docs/test_public_documentation.py`
  - `tests/unit/test_package_metadata.py`
- Description: Replace Phase 0's CLI-skeleton notice with the verified local
  Task workflow, document disposable alpha storage and reset expectations, and
  set internal pre-release metadata to `0.1.0a1`. Documentation must distinguish
  implemented Phase 1 behavior from later multi-project, Agent, identity,
  server, and backend features.
- Public interface changes:
  - Package version: `0.1.0a1`.
  - README quick start:

    ```bash
    uv sync --frozen
    uv run workaholic up --project-key ACME
    uv run workaholic task add "First persistent task"
    uv run workaholic task list
    ```

  - Document `WORKAHOLIC_DATA_DIR` for isolated development and testing.
  - Document that Phase 1 stores are disposable and unsupported schema versions
    require reset rather than migration.
- Inputs:
  - Verified command behavior and golden journey from Tasks 11-15.
- Outputs:
  - Public documentation that matches the first useful product increment.
  - Build metadata suitable for a `0.1.0a1` artifact.
- Tests:
  - Execute and assert the documented quick start in an isolated directory.
  - Assert version output, distribution metadata, README status, command
    inventory, and implementation notices agree.
  - Assert documentation does not claim upward discovery, multiple active
    Projects, Tokens, Agents, RemoteSession, JSON/PostgreSQL adapters, or schema
    migration exists.
- Acceptance criteria:
  - A new developer can complete the local Task journey without consulting an
    internal document.

### Task 17: Execute the Phase 1 clean-state acceptance gate

- Deliverables:
  - `scripts/verify-phase-1.sh`
  - `scripts/smoke-phase-1-wheel.sh`
  - `tests/e2e/test_phase_1_distribution.py`
  - `.pre-commit-config.yaml`
  - `README.md`
  - `CHANGELOG.md`
  - Phase 1 GitHub epic and implementation issues
- Description: Add one fail-fast Phase 1 acceptance command and execute it from
  a fresh clone with an empty temporary user-data directory and Workspace. The
  gate orchestrates existing commands without duplicating lint, test, build, or
  schema configuration. It validates both the source checkout and an installed
  wheel without touching the operator's actual profile or database.
- Public interface changes:
  - Acceptance command: `scripts/verify-phase-1.sh`.
  - Required clean-state journey:

    ```bash
    uv sync --frozen
    uv run pre-commit run --all-files
    uv run pytest
    uv build
    scripts/smoke-install.sh dist/*.whl
    scripts/smoke-phase-1-wheel.sh dist/*.whl
    ```

  - The wheel smoke script creates its own temporary virtual environment,
    `WORKAHOLIC_DATA_DIR`, and Workspace; runs `up`, Task add/list/show in fresh
    processes; and removes the temporary state on exit.
- Inputs:
  - All Phase 1 implementation and documentation from Tasks 1-16.
- Outputs:
  - Reproducible evidence for the Local Alpha exit gate.
  - A closed Phase 1 epic and milestone only after green protected `main`.
- Tests:
  - Run the aggregate gate and README journey independently in separate fresh
    clones.
  - Prove malformed context, unsupported schema, idempotency conflict, failing
    test, and malformed wheel each fail the appropriate boundary.
  - Assert the gate rejects an active virtual environment, pre-existing local
    build output, or a non-clean checkout.
  - Verify wheel execution never imports or writes source-checkout state.
- Acceptance criteria:
  - Required `quality`, `tests`, `build`, and `wheel-smoke` checks pass on the
    merge commit on `main`.
  - The solo golden journey passes; exactly five later journeys remain skipped.
  - Source and wheel workflows persist the same Task across separate CLI
    processes.
  - Evidence is linked from the Phase 1 epic before closing milestone
    `1 - Local Alpha`.

## Operational instructions

1. Before Task 1 begins, create one Phase 1 epic and issues for Tasks 1-17 in
   GitHub milestone `1 - Local Alpha`. Copy each task's acceptance criteria into
   its issue and preserve task order through explicit dependency links.
2. Implement and merge Tasks 1-17 in order. Tasks may be developed in parallel
   only when they modify disjoint deliverables and all shared inputs have
   already merged.
3. Every developer installs and runs the repository's existing hook stages:

   ```bash
   uv sync --frozen
   uv run pre-commit install --hook-type pre-commit --hook-type pre-push
   uv run pre-commit run --all-files
   uv run pytest
   uv build
   ```

4. Use `WORKAHOLIC_DATA_DIR` pointing to a test-owned temporary directory for
   every manual, integration, and acceptance run. Never test against the
   operator's default local database.
5. Phase 1 introduces schema version `1` into previously empty application
   storage. There is no upgrade or migration from Phase 0 because Phase 0
   persisted no application data.
6. Pre-release Phase 1 stores are disposable. If schema version `1` changes
   during development, delete only the explicitly selected test/development
   data directory after confirming its path; never implement a silent
   migration or broad recursive cleanup.
7. Every pull request that changes commands, output, prerequisites, storage
   behavior, environment variables, support status, or the primary journey
   updates README and contract documentation in the same change.
8. The `0.1.0a1` version is pre-release metadata, not a compatibility promise.
   Do not upload to PyPI or create a GitHub release without a separate explicit
   owner authorization. Built artifacts remain CI and acceptance evidence.
9. After Task 17 merges, rerun the gate from the protected `main` commit,
   attach the clean-state and CI evidence to the Phase 1 epic, and only then
   close the epic and milestone.
