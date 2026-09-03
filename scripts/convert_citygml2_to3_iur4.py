#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Convert the bounded CityGML 2.0 + i-UR 3 profile to CityGML 3.0 + i-UR 4.

The CityGML core conversion is delegated to a pinned citygml-tools executable.
Before that conversion, supported i-UR 3 ADE fragments are captured by gml:id.
They are restored afterwards as i-UR 4 or lossless legacy KeyValuePair values.

This first implementation deliberately supports only the ADE objects present in
the Tokyo Station sample.  Unknown wrappers, children, duplicate legacy data
quality groups, and missing target IDs fail closed instead of being discarded.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.safe_xml import safe_iterparse, safe_parser  # noqa: E402


VERSION = "0.1.0"

CORE2 = "http://www.opengis.net/citygml/2.0"
BLDG2 = "http://www.opengis.net/citygml/building/2.0"
GML31 = "http://www.opengis.net/gml"
CORE3 = "http://www.opengis.net/citygml/3.0"
BLDG3 = "http://www.opengis.net/citygml/building/3.0"
CON3 = "http://www.opengis.net/citygml/construction/3.0"
APP3 = "http://www.opengis.net/citygml/appearance/3.0"
GML32 = "http://www.opengis.net/gml/3.2"
XLINK = "http://www.w3.org/1999/xlink"
URO4 = "https://www.geospatial.jp/iur/uro/4.0"
URC4 = "https://www.geospatial.jp/iur/urc/4.0"
XSI = "http://www.w3.org/2001/XMLSchema-instance"

DEFAULT_I4DUR_NS = "https://4dcitygml.github.io/schemas/i4dur/1.0"
DEFAULT_I4DUR_XSD = "https://4dcitygml.github.io/schemas/i4dur/1.0/i4dUR.xsd"
DEFAULT_COMPAT_KEY_CODESPACE = (
    "../../codelists/i4d-ur/LegacyIUR3Attribute_key.xml"
)

SUPPORTED_WRAPPERS = {
    "buildingIDAttribute": "BuildingIDAttribute",
    "buildingDetailAttribute": "BuildingDetailAttribute",
    "buildingDisasterRiskAttribute": {
        "BuildingRiverFloodingRiskAttribute",
        "BuildingHighTideRiskAttribute",
    },
    "buildingDataQualityAttribute": "BuildingDataQualityAttribute",
    "keyValuePairAttribute": "KeyValuePairAttribute",
}

BUILDING_ID_CHILDREN = {"buildingID", "branchID", "partID", "prefecture", "city"}
BUILDING_DETAIL_CHILDREN = {
    "serialNumberOfBuildingCertification",
    "siteArea",
    "totalFloorArea",
    "buildingFootprintArea",
    "buildingRoofEdgeArea",
    "developmentArea",
    "buildingStructureType",
    "buildingStructureOrgType",
    "fireproofStructureType",
    "implementingBody",
    "urbanPlanType",
    "areaClassificationType",
    "districtsAndZonesType",
    "landUseType",
    "reference",
    "majorUsage",
    "majorUsage2",
    "orgUsage",
    "orgUsage2",
    "detailedUsage",
    "detailedUsage2",
    "detailedUsage3",
    "groundFloorUsage",
    "secondFloorUsage",
    "thirdFloorUsage",
    "basementUsage",
    "basementFirstUsage",
    "basementSecondUsage",
    "vacancy",
    "buildingCoverageRate",
    "floorAreaRate",
    "specifiedBuildingCoverageRate",
    "specifiedFloorAreaRate",
    "standardFloorAreaRate",
    "buildingHeight",
    "eaveHeight",
    "note",
    "surveyYear",
}
LEGACY_DETAIL_CODES = {"detailedUsage", "detailedUsage2", "detailedUsage3"}
DATA_QUALITY_CHILDREN = {
    "srcScale",
    "geometrySrcDesc",
    "thematicSrcDesc",
    "appearanceSrcDesc",
    "lod1HeightType",
    "lodType",
}
RISK_CHILDREN = {"description", "rank", "rankOrg", "depth", "adminType", "scale", "duration"}
KVP_CHILDREN = {"key", "codeValue"}
SUPPORTED_IUR3_LOCALS = (
    set(SUPPORTED_WRAPPERS)
    | {
        "BuildingIDAttribute",
        "BuildingDetailAttribute",
        "BuildingRiverFloodingRiskAttribute",
        "BuildingHighTideRiskAttribute",
        "BuildingDataQualityAttribute",
        "KeyValuePairAttribute",
    }
    | BUILDING_ID_CHILDREN
    | BUILDING_DETAIL_CHILDREN
    | DATA_QUALITY_CHILDREN
    | RISK_CHILDREN
    | KVP_CHILDREN
)

GYEAR = re.compile(r"^(?P<year>-?\d{4,})(?P<tz>Z|[+-]\d{2}:\d{2})?$")


class ConversionError(RuntimeError):
    """Raised when lossless conversion cannot be proved."""


@dataclass
class SourceRecord:
    gml_id: str
    building_id: str | None
    wrappers: list[etree._Element] = field(default_factory=list)


@dataclass
class TransformStats:
    counts: Counter = field(default_factory=Counter)

    def add(self, key: str, amount: int = 1) -> None:
        self.counts[key] += amount

    def as_dict(self) -> dict[str, int]:
        return dict(sorted(self.counts.items()))


def split_qname(element: etree._Element) -> tuple[str | None, str]:
    name = etree.QName(element)
    return name.namespace, name.localname


def is_iur3(namespace: str | None) -> bool:
    return bool(namespace and re.fullmatch(r"https://www\.geospatial\.jp/iur/uro/3\.\d+", namespace))


def _scan_iur3_usage(source: Path) -> None:
    """Reject every used i-UR 3 QName outside the explicitly supported profile."""

    iur_namespace = re.compile(
        r"https://www\.geospatial\.jp/iur/(?P<module>[a-z]+)/3\.\d+"
    )
    context = safe_iterparse(str(source), events=("end",), huge_tree=True)
    for _event, element in context:
        if isinstance(element.tag, str):
            namespace, local = split_qname(element)
            match = iur_namespace.fullmatch(namespace or "")
            if match and (
                match.group("module") != "uro" or local not in SUPPORTED_IUR3_LOCALS
            ):
                raise ConversionError(f"unsupported used i-UR 3 QName: {element.tag}")
        element.clear()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _one_inner(wrapper: etree._Element, expected: str | set[str]) -> etree._Element:
    children = [child for child in wrapper if isinstance(child.tag, str)]
    if len(children) != 1:
        raise ConversionError(
            f"{etree.QName(wrapper).localname} must contain exactly one ADE object"
        )
    namespace, local = split_qname(children[0])
    allowed = {expected} if isinstance(expected, str) else expected
    if not is_iur3(namespace) or local not in allowed:
        raise ConversionError(
            f"unsupported ADE object in {etree.QName(wrapper).localname}: {children[0].tag}"
        )
    return children[0]


def _check_children(element: etree._Element, allowed: set[str]) -> None:
    for child in element:
        if not isinstance(child.tag, str):
            continue
        namespace, local = split_qname(child)
        if not is_iur3(namespace) or local not in allowed:
            raise ConversionError(
                f"unsupported child in {etree.QName(element).localname}: {child.tag}"
            )


def read_source_records(source: Path) -> tuple[dict[str, SourceRecord], dict[str, int]]:
    """Read and validate the supported i-UR 3 building profile."""

    _scan_iur3_usage(source)
    records: dict[str, SourceRecord] = {}
    wrapper_counts: Counter = Counter()
    context = safe_iterparse(
        str(source), events=("end",), tag=f"{{{BLDG2}}}Building", huge_tree=True
    )
    for _event, building in context:
        gml_id = building.get(f"{{{GML31}}}id")
        if not gml_id:
            raise ConversionError("source bldg:Building without gml:id")
        if gml_id in records:
            raise ConversionError(f"duplicate source gml:id: {gml_id}")

        wrappers: list[etree._Element] = []
        building_id: str | None = None
        dq_count = 0
        for child in building:
            namespace, local = split_qname(child)
            if not is_iur3(namespace):
                continue
            if local not in SUPPORTED_WRAPPERS:
                raise ConversionError(f"unsupported i-UR 3 building wrapper: {child.tag}")
            inner = _one_inner(child, SUPPORTED_WRAPPERS[local])
            inner_local = etree.QName(inner).localname
            if inner_local == "BuildingIDAttribute":
                _check_children(inner, BUILDING_ID_CHILDREN)
                value = inner.findtext(f"{{{namespace}}}buildingID")
                building_id = value.strip() if value else None
            elif inner_local == "BuildingDetailAttribute":
                _check_children(inner, BUILDING_DETAIL_CHILDREN)
            elif inner_local == "BuildingDataQualityAttribute":
                _check_children(inner, DATA_QUALITY_CHILDREN)
                dq_count += 1
            elif inner_local in {
                "BuildingRiverFloodingRiskAttribute",
                "BuildingHighTideRiskAttribute",
            }:
                _check_children(inner, RISK_CHILDREN)
            elif inner_local == "KeyValuePairAttribute":
                _check_children(inner, KVP_CHILDREN)
            wrappers.append(copy.deepcopy(child))
            wrapper_counts[inner_local] += 1

        if dq_count > 1:
            raise ConversionError(
                f"{gml_id} has {dq_count} legacy data-quality groups; only one is supported"
            )
        records[gml_id] = SourceRecord(gml_id, building_id, wrappers)
        building.clear()
        while building.getprevious() is not None:
            del building.getparent()[0]

    if not records:
        raise ConversionError("no CityGML 2.0 bldg:Building elements found")
    return records, dict(sorted(wrapper_counts.items()))


def _retag_tree(element: etree._Element, namespace: str) -> etree._Element:
    clone = copy.deepcopy(element)
    for node in clone.iter():
        if isinstance(node.tag, str) and is_iur3(etree.QName(node).namespace):
            node.tag = str(etree.QName(namespace, etree.QName(node).localname))
    return clone


def _legacy_kvp(
    key_name: str,
    value: etree._Element,
    key_codespace: str,
) -> etree._Element:
    obj = etree.Element(etree.QName(URC4, "KeyValuePairAttribute"))
    key = etree.SubElement(obj, etree.QName(URC4, "key"))
    key.set("codeSpace", key_codespace)
    key.text = key_name
    code_value = _retag_tree(value, URC4)
    code_value.tag = str(etree.QName(URC4, "codeValue"))
    obj.append(code_value)
    return obj


def _core_ade(obj: etree._Element) -> etree._Element:
    wrapper = etree.Element(etree.QName(CORE3, "adeOfAbstractCityObject"))
    wrapper.append(obj)
    return wrapper


def _building_ade(obj: etree._Element) -> etree._Element:
    wrapper = etree.Element(etree.QName(BLDG3, "adeOfAbstractBuilding"))
    wrapper.append(obj)
    return wrapper


def _gYear_to_date(value: str) -> str:
    match = GYEAR.fullmatch(value.strip())
    if not match:
        raise ConversionError(f"surveyYear is not an xs:gYear lexical value: {value!r}")
    return f"{match.group('year')}-01-01{match.group('tz') or ''}"


def transform_record(
    record: SourceRecord,
    stats: TransformStats,
    key_codespace: str = DEFAULT_COMPAT_KEY_CODESPACE,
) -> tuple[list[etree._Element], list[etree._Element], bool]:
    """Return core ADE wrappers, building ADE wrappers, and survey marker need."""

    core_ades: list[etree._Element] = []
    building_ades: list[etree._Element] = []
    used_year_encoding = False

    for wrapper in record.wrappers:
        local = etree.QName(wrapper).localname
        inner = _one_inner(wrapper, SUPPORTED_WRAPPERS[local])
        inner_local = etree.QName(inner).localname

        if inner_local == "BuildingIDAttribute":
            building_ades.append(_building_ade(_retag_tree(inner, URO4)))
            stats.add("official.BuildingIDAttribute")
        elif inner_local == "BuildingDetailAttribute":
            target = etree.Element(etree.QName(URO4, "BuildingDetailAttribute"))
            for child in inner:
                child_local = etree.QName(child).localname
                if child_local in LEGACY_DETAIL_CODES:
                    key = f"iur3.uro.BuildingDetailAttribute.{child_local}"
                    core_ades.append(_core_ade(_legacy_kvp(key, child, key_codespace)))
                    stats.add("legacy-preserved.BuildingDetailAttribute.detailedUsage")
                    continue
                clone = _retag_tree(child, URO4)
                if child_local == "surveyYear":
                    clone.text = _gYear_to_date(child.text or "")
                    used_year_encoding = True
                    stats.add("i4d-required.surveyYear")
                else:
                    stats.add(f"official.BuildingDetailAttribute.{child_local}")
                target.append(clone)
            building_ades.append(_building_ade(target))
            stats.add("official.BuildingDetailAttribute")
        elif inner_local == "BuildingDataQualityAttribute":
            for child in inner:
                child_local = etree.QName(child).localname
                key = f"iur3.uro.BuildingDataQualityAttribute.{child_local}"
                core_ades.append(_core_ade(_legacy_kvp(key, child, key_codespace)))
                stats.add("legacy-preserved.BuildingDataQualityAttribute")
        elif inner_local == "KeyValuePairAttribute":
            core_ades.append(_core_ade(_retag_tree(inner, URC4)))
            stats.add("official.KeyValuePairAttribute")
        elif inner_local in {
            "BuildingRiverFloodingRiskAttribute",
            "BuildingHighTideRiskAttribute",
        }:
            name = inner_local.removeprefix("Building")
            target = _retag_tree(inner, URC4)
            target.tag = str(etree.QName(URC4, name))
            core_ades.append(_core_ade(target))
            stats.add(f"official.{name}")
        else:  # protected by preflight; kept for defense in depth
            raise ConversionError(f"unsupported ADE object: {inner_local}")

    return core_ades, building_ades, used_year_encoding


DERIVED_CITY_OBJECT_PROPERTIES = {
    "spaceType",
    "volume",
    "boundary",
    "lod0Point",
    "lod0MultiSurface",
    "lod0MultiCurve",
    "lod1Solid",
    "lod2Solid",
    "lod3Solid",
    "height",
    "class",
    "function",
    "usage",
    "roofType",
    "storeysAboveGround",
    "storeysBelowGround",
    "storeyHeightsAboveGround",
    "storeyHeightsBelowGround",
    "buildingConstructiveElement",
    "buildingInstallation",
    "buildingRoom",
    "buildingFurniture",
    "buildingSubdivision",
    "address",
}


def _insert_core_ades(building: etree._Element, wrappers: list[etree._Element]) -> None:
    children = list(building)
    index = len(children)
    for position, child in enumerate(children):
        namespace, local = split_qname(child)
        if namespace in {CON3, BLDG3} or local in DERIVED_CITY_OBJECT_PROPERTIES:
            index = position
            break
    for wrapper in wrappers:
        building.insert(index, wrapper)
        index += 1


def _root_with_namespaces(
    tree: etree._ElementTree, i4dur_namespace: str
) -> etree._Element:
    old = tree.getroot()
    nsmap = dict(old.nsmap)
    nsmap.update({"uro": URO4, "urc": URC4, "i4dur": i4dur_namespace})
    new = etree.Element(old.tag, nsmap=nsmap)
    new.text = old.text
    new.tail = old.tail
    for name, value in old.attrib.items():
        new.set(name, value)
    for child in list(old):
        new.append(child)
    tree._setroot(new)
    return new


def _add_schema_locations(root: etree._Element, i4dur_namespace: str, i4dur_xsd: str) -> None:
    attr = etree.QName(XSI, "schemaLocation")
    tokens = root.get(attr, "").split()
    pairs = list(zip(tokens[0::2], tokens[1::2]))
    replacements = {
        URO4: "https://www.geospatial.jp/iur/schemas/uro/4.0/urbanObject.xsd",
        URC4: "https://www.geospatial.jp/iur/schemas/urc/4.0/urbanCore.xsd",
        i4dur_namespace: i4dur_xsd,
    }
    present = {namespace for namespace, _location in pairs}
    for namespace, location in replacements.items():
        if namespace not in present:
            pairs.append((namespace, location))
    root.set(attr, " ".join(value for pair in pairs for value in pair))


def _source_appearance_ids(source: Path) -> set[str]:
    result: set[str] = set()
    context = safe_iterparse(str(source), events=("end",), huge_tree=True)
    for _event, element in context:
        if isinstance(element.tag, str) and etree.QName(element).localname == "Appearance":
            value = element.get(etree.QName(GML31, "id"))
            if value:
                result.add(value)
        element.clear()
    return result


def _stabilize_generated_appearance_ids(root: etree._Element, source: Path) -> int:
    """Replace citygml-tools-generated Appearance IDs with content-derived IDs."""

    source_ids = _source_appearance_ids(source)
    gml_id = etree.QName(GML32, "id")
    mappings: dict[str, str] = {}
    occurrences: Counter = Counter()
    used_ids = {
        value
        for element in root.iter()
        for name, value in element.attrib.items()
        if etree.QName(name).namespace == GML32 and etree.QName(name).localname == "id"
    }

    for appearance in root.iter(etree.QName(APP3, "Appearance")):
        old_id = appearance.get(gml_id)
        if not old_id or old_id in source_ids:
            continue
        del appearance.attrib[gml_id]
        canonical = etree.tostring(appearance, method="c14n", exclusive=True)
        digest = hashlib.sha256(canonical).hexdigest()
        occurrence = occurrences[digest]
        occurrences[digest] += 1
        while True:
            seed = f"{digest}:{occurrence}".encode("ascii")
            candidate = f"ID_{uuid.UUID(bytes=hashlib.sha256(seed).digest()[:16])}"
            if candidate not in used_ids or candidate == old_id:
                break
            occurrence += 1
        appearance.set(gml_id, candidate)
        used_ids.discard(old_id)
        used_ids.add(candidate)
        mappings[old_id] = candidate

    if mappings:
        href = etree.QName(XLINK, "href")
        for element in root.iter():
            value = element.get(href)
            if value and value.startswith("#") and value[1:] in mappings:
                element.set(href, f"#{mappings[value[1:]]}")
    return len(mappings)


def restore_ade(
    source: Path,
    core_target: Path,
    output: Path,
    *,
    i4dur_namespace: str = DEFAULT_I4DUR_NS,
    i4dur_xsd: str = DEFAULT_I4DUR_XSD,
    key_codespace: str = DEFAULT_COMPAT_KEY_CODESPACE,
) -> dict:
    records, wrapper_counts = read_source_records(source)
    source_building_ids = [record.building_id for record in records.values()]
    if any(value is None for value in source_building_ids):
        raise ConversionError("at least one source building has no uro:buildingID")
    if len(source_building_ids) != len(set(source_building_ids)):
        raise ConversionError("duplicate uro:buildingID in source")

    parser = safe_parser(remove_blank_text=False, huge_tree=True)
    tree = etree.parse(str(core_target), parser)
    root = _root_with_namespaces(tree, i4dur_namespace)

    targets: dict[str, etree._Element] = {}
    for building in root.iter(etree.QName(BLDG3, "Building")):
        gml_id = building.get(etree.QName(GML32, "id"))
        if not gml_id:
            raise ConversionError("target bldg:Building without gml:id")
        if gml_id in targets:
            raise ConversionError(f"duplicate target gml:id: {gml_id}")
        targets[gml_id] = building

    source_ids = set(records)
    target_ids = set(targets)
    if source_ids != target_ids:
        missing = sorted(source_ids - target_ids)[:5]
        extra = sorted(target_ids - source_ids)[:5]
        raise ConversionError(
            f"source/target building gml:id sets differ; missing={missing}, extra={extra}"
        )

    stats = TransformStats()
    year_marker_needed = False
    for gml_id in sorted(records):
        core_ades, building_ades, used_year = transform_record(
            records[gml_id], stats, key_codespace
        )
        _insert_core_ades(targets[gml_id], core_ades)
        for wrapper in building_ades:
            targets[gml_id].append(wrapper)
        year_marker_needed |= used_year

    _add_schema_locations(root, i4dur_namespace, i4dur_xsd)
    if year_marker_needed:
        wrapper = etree.SubElement(root, etree.QName(CORE3, "adeOfCityModel"))
        etree.SubElement(wrapper, etree.QName(i4dur_namespace, "SurveyYearEncoding"))

    stabilized_appearance_ids = _stabilize_generated_appearance_ids(root, source)

    # Drop the copied i-UR 3 namespace declarations after every QName has been
    # retagged.  Keeping them would make a visually identical `uro` prefix
    # resolve differently on nested elements.
    etree.cleanup_namespaces(root)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", dir=output.parent, prefix=f".{output.name}.", delete=False
    ) as stream:
        temp_path = Path(stream.name)
        tree.write(stream, encoding="UTF-8", xml_declaration=True, pretty_print=False)
    os.replace(temp_path, output)

    return {
        "converter": {"name": Path(__file__).name, "version": VERSION},
        "source": {
            "path": str(source),
            "sha256": sha256(source),
            "citygml": "2.0",
            "building_count": len(records),
            "building_id_count": len(source_building_ids),
            "ade_object_counts": wrapper_counts,
        },
        "target": {
            "path": str(output),
            "sha256": sha256(output),
            "citygml": "3.0",
            "building_count": len(targets),
            "profile": "i-UR 4.0 + i4d-UR 1.0",
        },
        "rules": stats.as_dict(),
        "i4d_ur": {
            "namespace": i4dur_namespace,
            "namespace_status": "provisional",
            "survey_year_encoding": year_marker_needed,
        },
        "invariants": {
            "gml_id_set_equal": True,
            "building_id_unique": True,
            "generated_appearance_ids_stabilized": stabilized_appearance_ids,
            "unsupported_elements": 0,
        },
    }


def _prepare_external_resource_dirs(source: Path, output_dir: Path) -> None:
    context = safe_iterparse(str(source), events=("end",), huge_tree=True)
    for _event, element in context:
        if not isinstance(element.tag, str):
            continue
        if etree.QName(element).localname == "imageURI" and element.text:
            uri = element.text.strip()
            if uri and "://" not in uri and not uri.startswith("/"):
                (output_dir / Path(uri).parent).mkdir(parents=True, exist_ok=True)
        element.clear()


def run_core_upgrade(source: Path, output: Path, executable: Path) -> tuple[Path, str]:
    if source.resolve() == output.resolve() or source.parent.resolve() == output.parent.resolve():
        raise ConversionError("output must be in a directory separate from the canonical source")
    if output.exists():
        raise ConversionError(f"refusing to overwrite existing output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    _prepare_external_resource_dirs(source, output.parent)

    env = os.environ.copy()
    env.update({"TZ": "UTC", "LC_ALL": "C", "LANG": "C"})
    version = subprocess.run(
        [str(executable), "--version"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()
    subprocess.run(
        [
            str(executable),
            "upgrade",
            "--no-pretty-print",
            "-o",
            str(output.parent),
            str(source),
        ],
        check=True,
        env=env,
    )
    generated = output.parent / source.name
    if not generated.exists():
        raise ConversionError(f"citygml-tools did not create expected file: {generated}")
    return generated, version


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def command_inspect(args: argparse.Namespace) -> int:
    records, counts = read_source_records(args.source)
    building_ids = [record.building_id for record in records.values()]
    result = {
        "source": str(args.source),
        "sha256": sha256(args.source),
        "building_count": len(records),
        "building_id_missing": sum(value is None for value in building_ids),
        "building_id_duplicates": len(building_ids) - len(set(building_ids)),
        "ade_object_counts": counts,
        "unsupported_elements": 0,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def command_restore(args: argparse.Namespace) -> int:
    manifest = restore_ade(
        args.source,
        args.core_target,
        args.output,
        i4dur_namespace=args.i4dur_namespace,
        i4dur_xsd=args.i4dur_xsd,
        key_codespace=args.key_codespace,
    )
    manifest_path = args.manifest or args.output.with_suffix(args.output.suffix + ".conversion.json")
    write_json(manifest_path, manifest)
    print(manifest_path)
    return 0


def command_convert(args: argparse.Namespace) -> int:
    # Preflight before running the external converter, so unsupported ADE never gets skipped.
    read_source_records(args.source)
    generated, tool_version = run_core_upgrade(args.source, args.output, args.citygml_tools)
    with tempfile.NamedTemporaryFile(
        dir=args.output.parent, prefix=f".{args.output.name}.ade.", delete=False
    ) as stream:
        restored = Path(stream.name)
    try:
        manifest = restore_ade(
            args.source,
            generated,
            restored,
            i4dur_namespace=args.i4dur_namespace,
            i4dur_xsd=args.i4dur_xsd,
            key_codespace=args.key_codespace,
        )
        os.replace(restored, args.output)
    finally:
        if restored.exists():
            restored.unlink()
        if generated != args.output and generated.exists():
            generated.unlink()
    manifest["target"]["path"] = str(args.output)
    manifest["target"]["sha256"] = sha256(args.output)
    manifest["core_converter"] = {
        "name": "citygml-tools",
        "version_output": tool_version,
        "timezone": "UTC",
        "command": "upgrade --no-pretty-print",
    }
    manifest_path = args.manifest or args.output.with_suffix(args.output.suffix + ".conversion.json")
    write_json(manifest_path, manifest)
    print(manifest_path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="fail-closed source preflight")
    inspect_parser.add_argument("source", type=Path)
    inspect_parser.set_defaults(func=command_inspect)

    def add_ade_options(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--manifest", type=Path)
        subparser.add_argument("--i4dur-namespace", default=DEFAULT_I4DUR_NS)
        subparser.add_argument("--i4dur-xsd", default=DEFAULT_I4DUR_XSD)
        subparser.add_argument("--key-codespace", default=DEFAULT_COMPAT_KEY_CODESPACE)

    restore_parser = subparsers.add_parser(
        "restore-ade", help="restore supported i-UR ADE into an already upgraded core file"
    )
    restore_parser.add_argument("source", type=Path)
    restore_parser.add_argument("core_target", type=Path)
    restore_parser.add_argument("output", type=Path)
    add_ade_options(restore_parser)
    restore_parser.set_defaults(func=command_restore)

    convert_parser = subparsers.add_parser(
        "convert", help="run citygml-tools upgrade and restore supported i-UR ADE"
    )
    convert_parser.add_argument("source", type=Path)
    convert_parser.add_argument("output", type=Path)
    convert_parser.add_argument("--citygml-tools", required=True, type=Path)
    add_ade_options(convert_parser)
    convert_parser.set_defaults(func=command_convert)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except (ConversionError, etree.XMLSyntaxError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
