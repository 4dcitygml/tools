# Generate CityGML 3.0 derived objects from CityGML 2.0 source

`convert_citygml2_to3_iur4.py` is an initial converter to generate CityGML 3.0 + i-UR 4.0 + i4d-UR derived GML without modifying the CityGML 2.0 + i-UR 3 source.

Every city is exported by `scripts/export_citygml3.py` (REQ-dialect-packs section 7): the core 2.0 / 1.0 to 3.0 conversion is the pinned citygml-tools' `upgrade`, the inputs are the city's declaration (`data_dirs`, its pack), and every output is validated by citygml-tools against the CityGML 3.0 schemas and the pack's. A city without a pack gets the upgrade alone: generic attributes and appearances pass through, and a namespace other than CityGML, GML, xAL or XLink is refused. A pack with an ADE converts it in its hook (`Pack.citygml3`); for PLATEAU that is this converter, `dialects/plateau/convert_citygml2_to3_iur4.py`, and its provisional i4d-UR schema and code list (`dialects/plateau/i4dur/1.0/`) are copied beside the output.

## Current scope

Only handles the following building ADEs used in the Tokyo Station sample.

- BuildingIDAttribute, BuildingDetailAttribute
- BuildingRiverFloodingRiskAttribute, BuildingHighTideRiskAttribute
- BuildingDataQualityAttribute, KeyValuePairAttribute

Unsupported elements, multiple old data quality attributes, and mismatched `gml:id` values before and after conversion are treated as errors. Output that converts only the CityGML core and discards ADEs is not considered successful.

## Execution

First, verify that the source is within the current supported range.

```bash
python dialects/plateau/convert_citygml2_to3_iur4.py inspect path/to/source.gml
```

Next, specify a fixed version of citygml-tools to perform the conversion. Place the output in a separate directory from the source.

```bash
python dialects/plateau/convert_citygml2_to3_iur4.py convert \
  path/to/source.gml path/to/generated/source.gml \
  --citygml-tools path/to/citygml-tools
```

The converter runs `citygml-tools upgrade --no-pretty-print` with `TZ=UTC`, restores the saved ADEs by building `gml:id`, and creates `*.conversion.json` next to the output. It records input/output hash, conversion count, compatibility processing, and ID immutability constraints.

If `citygml-tools` generates different IDs for Appearances without IDs on each run, the converter replaces them with stable IDs derived from content and updates XLink references simultaneously. This stabilizes the regenerated hash of the derived GML.

If you already have a file with only the CityGML core converted to 3.0, you can try ADE restoration only.

```bash
python dialects/plateau/convert_citygml2_to3_iur4.py restore-ade \
  path/to/source.gml path/to/core-3.gml path/to/output.gml
```

## Conversion rules

- Building IDs, building details, disaster risks, and KeyValuePairs that match in meaning and structure are migrated to official i-UR 4.0 elements.
- Old `detailedUsage*` are not speculatively mapped to new codes but preserved as original values and codeSpace in the official `urc:KeyValuePairAttribute`.
- Old BuildingDataQualityAttribute is currently preserved as KeyValuePair values for each item. No speculative expansion to LOD-specific new attributes.
- `surveyYear` is converted from `YYYY` to `YYYY-01-01`, and one `i4dur:SurveyYearEncoding` is added to the CityModel.

## Incomplete gates

This initial version alone does not complete the CityGML 3.0 distribution package for public release. The following are required.

- i4d-UR namespace finalization and XSD publication
- Offline XSD validation including official i-UR 4.0 and i4d-UR
- Fixed placement of original and official 4.0 codelists with hash
- Reverse conversion testing 2.0 → 3.0 → 2.0
- Stream processing for nationwide-scale data

The current default namespace `https://4dcitygml.github.io/schemas/i4dur/1.0` is provisional and recorded as such in the manifest. The generated output will not be formally distributed until the publication location is confirmed and finalized.

This repository carries a manual workflow, `export-citygml3.yml` (moved from the Tokyo Station sample on 2026-09-30). It takes a city repository and a ref of it; the data folders come from the city's declaration, and the converter is this repository at the commit the run is dispatched on, so `provenance.txt` in the artifact names both commits. The export runs twice and the two results must be byte-identical; the result is a 7-day artifact, and nothing is committed or pushed to the source. A pack whose output uses a provisional namespace (PLATEAU's i4d-UR) needs the explicit `accept_provisional` input; before formal deployment, dispatch from a release tag and remove that input.
