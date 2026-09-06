# 4dcitygml tools

**Add history — the axis of time — to your 3D city model, and make it 4D.**

**Extend open-source culture to city data and the tools and practices that sustain it.**

A 3D city model is usually a snapshot: delivered once, outdated soon after.
4dcitygml turns it into a living record. Buildings are edited one at a time,
every change is reviewed and merged as Git history — so the model carries not
only geometry and semantics, but *when, what and why* something changed.

The same process improves the shared tools: cases from city repositories help
us refine processing, checks, definitions and guidance, then share the results
through common releases. Cities keep their data and adoption decisions in their
own repositories. 4dcitygml gathers reusable findings from their ordinary issues
and pull requests; a separate report is not required. Cities can also consult
us directly about problems in the shared tools.

See [our approach](docs/principles.md) ([日本語](docs/ja/principles.md)) for how
city data, shared practice and feedback to standards development connect.

## What's inside

| Component | What it does |
|---|---|
| **Hub** | Opens your city, launches the tools, tracks your change proposals |
| **Attribute editor** | Click a building on the map, fix a value, cite your source |
| **Texture editor** | Adjust or replace facade photos, aligned on the model |
| **Processing recipes** | Prepare supported changes with recorded inputs, evidence and reproducible results |
| **CI and review** | Check proposals, explain results and support the city's standard GitHub approvals |
| **Definitions and guidance** | Share schemas, semantic definitions and procedures informed by city practice |

The canonical repository format can remain CityGML 2.0. A bounded,
fail-closed CityGML 3.0 + i-UR 4.0 derivative converter is documented in
[`docs/citygml2-to3-iur4.md`](docs/citygml2-to3-iur4.md).

An experimental, opt-in 3DCityDB v5 connector is available under
[`connectors/3dcitydb`](connectors/3dcitydb). Its citydb-tool plugin adds a
`citydb sync` command and a local review screen. The first **Sync** action is
read-only; a separate confirmation creates a one-building, minimal-diff pull
request.

## Quick start

1. Download your city's **starter kit** (linked from the city repository's
   README) and unzip it. It contains the starters and the city's configuration;
   the pinned hub release is downloaded and checksum-verified on first start.
2. Double-click `start-mac.command` (macOS) or `start-windows.bat` (Windows).
3. The hub opens in your browser, connected to that city. (A hub release
   downloaded directly from this repository connects to the Tokyo Station demo.)
4. Sign in with GitHub when prompted (the screen states exactly what you
   authorize), create your copy of the city, and import it. Viewing the data
   itself needs no account: open the city repository or the portal.

Nothing is ever changed directly: every edit becomes a pull request, reviewed
by the data maintainer — and the approved history *is* the record of the city.

## Shared processing and practice

Cities improve their own data while contributing cases that improve the common
processing, checks and explanations. See [the shared approach](docs/principles.md)
([日本語](docs/ja/principles.md)).

The [LOD0 semantic correction recipe](docs/lod0-semantic-correction.md) is a
pilot implementation included in version 1.1.0. It requires evidence review,
a compatible city CI version and a city GitHub pilot before routine use.
Consult [implementation status](docs/implementation-status.md) before enabling a procedure.

## Common releases

One [4dcitygml tools release](https://github.com/4dcitygml/tools/releases)
delivers the shared components at a recorded source version. The existing
`hub-v<version>` tag format is retained for download compatibility; it names
the common release, which includes more than the Hub.

| Download | Use |
|---|---|
| Windows Hub ZIP | Launch the city tools with bundled Python and Git |
| macOS Hub ZIP | Launch the city tools using Command Line Tools Python and Git |
| Common source ZIP | Use processing, CI, schemas, semantic definitions, documentation and tool sources; Python dependencies are required |

The source ZIP is introduced in version 1.1.0. See
[distribution and verification](docs/shared-tooling-release.md) for exact asset
names and checks. City CI pins an immutable tools commit; each case records the
version used. Installing a new Hub does not update a city's CI pin.

City starter kits currently remain in city releases. Moving them into the common
release, automatic version selection and automatic Issue-to-PR processing are
still planned work.

## For municipalities

Your city stays in your own repository, under your name, your license, your
logo and theme — the tools are shared. Start from the `city-template`
repository.

If you need support, contact us anytime — open an
[shared-tool consultation](https://github.com/4dcitygml/tools/issues/new?template=tooling_consultation.yml). For anything sensitive, use the
private report form linked from [SUPPORT.md](https://github.com/4dcitygml/.github/blob/main/SUPPORT.md).

## For developers

**Try connecting your own tool.** A city repository is plain Git and CityGML
— there is no proprietary API in front of it. Any tool that follows the
repository conventions can propose changes directly:

- **Ordinary edits use one commit per building**, with a
  `Building: <uro:buildingID>` trailer. Supported bulk and lifecycle procedures
  have dedicated contracts; merged history is never rewritten — see the
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
