#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Match buildings across mesh years and measure update volume for the same CityGML mesh.

Matching order is as follows:

1. Same ``gml:id``
2. LOD0 outline 1mm-normalized fingerprint matches uniquely
3. LOD0 outline IoU is mutual max and >= 0.5

Remaining buildings are add/delete candidates; split/merge candidates are also
enumerated separately based on multiple outline overlaps. Coordinates are
assumed to be JGD2011 latitude/longitude/altitude (EPSG:6697) and are converted
to simplified equirectangular coordinates near the target mesh for meter-unit comparison.

Dependencies: lxml==6.1.1, shapely==2.1.2
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from lxml import etree

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.safe_xml import safe_iterparse  # noqa: E402
from shapely import make_valid, normalize, set_precision, to_wkb
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

GML_NS = "http://www.opengis.net/gml"
APP_NS = "http://www.opengis.net/citygml/appearance/2.0"
EXCLUDED_ATTRIBUTE_NS = {GML_NS, APP_NS}
BUILDING_TAGS = (
    "{http://www.opengis.net/citygml/building/2.0}Building",
    "{http://www.opengis.net/citygml/building/1.0}Building",
)
GML_ID = f"{{{GML_NS}}}id"
EARTH_METRES_PER_DEGREE = 111_320.0
REFERENCE_LATITUDE = 35.71


@dataclass
class Building:
    id: str
    city_code: str | None
    attrs: dict[str, str]
    lods: tuple[str, ...]
    geom: Polygon | MultiPolygon | None
    fingerprint_1mm: str | None


def local_name(value: str) -> str:
    return etree.QName(value).localname


def norm_num(token: str) -> str:
    try:
        return format(Decimal(token).normalize(), "f")
    except (InvalidOperation, ValueError):
        return token


def metric_point(latitude: float, longitude: float) -> tuple[float, float]:
    x = longitude * EARTH_METRES_PER_DEGREE * math.cos(math.radians(REFERENCE_LATITUDE))
    y = latitude * EARTH_METRES_PER_DEGREE
    return x, y


def ring_from_poslist(text: str, dimension: int = 3) -> list[tuple[float, float]]:
    values = text.split()
    if len(values) < dimension * 4 or len(values) % dimension:
        return []
    points = []
    for offset in range(0, len(values), dimension):
        latitude = float(values[offset])
        longitude = float(values[offset + 1])
        points.append(metric_point(latitude, longitude))
    if points and points[0] != points[-1]:
        points.append(points[0])
    return points


def first_descendant(element: etree._Element, name: str) -> etree._Element | None:
    return next(
        (
            item
            for item in element.iter()
            if isinstance(item.tag, str) and local_name(item.tag) == name
        ),
        None,
    )


def linear_ring(container: etree._Element) -> list[tuple[float, float]]:
    ring = first_descendant(container, "LinearRing")
    if ring is None:
        return []
    poslist = first_descendant(ring, "posList")
    if poslist is not None and poslist.text:
        return ring_from_poslist(poslist.text)
    positions = [
        item
        for item in ring.iter()
        if isinstance(item.tag, str) and local_name(item.tag) == "pos" and item.text
    ]
    points = []
    for position in positions:
        values = position.text.split()
        if len(values) >= 2:
            points.append(metric_point(float(values[0]), float(values[1])))
    if points and points[0] != points[-1]:
        points.append(points[0])
    return points


def polygon_from_element(element: etree._Element) -> Polygon | None:
    exterior = next(
        (
            item
            for item in element
            if isinstance(item.tag, str) and local_name(item.tag) == "exterior"
        ),
        None,
    )
    if exterior is None:
        exterior = first_descendant(element, "exterior")
    if exterior is None:
        return None
    shell = linear_ring(exterior)
    if len(shell) < 4:
        return None
    holes = []
    for item in element.iter():
        if not isinstance(item.tag, str) or local_name(item.tag) != "interior":
            continue
        hole = linear_ring(item)
        if len(hole) >= 4:
            holes.append(hole)
    polygon = Polygon(shell, holes)
    if polygon.is_empty:
        return None
    if not polygon.is_valid:
        polygon = make_valid(polygon)
    return polygon if not polygon.is_empty else None


def polygonal_only(geometry):
    if geometry is None or geometry.is_empty:
        return None
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return geometry
    if isinstance(geometry, GeometryCollection):
        polygons = [part for part in geometry.geoms if isinstance(part, (Polygon, MultiPolygon))]
        return unary_union(polygons) if polygons else None
    return None


def lod0_geometry(building: etree._Element):
    lod0 = [
        item
        for item in building.iter()
        if isinstance(item.tag, str)
        and local_name(item.tag) in {"lod0RoofEdge", "lod0FootPrint"}
    ]
    polygons = []
    for lod_element in lod0:
        for item in lod_element.iter():
            if isinstance(item.tag, str) and local_name(item.tag) == "Polygon":
                polygon = polygon_from_element(item)
                if polygon is not None:
                    polygons.append(polygon)
    geometry = polygonal_only(unary_union(polygons)) if polygons else None
    if geometry is not None and not geometry.is_valid:
        geometry = polygonal_only(make_valid(geometry))
    return geometry


def geometry_fingerprint(geometry) -> str | None:
    if geometry is None or geometry.is_empty:
        return None
    rounded = set_precision(geometry, 0.001)
    canonical = normalize(rounded)
    return hashlib.sha256(to_wkb(canonical, byte_order=1)).hexdigest()


def extract_attributes(building: etree._Element) -> dict[str, str]:
    collected: dict[str, list[str]] = defaultdict(list)

    def walk(element: etree._Element, path: str) -> None:
        for child in element:
            if not isinstance(child.tag, str):
                continue
            qname = etree.QName(child)
            if qname.namespace in EXCLUDED_ATTRIBUTE_NS:
                continue
            segment = qname.localname
            if child.get("name"):
                segment += f"[@name={child.get('name')}]"
            child_path = f"{path}/{segment}"
            if len(child) == 0:
                value = (child.text or "").strip()
                if value:
                    collected[child_path].append(norm_num(value))
            else:
                walk(child, child_path)

    walk(building, "")
    result = {}
    for path, values in collected.items():
        if len(values) == 1:
            result[path] = values[0]
        else:
            for index, value in enumerate(values):
                result[f"{path}[{index}]"] = value
    return result


def extract_lods(building: etree._Element) -> tuple[str, ...]:
    lods = set()
    for item in building.iter():
        if not isinstance(item.tag, str):
            continue
        name = local_name(item.tag)
        if len(name) >= 4 and name[:3] == "lod" and name[3].isdigit():
            lods.add(name[:4])
    return tuple(sorted(lods))


def extract_city_code(building: etree._Element) -> str | None:
    for item in building.iter():
        if not isinstance(item.tag, str) or local_name(item.tag) != "city":
            continue
        value = (item.text or "").strip()
        if len(value) == 5 and value.isdigit():
            return value
    return None


def drop_element(building: etree._Element) -> None:
    building.clear()
    parent = building.getparent()
    if parent is None:
        return
    parent.clear()
    grandparent = parent.getparent()
    if grandparent is not None:
        while parent.getprevious() is not None:
            del grandparent[0]


def load_buildings(path: Path) -> dict[str, Building]:
    result = {}
    context = safe_iterparse(str(path), events=("end",), tag=BUILDING_TAGS, huge_tree=True)
    for _event, element in context:
        building_id = element.get(GML_ID)
        if building_id:
            geometry = lod0_geometry(element)
            result[building_id] = Building(
                id=building_id,
                city_code=extract_city_code(element),
                attrs=extract_attributes(element),
                lods=extract_lods(element),
                geom=geometry,
                fingerprint_1mm=geometry_fingerprint(geometry),
            )
        drop_element(element)
    return result


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def distribution(values: Iterable[float], digits: int = 6) -> dict[str, float | int | None]:
    data = list(values)
    if not data:
        return {"count": 0, "min": None, "p10": None, "median": None, "p90": None, "max": None}
    return {
        "count": len(data),
        "min": round(min(data), digits),
        "p10": round(percentile(data, 0.10), digits),
        "median": round(statistics.median(data), digits),
        "p90": round(percentile(data, 0.90), digits),
        "max": round(max(data), digits),
    }


def geometry_metrics(old: Building, new: Building) -> tuple[float | None, float | None, float | None]:
    if old.geom is None or new.geom is None:
        return None, None, None
    intersection = old.geom.intersection(new.geom).area
    union = old.geom.union(new.geom).area
    iou = intersection / union if union else None
    hausdorff = old.geom.hausdorff_distance(new.geom)
    centroid_distance = old.geom.centroid.distance(new.geom.centroid)
    return iou, hausdorff, centroid_distance


def unique_fingerprint_pairs(
    old: dict[str, Building],
    new: dict[str, Building],
    old_ids: set[str],
    new_ids: set[str],
) -> list[tuple[str, str]]:
    old_by_hash: dict[str, list[str]] = defaultdict(list)
    new_by_hash: dict[str, list[str]] = defaultdict(list)
    for building_id in old_ids:
        fingerprint = old[building_id].fingerprint_1mm
        if fingerprint:
            old_by_hash[fingerprint].append(building_id)
    for building_id in new_ids:
        fingerprint = new[building_id].fingerprint_1mm
        if fingerprint:
            new_by_hash[fingerprint].append(building_id)
    return [
        (old_ids_for_hash[0], new_by_hash[fingerprint][0])
        for fingerprint, old_ids_for_hash in old_by_hash.items()
        if len(old_ids_for_hash) == 1 and len(new_by_hash.get(fingerprint, [])) == 1
    ]


def mutual_iou_pairs(
    old: dict[str, Building],
    new: dict[str, Building],
    old_ids: set[str],
    new_ids: set[str],
    minimum_iou: float,
    search_padding: float,
) -> list[tuple[str, str, float]]:
    usable_new_ids = [building_id for building_id in new_ids if new[building_id].geom is not None]
    if not usable_new_ids:
        return []
    new_geometries = [new[building_id].geom for building_id in usable_new_ids]
    tree = STRtree(new_geometries)
    candidates: dict[tuple[str, str], float] = {}
    for old_id in old_ids:
        old_geometry = old[old_id].geom
        if old_geometry is None:
            continue
        for index in tree.query(old_geometry.buffer(search_padding)):
            new_id = usable_new_ids[int(index)]
            new_geometry = new[new_id].geom
            intersection = old_geometry.intersection(new_geometry).area
            if intersection <= 0:
                continue
            union = old_geometry.union(new_geometry).area
            if union:
                candidates[(old_id, new_id)] = intersection / union
    best_new_for_old: dict[str, tuple[str, float]] = {}
    best_old_for_new: dict[str, tuple[str, float]] = {}
    for (old_id, new_id), iou in candidates.items():
        if iou > best_new_for_old.get(old_id, ("", -1.0))[1]:
            best_new_for_old[old_id] = (new_id, iou)
        if iou > best_old_for_new.get(new_id, ("", -1.0))[1]:
            best_old_for_new[new_id] = (old_id, iou)
    result = []
    for old_id, (new_id, iou) in best_new_for_old.items():
        if iou < minimum_iou:
            continue
        if best_old_for_new.get(new_id, (None,))[0] == old_id:
            result.append((old_id, new_id, iou))
    return result


def overlap_events(
    old: dict[str, Building],
    new: dict[str, Building],
    old_ids: set[str],
    new_ids: set[str],
    minimum_piece_fraction: float = 0.10,
    minimum_coverage: float = 0.50,
) -> tuple[list[dict], list[dict]]:
    usable_new_ids = [building_id for building_id in new_ids if new[building_id].geom is not None]
    new_tree = STRtree([new[building_id].geom for building_id in usable_new_ids]) if usable_new_ids else None
    splits = []
    if new_tree is not None:
        for old_id in old_ids:
            old_geometry = old[old_id].geom
            if old_geometry is None or old_geometry.area <= 0:
                continue
            pieces = []
            intersections = []
            for index in new_tree.query(old_geometry):
                new_id = usable_new_ids[int(index)]
                intersection = old_geometry.intersection(new[new_id].geom)
                fraction = intersection.area / old_geometry.area
                if fraction >= minimum_piece_fraction:
                    pieces.append(new_id)
                    intersections.append(intersection)
            coverage = unary_union(intersections).area / old_geometry.area if intersections else 0.0
            if len(pieces) >= 2 and coverage >= minimum_coverage:
                splits.append({"old_id": old_id, "new_ids": sorted(pieces), "coverage": round(coverage, 6)})

    usable_old_ids = [building_id for building_id in old_ids if old[building_id].geom is not None]
    old_tree = STRtree([old[building_id].geom for building_id in usable_old_ids]) if usable_old_ids else None
    merges = []
    if old_tree is not None:
        for new_id in new_ids:
            new_geometry = new[new_id].geom
            if new_geometry is None or new_geometry.area <= 0:
                continue
            pieces = []
            intersections = []
            for index in old_tree.query(new_geometry):
                old_id = usable_old_ids[int(index)]
                intersection = new_geometry.intersection(old[old_id].geom)
                fraction = intersection.area / new_geometry.area
                if fraction >= minimum_piece_fraction:
                    pieces.append(old_id)
                    intersections.append(intersection)
            coverage = unary_union(intersections).area / new_geometry.area if intersections else 0.0
            if len(pieces) >= 2 and coverage >= minimum_coverage:
                merges.append({"old_ids": sorted(pieces), "new_id": new_id, "coverage": round(coverage, 6)})
    return splits, merges


def compare(
    old: dict[str, Building],
    new: dict[str, Building],
    minimum_iou: float,
    search_padding: float,
) -> dict:
    # For splits/merges, candidates are taken from all footprints before one-to-one
    # matches are excluded, so we do not miss a half that became a one-to-one candidate at IoU=0.5.
    splits, merges = overlap_events(old, new, set(old), set(new))
    unmatched_old = set(old)
    unmatched_new = set(new)
    matches: list[tuple[str, str, str]] = []

    for building_id in sorted(unmatched_old & unmatched_new):
        matches.append((building_id, building_id, "same_id"))
    unmatched_old -= {old_id for old_id, _new_id, _method in matches}
    unmatched_new -= {new_id for _old_id, new_id, _method in matches}

    fingerprint_matches = unique_fingerprint_pairs(old, new, unmatched_old, unmatched_new)
    for old_id, new_id in fingerprint_matches:
        matches.append((old_id, new_id, "lod0_fingerprint_1mm"))
    unmatched_old -= {old_id for old_id, _new_id in fingerprint_matches}
    unmatched_new -= {new_id for _old_id, new_id in fingerprint_matches}

    iou_matches = mutual_iou_pairs(
        old, new, unmatched_old, unmatched_new, minimum_iou, search_padding
    )
    for old_id, new_id, _iou in iou_matches:
        matches.append((old_id, new_id, "mutual_best_iou"))
    unmatched_old -= {old_id for old_id, _new_id, _iou in iou_matches}
    unmatched_new -= {new_id for _old_id, new_id, _iou in iou_matches}

    records = []
    path_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    ious = []
    hausdorffs = []
    centroid_distances = []
    changed_records = 0
    attribute_changed_records = 0
    lod_changed_records = 0
    geometry_changed_records = 0
    id_changed_records = 0
    city_code_changed_records = 0
    city_code_transitions: Counter[str] = Counter()
    exact_1mm_records = 0
    within_1cm_records = 0
    within_10cm_records = 0

    for old_id, new_id, method in sorted(matches):
        old_building = old[old_id]
        new_building = new[new_id]
        iou, hausdorff, centroid_distance = geometry_metrics(old_building, new_building)
        changed_paths = sorted(
            path
            for path in set(old_building.attrs) | set(new_building.attrs)
            if old_building.attrs.get(path) != new_building.attrs.get(path)
        )
        path_counts.update(changed_paths)
        geometry_changed = old_building.fingerprint_1mm != new_building.fingerprint_1mm
        attribute_changed = bool(changed_paths)
        lod_changed = old_building.lods != new_building.lods
        id_changed = old_id != new_id
        city_code_changed = old_building.city_code != new_building.city_code
        changed = geometry_changed or attribute_changed or lod_changed or id_changed
        changed_records += int(changed)
        attribute_changed_records += int(attribute_changed)
        lod_changed_records += int(lod_changed)
        geometry_changed_records += int(geometry_changed)
        id_changed_records += int(id_changed)
        city_code_changed_records += int(city_code_changed)
        city_code_transitions[f"{old_building.city_code or 'unknown'}->{new_building.city_code or 'unknown'}"] += 1
        exact_1mm_records += int(not geometry_changed)
        if iou is not None:
            ious.append(iou)
        if hausdorff is not None:
            hausdorffs.append(hausdorff)
            within_1cm_records += int(hausdorff <= 0.01)
            within_10cm_records += int(hausdorff <= 0.10)
        if centroid_distance is not None:
            centroid_distances.append(centroid_distance)
        method_counts[method] += 1
        records.append(
            {
                "old_id": old_id,
                "new_id": new_id,
                "method": method,
                "iou": round(iou, 8) if iou is not None else None,
                "hausdorff_m": round(hausdorff, 6) if hausdorff is not None else None,
                "centroid_distance_m": round(centroid_distance, 6) if centroid_distance is not None else None,
                "geometry_equal_1mm": not geometry_changed,
                "attribute_changed": attribute_changed,
                "lod_changed": lod_changed,
                "id_changed": id_changed,
                "old_city_code": old_building.city_code,
                "new_city_code": new_building.city_code,
                "city_code_changed": city_code_changed,
                "changed_paths": changed_paths,
            }
        )

    candidate_records = changed_records + len(unmatched_old) + len(unmatched_new)
    return {
        "summary": {
            "old_buildings": len(old),
            "new_buildings": len(new),
            "matched_one_to_one": len(matches),
            "match_methods": dict(sorted(method_counts.items())),
            "unmatched_old": len(unmatched_old),
            "unmatched_new": len(unmatched_new),
            "split_candidates": len(splits),
            "merge_candidates": len(merges),
            "matched_changed": changed_records,
            "matched_unchanged": len(matches) - changed_records,
            "id_changed": id_changed_records,
            "city_code_changed": city_code_changed_records,
            "city_code_transitions": dict(sorted(city_code_transitions.items())),
            "lod0_geometry_changed": geometry_changed_records,
            "attributes_changed": attribute_changed_records,
            "lod_presence_changed": lod_changed_records,
            "lod0_equal_1mm": exact_1mm_records,
            "lod0_hausdorff_within_1cm": within_1cm_records,
            "lod0_hausdorff_within_10cm": within_10cm_records,
            "one_building_pr_candidate_records": candidate_records,
        },
        "distributions": {
            "iou": distribution(ious, 8),
            "hausdorff_m": distribution(hausdorffs, 6),
            "centroid_distance_m": distribution(centroid_distances, 6),
        },
        "top_changed_attribute_paths": [
            {"path": path, "count": count} for path, count in path_counts.most_common(30)
        ],
        "unmatched_old_ids": sorted(unmatched_old),
        "unmatched_new_ids": sorted(unmatched_new),
        "split_candidate_details": splits,
        "merge_candidate_details": merges,
        "matches": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_gml", type=Path)
    parser.add_argument("new_gml", type=Path)
    parser.add_argument("--old-label", required=True)
    parser.add_argument("--new-label", required=True)
    parser.add_argument("--minimum-iou", type=float, default=0.5)
    parser.add_argument("--search-padding-m", type=float, default=2.0)
    parser.add_argument("--city-code", help="compare buildings of this municipality code only")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    old_all = load_buildings(args.old_gml)
    new_all = load_buildings(args.new_gml)
    old_city_counts = Counter(building.city_code or "unknown" for building in old_all.values())
    new_city_counts = Counter(building.city_code or "unknown" for building in new_all.values())
    if args.city_code:
        old = {key: value for key, value in old_all.items() if value.city_code == args.city_code}
        new = {key: value for key, value in new_all.items() if value.city_code == args.city_code}
    else:
        old = old_all
        new = new_all
    result = {
        "old": {"label": args.old_label, "path": str(args.old_gml)},
        "new": {"label": args.new_label, "path": str(args.new_gml)},
        "parameters": {
            "matching_order": ["same_id", "lod0_fingerprint_1mm", "mutual_best_iou"],
            "minimum_iou": args.minimum_iou,
            "search_padding_m": args.search_padding_m,
            "fingerprint_grid_m": 0.001,
            "reference_latitude": REFERENCE_LATITUDE,
            "city_code_filter": args.city_code,
        },
        "source_scope": {
            "old_all_buildings": len(old_all),
            "new_all_buildings": len(new_all),
            "old_city_counts": dict(sorted(old_city_counts.items())),
            "new_city_counts": dict(sorted(new_city_counts.items())),
        },
        **compare(old, new, args.minimum_iou, args.search_padding_m),
    }
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
