# 3DCityDB connector

Experimental, opt-in connector for reviewing changes from 3DCityDB v5 as
minimal-diff GitHub pull requests. The Git repository remains the reviewed
integration point; a DB edit is a proposal until its pull request is merged.
See [`../../docs/3dcitydb-github-integration.md`](../../docs/3dcitydb-github-integration.md)
for the source-of-truth model, local-only data boundary, and conflict policy.
The implementation contract is
[`../../docs/3dcitydb-integration-implementation-spec.md`](../../docs/3dcitydb-integration-implementation-spec.md).

## Initial scope

- 3DCityDB 5.1.x and citydb-tool 1.3.2
- canonical CityGML 2.0
- one configured CityGML file per synchronization
- existing-building changes, one building per pull request
- `FEATURE.last_modification_date`, `updating_person`, `reason_for_update`,
  and `lineage` are used to prefill the PR review form
- no-op export produces no Git change
- URO 3.x wrappers encoded by `xslt/uro_to_generic.xsl` are restored before
  comparison (the namespace is inferred from the reviewed CityGML)

Adds, deletes, renames, multi-building proposals, geometry changes,
appearances, and automated changelog cursoring remain fail-closed. The full
export is currently compared semantically; changelog will later reduce the
export scope and provide an independent cross-check. The configured DB export
scope must therefore correspond to the configured CityGML file.

## Configuration

Copy `config.example.json` outside the public repository and edit the paths.
Passwords are never stored in this JSON. Use the standard `CITYDB_*`
environment variables and `gh auth login`.

The native plugin launcher requires Python 3.12 (or another supported Python
3 installation) with the packages in `requirements.txt`. Set `PYTHON` or pass
`citydb sync --python /path/to/python` when it is not available as `python3`.

The first button is deliberately read-only:

1. **Sync**: export, normalize, semantic diff, minimal reconstruction, preview.
2. **Create PR**: isolated worktree, one-building commit, push, `gh pr create`.

If `psql` is available, versioning metadata is queried from the configured
`FEATURE` table. If it is unavailable or a field is empty, the UI requires the
operator to enter the missing public explanation.
