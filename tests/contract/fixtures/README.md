# Import-Boundary Violation Fixtures

These packages are intentionally invalid and exist only to prove that the
architecture tests fail for the right reason.

- `import_boundaries` contains Domain-to-Application,
  Application-to-Persistence, and CLI-to-Persistence edges. Its Import Linter
  contracts mirror the production layer and direct-import rules.
- `heavy_cli` imports sentinel server-framework, PostgreSQL-driver, and server
  scheduling modules during CLI startup.

The fixtures are excluded from mypy because unresolved, shadowed, and
deliberately misplaced imports are their test data. They remain covered by
Python parsing, Ruff, and the contract tests that execute them. No production
package is excluded from typing or Import Linter.
