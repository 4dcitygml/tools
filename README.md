# 4dcitygml

**Add history — the axis of time — to your 3D city model, and make it 4D.**

**From semantic modeling to semantic operation.**

A 3D city model is usually a snapshot: delivered once, outdated soon after.
4dcitygml turns it into a living record. Buildings are edited one at a time,
every change is reviewed and merged as Git history — so the model carries not
only geometry and semantics, but *when, what and why* something changed.

## What's inside

| Tool | What it does |
|---|---|
| **Hub** | Opens your city, launches the tools, tracks your change proposals |
| **Attribute editor** | Click a building on the map, fix a value, cite your source |
| **Texture editor** | Adjust or replace facade photos, aligned on the model |
| **Review** | Maintainers approve proposals — one commit, one building |

The canonical repository format can remain CityGML 2.0. A bounded,
fail-closed CityGML 3.0 + i-UR 4.0 derivative converter is documented in
[`docs/citygml2-to3-iur4.md`](docs/citygml2-to3-iur4.md).

An experimental, opt-in 3DCityDB v5 connector is available under
[`connectors/3dcitydb`](connectors/3dcitydb). Its citydb-tool plugin adds a
`citydb sync` command and a local review screen. The first **Sync** action is
read-only; a separate confirmation creates a one-building, minimal-diff pull
request.

## Quick start

1. Download the latest release and unzip it.
2. Double-click `start-mac.command` (macOS) or `start-windows.bat` (Windows).
3. The hub opens in your browser. Pick your city — it is already selected if
   you installed from your municipality's repository.
4. Browse freely; viewing needs no account. To send a change proposal, sign
   in with GitHub when prompted.

Nothing is ever changed directly: every edit becomes a pull request, reviewed
by the data maintainer — and the approved history *is* the record of the city.

## For municipalities

Your city stays in your own repository, under your name, your license, your
logo and theme — the tools are shared. Start from the `city-template`
repository.

If you need support, contact us anytime — open an
[issue](https://github.com/4dcitygml/tools/issues). For anything sensitive, use the
private report form linked from [SUPPORT.md](https://github.com/4dcitygml/.github/blob/main/SUPPORT.md).

## For developers

**Try connecting your own tool.** A city repository is plain Git and CityGML
— there is no proprietary API in front of it. Any tool that follows the
repository conventions can propose changes directly:

- **One commit = one building**, with a `Building: <uro:buildingID>` trailer,
  and merged history is never rewritten — see the
  [PR operations guide](https://github.com/4dcitygml/city-template/blob/main/docs/pr-operations.md).
- **Sources are recorded** with standard CityGML mechanisms only — see the
  [provenance rules](https://github.com/4dcitygml/city-template/blob/main/docs/provenance-rules.md).
- **PR bodies and CI comments** carry language-independent anchors
  (`<!--sec:reason-->`, `<!--cp:key-->`), so your tool can generate and parse
  them regardless of display language.
- **Data contributions are CC0** — see the
  [data contribution policy](https://github.com/4dcitygml/city-template/blob/main/docs/data-contribution-policy.md).

CI reviews proposals from any client the same way it reviews ours. If your
format or workflow needs an adapter, propose it in an issue and send a PR.

## License

Apache-2.0 — the same license as the core CityGML tooling ecosystem
(citygml4j, citygml-tools, 3DCityDB). This is an independent open-source
project: it currently has no affiliation with the OGC, and is not endorsed
by it. CityGML is a trademark of the Open Geospatial Consortium, used here
descriptively to refer to the data format.
