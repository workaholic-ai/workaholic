# Contributing to Workaholic AI

Workaholic AI is in pre-alpha development. Contributions should preserve the
current foundation contracts and clearly distinguish implemented behavior from
the planned v1 direction.

## Development environment

Development requires Git, uv, and CPython 3.14. From a source checkout, install
the locked project and development dependencies:

```bash
uv sync
```

Install both local Git hook stages:

```bash
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

The commit stage formats and lints source and documentation, validates common
repository formats, checks for accidental private keys, and runs strict static
typing. The pre-push stage runs the complete test suite and builds both package
distributions.

## Quality checks

Run the complete local quality gate before opening a pull request:

```bash
scripts/check.sh
```

Useful focused commands are:

| Purpose | Command |
| --- | --- |
| Commit-stage checks | `uv run pre-commit run --all-files` |
| Complete test suite | `uv run pytest` |
| One test module | `uv run pytest --no-cov tests/unit/docs/test_public_documentation.py` |
| Package build | `uv build` |
| CLI smoke check | `uv run workaholic --version` |

Tests treat warnings as errors and enforce the coverage threshold configured in
`pyproject.toml`. Add unit, integration, contract, or end-to-end coverage in
proportion to the behavior being changed. Bug fixes should include a regression
test.

## Code and interface expectations

- Keep modules small and dependency direction explicit.
- Prefer clear, typed interfaces over implementation magic.
- Validate runtime input at boundaries rather than relying on type hints alone.
- Use Google-style docstrings for maintained modules, functions, classes, and
  models. Document arguments, return values, raised exceptions, constraints,
  and invariants where they are relevant.
- Put observable contracts in interfaces and demonstrate their intent with
  focused tests.
- Keep credentials and arbitrary remote endpoints out of repository-controlled
  configuration.

## Architecture boundaries

The domain core depends on no adapter or presentation package. Application code
may depend on domain contracts, while CLI, persistence, protocol, client, and
server packages adapt those inward-facing contracts.

The CLI must communicate through the session boundary rather than directly
through persistence. Local and remote sessions must preserve the same
application behavior. Importing the normal CLI path must not start services,
access storage, use the network, or write to user directories.

Review [the architecture](ARCHITECTURE.md), [product scope](docs/product-scope.md),
and accepted [architecture decisions](docs/adr/) before changing a boundary or
public contract. Material changes require an ADR rather than an undocumented
exception.

## Documentation update policy

README review is mandatory for every user-visible pull request. Update the
README in the same pull request when a change affects installation, commands,
documented output, prerequisites, compatibility or support status, security
guidance, or the principal user journey.

| Change | Required documentation review |
| --- | --- |
| Dependency, runtime, or installation requirement | Prerequisites and quick start |
| CLI command, flag, output, or exit behavior | Current CLI and relevant examples |
| Newly working or removed capability | Development notice and current feature description |
| Compatibility or platform support | Compatibility notice and policy |
| Principal local, agent, or team journey | Quick start, current behavior, and v1 direction |
| Security boundary or reporting path | Security notice and `SECURITY.md` |
| Architecture or public contract | Linked architecture, contract, and ADR documents |

Do not describe planned commands as available. Any future example must appear
under a visible `Planned for v1 (not implemented)` heading until its executable
behavior and tests ship.

If README changes are not required, record why in the pull request template.
Passing tests do not replace this documentation review.

## Pull requests

Keep pull requests independently reviewable and include:

- the problem and intended outcome;
- the implementation and important tradeoffs;
- commands used to verify the change;
- tests for observable behavior;
- completed impact reviews from the pull request template;
- linked ADRs or contract updates when a boundary changes.

Before submitting, confirm that relative documentation links resolve and that
every documented command and output example matches the current package.

Report vulnerabilities privately through the [security policy](SECURITY.md),
not in a public issue or pull request.
