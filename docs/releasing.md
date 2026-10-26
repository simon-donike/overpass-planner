# Preparing an upstream PyPI release

The package is currently `1.1.0.dev0`. No release, tag or publication is part of this refactor. The prospective first packaged stable release is `1.1.0`, following semantic versioning. Preserve the historical citation DOI/version unless the original authors explicitly establish citation metadata for a new scientific release.

Before publishing, the upstream maintainer must verify availability/ownership of `eo-overpass-predictor` on PyPI and review all package metadata, scientific regression results, migration notes and release contents.

## Trusted publisher configuration

In PyPI's project publishing settings (or pending publisher settings for a new project), add a GitHub Actions trusted publisher using:

| Field | Upstream value |
|---|---|
| Project | `eo-overpass-predictor` |
| Owner | `julianManning` |
| Repository | `eo-overpass-predictor` |
| Workflow filename | `release.yml` |
| Environment | `pypi` |

If the publishing repository differs, configure its actual owner/repository rather than copying upstream identity. Configure the GitHub `pypi` environment with appropriate reviewers and release-tag restrictions before enabling publication. OIDC authentication needs no stored PyPI API token; `id-token: write` is scoped to the publishing job. See [PyPI's trusted publishing guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

## Release procedure (requires a separate, explicitly authorized release)

1. Set `project.version` to the chosen stable semantic version and update `uv.lock` with `uv lock`. Review release notes and ensure the prospective tag matches that version.
2. Run `uv sync --locked --all-extras`, tests, Ruff lint/format checks, mypy, `uv build`, and `uv run twine check dist/*` in a clean checkout. Run `tests/validate_distribution.py` on the wheel to verify base-only installation, offline predictions, installed CLI and tracker resources.
3. Merge through the normal upstream review process. Only after release approval, create and push the matching `vMAJOR.MINOR.PATCH` tag through the maintainer's usual release process.
4. `release.yml` runs CI, rejects a non-stable or mismatched tag, builds and validates distributions, then publishes the retained build artifacts through the `pypi` environment after its configured approval checks.
5. Verify the uploaded distributions and CLI from a clean PyPI installation. Publishing this package does not itself create a GitHub release or update Zenodo metadata.

Normal branch pushes, pull requests and development versions cannot trigger publication. Normal CI runs one Ubuntu test job with Python 3.12. Formatting, typing, distribution
validation and other-platform checks can be run manually before release.
