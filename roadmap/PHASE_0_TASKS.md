# Phase 0 Implementation Tasks

## Purpose

Deliver a reproducible, buildable, and testable foundation for Workaholic AI. At
the completion of these tasks, a clean checkout must expose a minimal
`workaholic` command, pass local and continuous-integration quality gates, build
installable distributions, and contain the product and architecture contracts
needed for later phases.

Tasks are ordered by dependency. Each task is intended to be implemented and
reviewed independently.

## Repository state at planning time

The following deliverables already exist and must be preserved:

- `ARCHITECTURE.md`: detailed v1 architecture.
- `ROADMAP.md`: phased v1 delivery roadmap.
- `AGENTS.md` and `CLAUDE.md`: coding-agent instructions.
- `.gitignore`: ignores the current local development artifacts.

No Python package, lockfile, tests, CI workflows, pre-commit configuration,
public README, community files, or GitHub project configuration exists yet.

## Confirmed product decisions

Implementation and documentation must consistently encode these owner-approved
decisions:

- V1 requires both local agent execution and distributed team coordination.
  Local agent execution is delivered first, and distributed team coordination
  remains a v1 release requirement.
- Breaking persisted-schema and automation-contract changes are allowed through
  Phase 7. Contracts freeze at the end of Phase 8, remain unchanged through the
  release candidate, and receive a formal backward-compatibility promise at
  `1.0.0`.
- V1 supports a single organization per instance. The instance administrator
  and deployment infrastructure are trusted. Humans and agents are constrained
  by project roles and must be treated as potentially compromised.
  Cross-organization isolation and public multi-tenancy are out of scope.

### Task 1: Establish project metadata and foundation decisions

- Deliverables:
  - `docs/product-scope.md`
  - `docs/compatibility-policy.md`
  - `docs/adr/0000-adr-template.md`
  - `docs/adr/0001-package-and-executable-naming.md`
  - `LICENSE`
  - `.python-version`
- Description: Record the immutable inputs needed by packaging, CI, public
  documentation, and later architecture records. Use the proposed identifiers
  (`Workaholic AI`, `workaholic-ai`, and `workaholic`) unless the owner rejects
  them before this task merges. Obtain and record the owner's
  explicit open-source license choice rather than selecting a license by
  inference. Record the minimum development Python version and the Phase 0 CI
  Python versions; the final public support matrix remains a later release
  decision.
- Public interface changes:
  - Distribution name: `workaholic-ai`.
  - Import package: `workaholic`.
  - Console command: `workaholic`.
  - Initial internal version: `0.0.0`.
  - ADR status values: `Proposed`, `Accepted`, `Superseded`, and `Rejected`.
  - Compatibility policy: no compatibility guarantee before `1.0.0`; schema
    and contract freeze occurs at the end of Phase 8.
- Inputs:
  - Owner-approved license, code-owner identity, security contact, and Python
    baseline.
  - The confirmed product decisions in this document.
- Outputs:
  - Unambiguous package metadata for Task 2.
  - An accepted naming ADR and documented pre-1.0 compatibility policy.
  - A real license file containing the canonical license text and copyright
    holder, with no placeholder fields.
- Tests:
  - Validate the selected SPDX license identifier when package metadata is
    introduced.
  - Verify `.python-version` is compatible with the recorded minimum Python
    version.
- Acceptance criteria:
  - No undecided placeholder such as `TBD`, `<owner>`, or `<email>` remains.
  - Product scope explicitly distinguishes mandatory v1 outcomes from
    post-v1 backlog.

### Task 2: Bootstrap the Python package and minimal CLI

- Deliverables:
  - `pyproject.toml`
  - `uv.lock`
  - `src/workaholic/__init__.py`
  - `src/workaholic/__main__.py`
  - `src/workaholic/cli/__init__.py`
  - `src/workaholic/cli/main.py`
  - `src/workaholic/domain/__init__.py`
  - `src/workaholic/application/__init__.py`
  - `src/workaholic/session/__init__.py`
  - `src/workaholic/context/__init__.py`
  - `src/workaholic/auth/__init__.py`
  - `src/workaholic/persistence/__init__.py`
  - `src/workaholic/protocol/__init__.py`
  - `src/workaholic/client/__init__.py`
  - `src/workaholic/server/__init__.py`
  - `tests/unit/cli/test_version.py`
  - `tests/unit/test_package_metadata.py`
- Description: Create a `src`-layout Python distribution and the smallest
  runnable CLI. Use a well-supported typed CLI library, configure a PEP 517
  build backend, and lock all runtime and development dependencies with `uv`.
  Placeholder packages expose dependency boundaries but contain no speculative
  domain implementation. Every maintained Python module must have an up-to-date
  module docstring.
- Public interface changes:
  - Console entry point: `workaholic = workaholic.cli.main:main`.
  - Module entry point: `python -m workaholic`.
  - Function:

    ```python
    def main() -> None:
        """Run the Workaholic command-line application."""
    ```

  - `workaholic --version` writes `workaholic 0.0.0` followed by one newline to
    stdout and exits with status `0`.
  - Invoking the CLI without a command prints help, does not prompt, and exits
    predictably.
- Inputs:
  - Package names, license identifier, and Python requirement fixed by Task 1.
- Outputs:
  - Buildable source and wheel distributions.
  - A runnable CLI with no database, network, or user-directory side effects.
- Tests:
  - Exercise both the console runner and `python -m workaholic`.
  - Assert exact version output, exit status, and absence of stderr.
  - Assert installed distribution metadata matches the naming ADR, license, and
    Python requirement.
  - Assert importing `workaholic` has no observable side effects.
- Acceptance criteria:
  - `uv sync`, `uv run workaholic --version`, `uv run pytest`, and `uv build`
    succeed locally.

### Task 3: Install local quality controls and pre-commit hooks

- Deliverables:
  - `.pre-commit-config.yaml`
  - `.markdownlint-cli2.yaml`
  - `.editorconfig`
  - `pyproject.toml`
  - `uv.lock`
  - `scripts/check.sh`
- Description: Make formatting, linting, strict type checking, tests, and common
  repository hygiene checks executable before feature development continues.
  Keep tool configuration in `pyproject.toml` where supported and pin every
  pre-commit hook. The local aggregate script must invoke the same commands CI
  will invoke; it must not contain a second copy of lint or test configuration.
- Public interface changes:
  - Developer command: `uv run pre-commit run --all-files`.
  - Aggregate quality command: `scripts/check.sh`.
  - Commit-stage hooks:
    - trailing-whitespace and end-of-file correction;
    - merge-conflict, case-conflict, private-key, TOML, YAML, and JSON checks;
    - Ruff formatting;
    - Ruff linting with import sorting and safe automatic fixes;
    - strict static type checking.
    - Markdown linting for the README, contracts, ADRs, and contributor
      documentation.
  - Pre-push hooks:
    - the complete pytest suite;
    - package build validation.
- Inputs:
  - The package and tests from Task 2.
- Outputs:
  - Deterministic local quality checks with no globally installed Python tools.
  - A hook installation documented as:

    ```bash
    uv run pre-commit install --hook-type pre-commit --hook-type pre-push
    ```

- Tests:
  - Add focused tests for any custom quality-check script behavior.
  - Run `uv run pre-commit run --all-files`.
  - Run `scripts/check.sh` from a clean working tree.
- Acceptance criteria:
  - The repository is Ruff-clean, formatted, and passes strict type checking.
  - Existing Markdown documents pass the configured project-wide rules before
    the README is added.
  - The test suite treats warnings as errors unless a narrowly documented
    exception is required.
  - Test coverage is measured from the start and fails below the threshold
    configured in `pyproject.toml`.
  - Hook revisions and Python dependencies are pinned and reproducible.

### Task 4: Publish the Phase 0 README and enforce README maintenance

- Deliverables:
  - `README.md`
  - `.github/pull_request_template.md`
  - `CONTRIBUTING.md`
- Description: Create the canonical public landing page and quick start. The
  README must describe what is available in the current repository, not present
  planned commands as implemented. It should remain concise and link to detailed
  documents rather than duplicating them. Add contribution and pull-request
  rules that make README review mandatory whenever installation, commands,
  output, requirements, support status, or the principal user journey changes.
- Public interface changes:
  - `README.md` must contain:
    - product name and one-paragraph value proposition;
    - prominent development-status and compatibility notices;
    - prerequisites;
    - a source-checkout quick start using `uv sync`,
      `uv run workaholic --version`, and `uv run pytest`;
    - the currently supported CLI surface;
    - a clearly labelled v1 direction without implying unimplemented features
      are available;
    - links to architecture, roadmap, security, contribution, license, and
      changelog documents.
  - `CONTRIBUTING.md` must document environment setup, pre-commit installation,
    test commands, docstring expectations, architecture boundaries, and the
    documentation-update policy.
  - The pull-request template must include explicit checkboxes for test impact,
    public interface impact, README/quick-start impact, security impact, and
    architecture-decision impact.
- Inputs:
  - The exact commands and distribution behavior implemented by Tasks 2 and 3.
- Outputs:
  - A new visitor can build, run, and test the current product without consulting
    internal planning documents.
  - Future feature tasks have an explicit README maintenance gate.
- Tests:
  - Execute every unqualified shell command in the README quick start on a clean
    checkout.
  - Run the configured Markdown linter against the README.
  - Verify all relative README links resolve.
  - Verify the documented `--version` output matches the installed artifact.
- Acceptance criteria:
  - Planned examples are labelled `Planned for v1` and are visually separate
    from the working quick start.
  - README changes are part of the definition of done for every user-visible
    pull request.

### Task 5: Canonicalize the documentation structure and threat model

- Deliverables:
  - `docs/architecture.md` moved from `ARCHITECTURE.md`
  - `docs/roadmap.md` moved from `ROADMAP.md`
  - `docs/glossary.md`
  - `docs/threat-model.md`
  - `README.md`
- Description: Move the existing architecture and roadmap into their canonical
  locations without keeping divergent copies. Create a shared glossary and the
  initial threat model. Update every repository-relative link after the moves.
  Do not alter accepted product behavior merely to shorten the documents.
- Public interface changes:
  - The glossary defines at least `Instance`, `Project`, `Workspace`, `Subject`,
    `Human`, `Agent`, `ProjectGrant`, `Task`, `Attempt`, `Lease`, `Result`,
    `TaskEvent`, `Session`, `LocalSession`, and `RemoteSession`.
  - The threat model defines:
    - trusted instance administrator and deployment infrastructure;
    - project-role isolation among human and agent subjects;
    - compromised-agent and stolen-token scenarios;
    - local filesystem and operating-system credential-store assumptions;
    - repository-controlled `.workaholic.env` as untrusted input;
    - token redirection, secret exposure, command injection, unauthorized
      attempt mutation, event forgery, and denial-of-service threats;
    - single-organization scope and the exclusion of public multi-tenancy.
- Inputs:
  - Existing architecture and roadmap documents.
  - The confirmed single-organization security decision.
- Outputs:
  - One canonical copy of each planning document.
  - Explicit trust boundaries and mitigations that later authentication,
    context, protocol, and persistence work can test.
- Tests:
  - Run the repository documentation-link checker.
  - Search for and reject stale root-level architecture/roadmap links.
  - Validate that terminology used by the architecture and roadmap is defined
    consistently in the glossary.
- Acceptance criteria:
  - No architecture or roadmap content is lost during the move.
  - The README links only to canonical document locations.

### Task 6: Record architecture decisions and delivery contracts

- Deliverables:
  - `docs/adr/0002-local-and-remote-sessions.md`
  - `docs/adr/0003-cli-json-automation-contract.md`
  - `docs/adr/0004-private-versioned-client-server-protocol.md`
  - `docs/adr/0005-semantic-persistence-interface.md`
  - `docs/adr/0006-project-context-trust-model.md`
  - `docs/adr/0007-human-and-agent-identity-model.md`
  - `docs/adr/0008-stable-task-key-allocation.md`
  - `docs/adr/0009-no-storage-migrations-in-v1.md`
  - `docs/adr/0010-single-process-single-instance-server.md`
  - `docs/cli-contract.md`
  - `docs/persistence-contract.md`
- Description: Convert the architecture's foundational decisions into accepted,
  reviewable ADRs and define the two semantic contracts needed by subsequent
  implementation. ADRs must state context, decision, alternatives considered,
  consequences, status, and date. Contract documents define observable behavior
  and invariants, not SQL tables, HTTP route internals, or speculative
  implementation helpers.
- Public interface changes:
  - CLI JSON envelope fields:
    - `schema`;
    - `ok`;
    - `data` on success;
    - `error.code`, `error.message`, and `error.retryable` on failure.
  - Automation rules:
    - JSON-only stdout in JSON mode;
    - diagnostics on stderr;
    - stable machine-readable errors after the contract freeze;
    - no prompts in non-interactive mode;
    - idempotency keys for mutations;
    - file/stdin support for large payloads.
  - Persistence contract:
    - semantic, transaction-scoped operations;
    - schema-version validation;
    - atomic task-number allocation and claim behavior;
    - optimistic version checks;
    - append-only event consistency;
    - idempotent mutation recording;
    - identical externally observable behavior across adapters.
- Inputs:
  - Canonical architecture, product scope, compatibility policy, glossary, and
    threat model.
- Outputs:
  - Ten accepted ADRs, including ADR 0001 from Task 1.
  - Contracts detailed enough to drive Phase 1 interfaces and contract tests.
- Tests:
  - Validate every ADR has all required headings and a valid status.
  - Validate that ADR numbers are unique and contiguous.
  - Check cross-document links and version identifiers.
- Acceptance criteria:
  - The documents consistently distinguish the public CLI JSON contract from
    the unsupported private network protocol.
  - The compatibility language matches the owner-approved Phase 8 freeze and
    `1.0.0` guarantee.

### Task 7: Add executable golden-journey specifications and test architecture

- Deliverables:
  - `tests/conftest.py`
  - `tests/unit/`
  - `tests/contract/`
  - `tests/integration/`
  - `tests/e2e/golden/test_solo_journey.py`
  - `tests/e2e/golden/test_multi_project_journey.py`
  - `tests/e2e/golden/test_agent_journey.py`
  - `tests/e2e/golden/test_team_journey.py`
  - `tests/e2e/golden/test_backend_conformance_journey.py`
  - `tests/e2e/golden/test_clean_install_journey.py`
  - `tests/e2e/golden/README.md`
  - `tests/unit/test_golden_journey_inventory.py`
  - `pyproject.toml`
- Description: Establish the permanent test taxonomy and express all six golden
  journeys as executable pytest specifications. Unimplemented journeys must be
  explicitly skipped with the phase and missing capability in the reason.
  Shared future-facing fixtures may define typed protocols, but must not contain
  fake production behavior that lets a journey pass prematurely.
- Public interface changes:
  - Pytest markers: `contract`, `integration`, `e2e`, `golden`, `requires_uv`,
    `requires_postgres`, and `requires_network`.
  - Each golden test name describes the user-observable outcome rather than an
    implementation detail.
  - The golden-test README maps every journey to its intended enabling phase and
    unskip conditions.
- Inputs:
  - CLI and persistence contracts from Task 6.
- Outputs:
  - Collectable specifications for solo, multi-project, agent, team, backend,
    and clean-install journeys.
  - A test layout future phases can extend without reorganizing existing tests.
- Tests:
  - The inventory test asserts exactly one canonical specification exists for
    every required journey.
  - Test collection fails for unknown or misspelled markers.
  - Skipped journeys expose a non-empty, phase-specific reason.
  - Existing Phase 0 tests continue to pass without counting skipped journeys as
    implemented coverage.
- Acceptance criteria:
  - `uv run pytest` collects all golden journeys and passes Phase 0 tests.
  - No golden journey is marked `xfail` in a way that can silently become
    `xpass`.

### Task 8: Enforce package dependency boundaries

- Deliverables:
  - `pyproject.toml`
  - `tests/contract/test_import_boundaries.py`
  - `tests/contract/test_cli_import_weight.py`
  - `docs/architecture.md`
- Description: Make the intended dependency direction machine-checkable before
  the placeholder packages acquire implementation. Use a maintained import
  contract tool where it provides clear enforcement and small explicit tests for
  startup-import constraints that it cannot express.
- Public interface changes:
  - `domain` must not import application, session, persistence, protocol, client,
    server, context, auth, CLI, or TUI packages.
  - `application` may depend on domain contracts but not CLI, server routes,
    concrete transports, or concrete persistence adapters.
  - CLI presentation code communicates through session interfaces rather than
    persistence adapters.
  - Importing the normal CLI path must not eagerly import server frameworks,
    PostgreSQL drivers, or server scheduling code.
- Inputs:
  - Placeholder package layout from Task 2 and dependency direction in the
    architecture.
- Outputs:
  - Fast contract tests that fail on prohibited imports.
  - Documented exceptions only at explicit adapter composition roots.
- Tests:
  - Include one test fixture or isolated sample proving each boundary rule can
    fail when violated.
  - Run boundary tests as part of the normal test suite and pre-push checks.
- Acceptance criteria:
  - Boundary failures identify both the prohibited importer and imported module.
  - No production package is exempted wholesale.

### Task 9: Add community, security, and repository-management files

- Deliverables:
  - `CODE_OF_CONDUCT.md`
  - `SECURITY.md`
  - `CHANGELOG.md`
  - `.github/CODEOWNERS`
  - `.github/ISSUE_TEMPLATE/bug.yml`
  - `.github/ISSUE_TEMPLATE/feature.yml`
  - `.github/ISSUE_TEMPLATE/architecture-decision.yml`
  - `.github/ISSUE_TEMPLATE/config.yml`
  - `.github/dependabot.yml`
  - `.github/pull_request_template.md`
  - `README.md`
- Description: Add the repository policies required for safe private
  development now and public operation later. Use real owner/security contacts
  from Task 1. Issue forms must request reproducible evidence and must never ask
  reporters to paste credentials. Dependabot must use bounded update frequency
  and grouping so dependency updates remain reviewable.
- Public interface changes:
  - `SECURITY.md` states supported versions, private reporting instructions,
    expected response process, and a prohibition on public disclosure of
    unpatched credentials or vulnerabilities.
  - `CHANGELOG.md` follows Keep a Changelog conventions and begins with an
    `Unreleased` section.
  - `CODEOWNERS` assigns defaults and tighter ownership for workflows, security,
    authentication, protocol, and persistence contracts.
  - Issue forms apply the repository's `area:*`, `kind:*`, `priority:*`, and
    `status:*` taxonomy where GitHub supports default labels.
- Inputs:
  - Owner, security contact, license, and repository identity from Task 1.
- Outputs:
  - Reviewable contribution, disclosure, ownership, and dependency-management
    paths.
- Tests:
  - Parse all YAML issue forms and Dependabot configuration.
  - Validate CODEOWNERS syntax.
  - Run documentation formatting and link checks.
- Acceptance criteria:
  - No placeholder contact or organization remains.
  - README links to all public policy files.

### Task 10: Implement least-privilege continuous integration

- Deliverables:
  - `.github/workflows/ci.yml`
  - `.github/dependabot.yml`
  - `.pre-commit-config.yaml`
  - `scripts/smoke-install.sh`
- Description: Implement required pull-request checks using `uv` and the locked
  dependency graph. CI must call the same quality commands developers run
  locally, then independently build and test the installable wheel. GitHub
  workflow permissions must be explicit and minimal. Third-party actions must be
  pinned to immutable commit SHAs and covered by Dependabot updates.
- Public interface changes:
  - Required CI checks:
    - `quality`: pre-commit, formatting, linting, and strict type checking;
    - `tests`: unit and collected specification tests;
    - `build`: source distribution and wheel creation;
    - `wheel-smoke`: install the wheel into a clean environment outside the
      checkout and run `workaholic --version`.
  - `scripts/smoke-install.sh <wheel-path>` creates an isolated temporary
    environment, installs exactly the supplied wheel, runs the CLI from outside
    the repository, verifies output, and cleans up on exit.
- Inputs:
  - Python versions recorded in Task 1.
  - Local checks from Task 3 and package behavior from Task 2.
- Outputs:
  - A CI workflow suitable for a required branch-protection ruleset.
  - Proof that tests do not depend on editable-install behavior.
- Tests:
  - Lint the workflow syntax.
  - Audit workflow permissions and pinned action references.
  - Run `scripts/smoke-install.sh` locally against the built wheel.
  - Confirm the smoke test fails for a missing, malformed, or wrong-version
    wheel path.
- Acceptance criteria:
  - Default workflow permissions are `contents: read`; no write permission or
    long-lived secret is configured.
  - CI uses `uv sync --frozen` and fails when `uv.lock` is stale.
  - CI uploads build artifacts for inspection but does not publish them.

### Task 11: Configure the GitHub operating model

- Deliverables:
  - GitHub organization repository `workaholic-ai/workaholic`
  - GitHub labels:
    - `area:domain`, `area:cli`, `area:context`, `area:auth`, `area:server`,
      `area:storage`, `area:release`, and `area:docs`;
    - `kind:feature`, `kind:bug`, `kind:refactor`, `kind:test`,
      `kind:security`, and `kind:decision`;
    - `priority:p0`, `priority:p1`, `priority:p2`, and `priority:p3`;
    - `status:blocked`, `status:needs-design`, and `status:ready`.
  - GitHub milestones `0 - Foundation` through `10 - v1 Release`
  - One Phase 0 epic issue
  - One implementation issue for each task in this document
  - `main` branch ruleset
- Description: Create or connect the public organization repository and encode
  the trunk-based development workflow. This task changes GitHub state and must
  be performed by an authorized repository owner. Copy each task into an
  independently assignable issue while preserving implementation order and
  acceptance criteria.
- Public interface changes:
  - Pull requests and passing required CI checks are mandatory for `main`.
  - Force pushes and branch deletion are blocked.
  - Review conversations must be resolved.
  - The Phase 0 epic tracks every implementation issue and the exit gate.
- Inputs:
  - CI check names from Task 10.
  - CODEOWNERS and issue forms from Task 9.
- Outputs:
  - A protected public repository with milestones, labels, and actionable
    implementation issues.
- Tests:
  - Open a temporary pull request or use the ruleset evaluator to verify direct
    unreviewed updates cannot merge.
  - Verify all required check names exactly match workflow job names.
  - Verify every Phase 0 issue is assigned to the Foundation milestone and
    linked from the epic.
- Acceptance criteria:
  - `main` remains releasable.
  - No permanent `develop` branch exists.
  - Repository secrets are not needed for normal CI.

### Task 12: Execute the Phase 0 clean-checkout acceptance gate

- Deliverables:
  - `scripts/verify-phase-0.sh`
  - `tests/e2e/test_phase_0_distribution.py`
  - `README.md`
  - `CHANGELOG.md`
  - Phase 0 GitHub epic and implementation issues
- Description: Add one fail-fast acceptance command and run it from a fresh
  clone, not the developer's existing environment. The script orchestrates
  existing commands and must not duplicate their configuration. Update the
  README so its quick start exactly reflects the verified workflow, and record
  the completed foundation in the changelog.
- Public interface changes:
  - Acceptance command: `scripts/verify-phase-0.sh`.
  - Required clean-checkout journey:

    ```bash
    uv sync --frozen
    uv run pre-commit run --all-files
    uv run workaholic --version
    uv run pytest
    uv build
    scripts/smoke-install.sh dist/*.whl
    ```

- Inputs:
  - All repository deliverables and GitHub configuration from Tasks 1-11.
- Outputs:
  - Reproducible evidence that the source checkout and built wheel behave as
    documented.
  - A closed Phase 0 milestone with links to CI evidence.
- Tests:
  - Run the acceptance script in a temporary clean clone with no active virtual
    environment and no repository-local untracked files.
  - Run the README quick start independently in the same clean-clone
    environment.
  - Confirm an intentionally stale lockfile, dirty format, failing test, or
    malformed wheel makes the gate fail.
- Acceptance criteria:
  - Every required CI check passes on `main`.
  - Architecture, roadmap, product scope, contracts, ADRs, README, and tests
    contain no contradictory compatibility, tenancy, or v1-scope statements.
  - All six golden journeys collect with explicit implementation status.
  - The Phase 0 epic and milestone are closed only after clean-checkout evidence
    is attached.

## Operational instructions

1. Implement and merge Tasks 1-12 in order. A task may be developed in parallel
   only when all of its listed inputs have already merged.
2. From Task 3 onward, every developer installs both hook stages:

   ```bash
   uv sync --frozen
   uv run pre-commit install --hook-type pre-commit --hook-type pre-push
   ```

3. Before opening or updating a pull request, run:

   ```bash
   uv run pre-commit run --all-files
   uv run pytest
   uv build
   ```

4. Every pull request that changes installation, CLI commands, output,
   prerequisites, support status, or the primary user journey must update the
   relevant `README.md` quick-start or status content in the same change.
5. Public source development begins in Phase 0, but do not publish a package or
   create a GitHub release during Phase 0. Built artifacts are CI evidence only.
6. Phase 0 introduces no persisted application schema, data migration, server,
   external service, or deployment procedure. No production migration or
   rollback step is required.
7. After Task 10 merges, make its exact job names required in the `main` ruleset.
   If a job is renamed, update the ruleset in the same operational change.
8. Close Phase 0 only after Task 12 succeeds from a clean clone and the result is
   linked from the Foundation milestone.
