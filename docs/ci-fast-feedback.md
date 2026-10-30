# CI recovery after fast-gate failure

The proposed GitHub-hosted fast Ruff gate failed before the macOS regression
could run. The run did not provide usable job logs or steps through the
connected GitHub API, so the exact failure cause remains unconfirmed.

This branch restores the original CI workflow: the self-hosted macOS job runs
independently, including Ruff lint and formatting, strict Mypy, full offline
pytest, wheel/sdist builds and installed wheel smoke tests. No safety or
regression checks are removed.

A separate fast feedback gate should not be reintroduced until GitHub-hosted
runner eligibility, billing, job logs and Ruff version availability are
verified. Any subsequent optimization should be measured before adoption.
