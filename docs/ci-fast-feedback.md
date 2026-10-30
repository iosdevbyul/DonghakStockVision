# CI fast feedback gate

The GitHub Actions workflow first runs Ruff lint and formatting in a separate
GitHub-hosted Linux job. It uses an explicit Ruff version matching the CI
version observed when this change was prepared. The full self-hosted macOS job
depends on this gate, so common format failures do not occupy the slow Mac
runner.

The existing macOS checks are unchanged and remain mandatory:

- Editable installation with all development/learning/API/custody dependencies
- Ruff lint and formatting (again in the full job)
- Strict Mypy
- Full offline pytest regression
- Wheel and sdist build
- Installed base, learning, API and decision wheel smoke tests

This change does not eliminate any test or permit a PR to merge on the fast
gate alone. Hosted-runner availability and tool download time can affect the
actual gain. Follow-up: inspect job durations and queued time before further
optimization; never parallelize stateful tests blindly.
