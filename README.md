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

See [our approach](docs/principles.md) for how
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

1. Paste the one-line command from your city's README into Terminal (macOS)
   or PowerShell (Windows). Without a city argument it connects to a practice
   city chosen by your language:

   ```
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/4dcitygml/tools/install-v1/install/citygml.sh)" -- <owner>/<repo>
   ```
   The script installs the newest `hub-v` release into
   `~/Documents/citygml-tools/citygml-hub/<version>/` after verifying it against
   the SHA-256 digest GitHub publishes for the asset, keeps a copy of itself in
   `~/Documents/citygml-tools/`, and starts the hub.
2. The hub opens in your browser, connected to that city. After setup it offers
   a desktop icon; from then on the icon starts the tools (no terminal needed).
3. Newer versions are announced inside the hub; *Get it now* downloads and
   verifies them, and they are used from the next start. Nothing is downloaded
   or restarted without your click.
4. Sign in with GitHub when prompted (the screen states exactly what you
   authorize), create your copy of the city, and import it. Viewing the data
   itself needs no account: open the city repository or the portal.

Nothing is ever changed directly: every edit becomes a pull request, reviewed
by the data maintainer — and the approved history *is* the record of the city.

## Shared processing and practice

Cities improve their own data while contributing cases that improve the common
processing, checks and explanations. See [the shared approach](docs/principles.md).

The [LOD0 semantic correction recipe](docs/lod0-semantic-correction.md) is a
pilot implementation included in version 1.1.0. It requires evidence review,
a compatible city CI version and a city GitHub pilot before routine use.
Consult [implementation status](docs/implementation-status.md) before enabling a procedure.

## Common releases

[Releases](https://github.com/4dcitygml/tools/releases) serve two destinations:

| Series | Downloads | Who updates it |
|---|---|---|
| `hub-v` | Mac and Windows Hub clients | The user, from the hub's update banner (cities do not pin the client) |
| `tools-v` | Common processing, CI, schemas, semantic definitions, guidance and supporting sources | City operators, after checking the impact on their procedures |

Hub keeps its existing two client assets and version series. City tooling has its own
`tools-v` series (first `tools-v1.1.0`, currently `tools-v1.2.2`). Versions can advance
independently. See [distribution and verification](docs/shared-tooling-release.md)
for the current transition, exact filenames and checks.

City CI pins an immutable tools commit; each case records the version used.
Installing a new Hub does not update a city's CI pin. The common source ZIP
requires Python dependencies and is not an automatic city installer.

Cities distribute no code: no starter kit, no launcher, no client pin
(Exchange Contract A11). The one-line installer above and the desktop icon
are the only entry points. Automatic CI version selection and automatic
Issue-to-PR processing are still planned work.

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
