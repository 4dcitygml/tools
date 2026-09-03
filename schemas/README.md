<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 (this README only; vendored schemas keep their own licenses) -->

# schemas/ — Schemas for offline XSD validation (vendored)

A local mirror of third-party XSD schemas that lets `scripts/validate_citygml.py`
validate CityGML/PLATEAU **without network access**.

## Layout

- `schemas.opengis.net/` — the **CityGML 2.0** modules + **GML 3.1.1** (distributed by OGC)
- `www.w3.org/` — xlink / SMIL 2.0 (W3C)
- `docs.oasis-open.org/` — xAL 2.0 (OASIS)
- `master.xsd` — the validation root that imports all the namespaces above plus the
  bundled i-UR (2.0/3.0/3.1/3.2). `http(s)://` references inside the schemas are resolved
  to this mirror by the lxml Resolver in `validate_citygml.py`.
- i-UR (`uro/2.0–3.2`, `urf/2.0–3.2`): 3.0–3.2 are taken from `schemas/iur/` bundled in
  the official PLATEAU distribution zip; 2.0 (the edition used by the 2020–2021 PLATEAU
  datasets, e.g. Tokyo 23 wards 2020 v4) was fetched from the official schema directory
  https://www.geospatial.jp/iur/schemas/ (2026-09-02). Its `urbanObject.xsd` is
  byte-identical to the copy in the 2020 Tokyo ZIP; `urbanFunction.xsd` matches the
  2022 ZIP (the 2020 ZIP carried an earlier revision of the same 2.0 namespace).
  i-UR 1.4/1.5 (PLATEAU 2020 v1–v3 packages) are no longer published and are not
  bundled; re-download the current v4 package instead. i-UR 4.0 targets CityGML 3.0
  and is out of scope for this CityGML 2.0 validator.

Verified that real data (PLATEAU-distributed GML, tens of MB / on the order of a
thousand buildings) comes out `valid=True` (about 1.3 seconds). Compilation and
validation work even with the network cut off.

## Sources and updates

The files were obtained by recursively following `xsd:import`/`include` from each
distributor (a closure of 51 files). They match the URLs pointed to by the
`xsi:schemaLocation` of real gml files and by the i-UR imports. To update, re-fetch
with the same procedure.

## Licenses (important)

**Each .xsd in this directory is a third-party work** and is governed by its
distributor's license (this repository's Apache-2.0 does not apply):

- OGC schemas (CityGML / GML): the OGC schema terms of use (redistributable)
- W3C (xlink / SMIL): the W3C Software/Document License
- OASIS (xAL): the OASIS schema terms of use

They are bundled (vendored) for the convenience of validation; the rights to the
originals belong to the respective organizations.
