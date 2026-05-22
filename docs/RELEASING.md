# Releasing REX

Process for cutting a new version and publishing to PyPI.

## Prerequisites

- You are a maintainer with push access to `main` and tag-create permission.
- `gh` CLI authenticated against `David-Antolick/REX_voice_assistant`.
- PyPI publishing happens via [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (configured under the `pypi` environment in [.github/workflows/publish.yml](../.github/workflows/publish.yml)). No API token is needed locally.

## Steps

1. **Bump `version` in [pyproject.toml](../pyproject.toml).** Semver: `MAJOR.MINOR.PATCH`. Breaking changes bump MAJOR, new features MINOR, fixes PATCH.

2. **Write the release notes in [CHANGELOG.md](../CHANGELOG.md)** as a new top-level section. Format: `## [X.Y.Z] - YYYY-MM-DD`. Mirror the structure of prior entries (New Features / Migration notes / Documentation / Tests).

3. **Land both on `main`** in a single commit. Convention: `Release X.Y.Z: <one-line summary>`. Push and wait for CI to go green.

4. **Tag the release commit:**

   ```powershell
   git tag -a vX.Y.Z -m "Release X.Y.Z: <one-line summary>"
   git push origin vX.Y.Z
   ```

5. **Create the GitHub Release** — this is what triggers the publish workflow. Extract the CHANGELOG section for the body:

   ```powershell
   gh release create vX.Y.Z `
     --title "vX.Y.Z — <one-line summary>" `
     --notes-file <path-to-notes.md> `
     --verify-tag
   ```

   Where `<path-to-notes.md>` is a temp file containing just the body of the `## [X.Y.Z]` CHANGELOG section (no need to include the heading itself — GitHub renders it from `--title`).

6. **Workflow auto-publishes.** Watch:

   ```powershell
   gh run watch (gh run list --workflow publish.yml --limit 1 --json databaseId --jq '.[0].databaseId') --exit-status
   ```

   Build runs on `windows-latest`, publish runs on `ubuntu-latest`. Total ~1 minute. Look for `✓ publish-pypi`.

7. **Verify PyPI:**

   ```powershell
   curl -s https://pypi.org/pypi/rex-voice-assistant/json | python -c "import sys,json; print(json.load(sys.stdin)['info']['version'])"
   ```

8. **Upgrade your local install:**

   ```powershell
   uv tool upgrade rex-voice-assistant
   # if uv is caching the old version:
   uv tool install --reinstall --refresh rex-voice-assistant
   ```

9. **Clean up the temp release-notes file** if you created one.

## If something goes wrong

- **Workflow fails before publish:** fix the issue, delete the GitHub Release (`gh release delete vX.Y.Z --cleanup-tag`), and start over. The tag and release can be recreated freely as long as nothing landed on PyPI.
- **Publish succeeded but the build is broken:** you cannot reuse `X.Y.Z` on PyPI (versions are immutable). Yank it (`pip install twine; twine yank rex-voice-assistant==X.Y.Z`) and ship `X.Y.Z+1` with the fix.
- **Forgot to bump pyproject.toml before tagging:** the workflow will fail at `twine check` because the wheel filename won't match the tag. Delete the release + tag, bump pyproject on `main`, retag from the new commit.

## Test PyPI dry run

To test the publish path without hitting real PyPI, trigger the workflow manually with `test_pypi: true`:

```powershell
gh workflow run publish.yml -f test_pypi=true
```

This uses the `test-pypi` environment and publishes to `test.pypi.org`. Useful for verifying packaging changes before a real release.
