# 4dcitygml 3DCityDB Sync plugin

This plugin adds the `citydb sync` command to **citydb-tool 1.3.2**. The
command opens a local review screen that compares a 3DCityDB CityGML 2.0
export with the reviewed Git file and, after an explicit confirmation, can
create a one-building pull request.

The plugin intentionally keeps the synchronization core in
`connectors/3dcitydb`. This makes the CityGML semantic-diff contract reusable
from CI and other clients while the JAR remains a small citydb-tool entrypoint.

## Build

Java 17, Python 3 with the bundled `connector/requirements.txt` installed, and
an unpacked citydb-tool 1.3.2 release are required.

```bash
export CITYDB_HOME=/path/to/citydb-tool-1.3.2
./build.sh
```

Copy the generated directory into the official plugin folder:

```bash
cp -R build/distributions/4dcitygml-citydb-sync \
  "$CITYDB_HOME/plugins/4dcitygml-citydb-sync"
```

Run the installation check and start the UI:

```bash
citydb sync --repo /path/to/city-repository \
  --citygml path/inside/repository/mesh.gml --check

citydb sync --repo /path/to/city-repository \
  --citygml path/inside/repository/mesh.gml
```

If Python is not available as `python3`, pass `--python /path/to/python` or
set `PYTHON`. The generated plugin directory is self-contained apart from the
Python runtime and packages; it includes the semantic diff engine and URO
round-trip adapter.

DB connection values are inherited from the standard citydb-tool environment
variables (`CITYDB_HOST`, `CITYDB_PORT`, `CITYDB_NAME`, `CITYDB_SCHEMA`,
`CITYDB_USERNAME`, `CITYDB_PASSWORD`). GitHub authentication is delegated to
the GitHub CLI (`gh auth login`).

For offline testing, pass an existing export with `--export-file`.

## Safety boundary

- **Sync** only exports, compares, and shows a plan. It does not edit Git.
- **Create PR** requires a clean repository, unchanged `HEAD`, one modified
  building, a reason, a source, and a public author name.
- PR creation happens in a temporary Git worktree; the user's checkout is not
  overwritten.
- Adds, deletes, renames, and multi-building changes stop for manual review in
  this initial version.
- Geometry changes are displayed by Sync but cannot be published by the
  initial Create PR action.
- `FEATURE` metadata is read with `psql` when available. Missing metadata is
  collected in the review form rather than guessed.
