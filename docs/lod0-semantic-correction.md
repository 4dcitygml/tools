<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# LOD0 semantic correction: pilot recipe

City tooling is distributed independently as `tools-v1.1.0`.
See [distribution status](shared-tooling-release.md) before adopting it.

Status: pilot implementation included in tools-v1.1.0; city GitHub PR verification pending.
This recipe changes the interpretation of an existing boundary. It does not
repair its coordinates or establish that any particular building is a roof outline.
A plan and reviewable evidence are required before adoption.

## Scope and maturity

`lod0-footprint-to-roofedge-v1` supports CityGML 2.0 with i-UR 3.1 building IDs.
It changes exactly the opening and closing XML element names of one direct
`bldg:lod0FootPrint` property to `bldg:lod0RoofEdge` for every eligible building
in one mesh. All other bytes, including coordinates, IDs, other attributes,
namespace declarations, comments, whitespace and line endings, stay unchanged.

Inputs must have direct Building members with unique building IDs and gml:ids,
a matching municipality, and inline MultiSurface/posList geometry. BuildingPart,
DTD, ambiguous or coexisting FootPrint/RoofEdge properties, property references,
and unsupported editions are rejected. Buildings already using RoofEdge or
without FootPrint are listed as exclusions and stay unchanged. An empty result
must not create a PR. Existing XSD and geometry validation still apply.

The transformation reads namespaces, not textual prefix guesses. The existing
commit-scope parser requires `core:cityObjectMember` and `gml:id` serialization;
commit generation checks this compatibility before changing the working tree.
Broader parser support is not implied by the transformer's prefix support.

This is a pilot, not an automatically accepted operation for arbitrary cities.
Its applicability depends on evidence about the original boundary's meaning.
Successful reproduction does not establish that meaning. The first sorted
30 targets (or all if fewer) form a deterministic review selection, not a
statistically representative audit or proof of applicability to all targets.

## Generate and review a plan

Use `scripts/lod0_semantic_manifest.py generate --help` for arguments.
Required inputs include the repository, mesh, municipality, current GML,
rationale file, product path, actual tools commit SHA and plan Issue reference.
The command writes a bulk manifest and optionally a separate proposed GML and
five-part review note (`--apply-output`, `--report`). It does not overwrite inputs.

The manifest has kind `semantic-correction`, recipe and profile identifiers,
input and rationale hashes, a single product hash, targets and exclusions.
The rationale is fetched and hash-checked alongside the GML. Human approval is
recorded through GitHub reviews, not by editing an immutable manifest's result.

For a city PR, use `--current-uri git:<base-sha>:<product-path>` and a pinned
Git blob or HTTPS resource for `--rationale-uri`. Put the rationale in the base
history or publish it separately before making this data PR. File URIs are
only for local work; city CI rejects them. Use a real published tools commit,
not the local rehearsal snapshot, before submission.

## Create building commits and reproduce

1. In a clean temporary city checkout, generate the manifest under
   `provenance/semantic-correction/<mesh>-lod0.json`.
2. Run `commits --repo <checkout> --manifest <manifest>`. Only the newly
   generated, untracked manifest may be present in the otherwise clean tree.
   The command checks the exact HEAD reference and product before writing.
3. Each target becomes one `Building:` commit referencing the same manifest
   digest. Together they form one mesh's bulk PR. No remote operation is made.
4. `fetch_materials.py` retrieves the declared inputs. Run
   `verify --manifest <manifest> --materials-dir <retrieved-directory>`.
   The directory contains `current` and `rationale`.
5. The normal commit-scope gate additionally verifies the exact base-to-head
   product, each intermediate commit and the changed file set. Extra files,
   altered headers, repeated/missing targets, mixed changes and later manifest
   replacement are rejected, including changes hidden by a later revert.

Reproduction is dispatched by `ci/pr_analysis_main.sh` and includes the
recipe's change/evidence/checks/impact/recommended-action note in the existing
reproduction artifact, which the shared report publisher already reads.
For this initial recipe, the city CI's trusted tools SHA must equal the
manifest's builder SHA. Updating the city tooling pin is therefore a prerequisite
when adopting a new recipe release; merely downloading a new generator is not
enough. General per-case version selection remains future work.

## Local verification and remaining release work

Tests cover namespace handling, literal comment preservation, BOM/CRLF,
wrong editions/municipalities, duplicate IDs, ambiguity, input/evidence/product
hashes, protected input files and adversarial PR histories.

The local rehearsal script in the internal project records uses a pinned
source archive and isolated Git repositories without remotes. Its source
snapshot SHA is real but local, and must never be described as a release SHA.

Before city operation: publish the tested tools version, update city CI pins,
prepare accessible rationale and the plan Issue, and verify GitHub CI, shared
report, standard review and merge/history behavior. Also distinguish the
bundled master XSD check from validation with an edition-specific official
schema profile; the latter is not added by this recipe.
