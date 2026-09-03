# Generate CityGML 3.0 derived objects from CityGML 2.0 source

`convert_citygml2_to3_iur4.py` is an initial converter to generate CityGML 3.0 + i-UR 4.0 + i4d-UR derived GML without modifying the CityGML 2.0 + i-UR 3 source.

## Current scope

Only handles the following building ADEs used in the Tokyo Station sample.

- BuildingIDAttribute, BuildingDetailAttribute
- BuildingRiverFloodingRiskAttribute, BuildingHighTideRiskAttribute
- BuildingDataQualityAttribute, KeyValuePairAttribute

Unsupported elements, multiple old data quality attributes, and mismatched `gml:id` values before and after conversion are treated as errors. Output that converts only the CityGML core and discards ADEs is not considered successful.

## Execution

First, verify that the source is within the current supported range.

```bash
python scripts/convert_citygml2_to3_iur4.py inspect path/to/source.gml
```

Next, specify a fixed version of citygml-tools to perform the conversion. Place the output in a separate directory from the source.

```bash
python scripts/convert_citygml2_to3_iur4.py convert \
  path/to/source.gml path/to/generated/source.gml \
  --citygml-tools path/to/citygml-tools
```

The converter runs `citygml-tools upgrade --no-pretty-print` with `TZ=UTC`, restores the saved ADEs by building `gml:id`, and creates `*.conversion.json` next to the output. It records input/output hash, conversion count, compatibility processing, and ID immutability constraints.

If `citygml-tools` generates different IDs for Appearances without IDs on each run, the converter replaces them with stable IDs derived from content and updates XLink references simultaneously. This stabilizes the regenerated hash of the derived GML.

If you already have a file with only the CityGML core converted to 3.0, you can try ADE restoration only.

```bash
python scripts/convert_citygml2_to3_iur4.py restore-ade \
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

The Tokyo Station sample includes a manual workflow `export-citygml3.yml`. By default, the job does not run; only the PoC with explicitly stated `accept_provisional_i4dur` is generated as a 7-day artifact. No commits or pushes are made to the source. Before formal deployment, pin `tools_ref` to a release tag or commit and remove the provisional approval input.
