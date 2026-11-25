#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Match the buildings of two editions of the same area and measure what changed (generic CityGML).

Matching order is as follows:

1. Same ``gml:id``
2. LOD0 outline 1mm-normalized fingerprint matches uniquely
3. LOD0 outline IoU is mutual max and >= 0.5

Remaining buildings are add/delete candidates; split/merge candidates are also
enumerated separately based on multiple outline overlaps. Coordinates are read in the file's
CRS (its Envelope's srsName, else the city's declared `crs`) and converted to simplified
equirectangular metres around a reference latitude (the city's `map.center`, else the data's
own Envelope), so the comparison works in metres for any city (D-4b5: JGD2011 latitude /
longitude and a Tokyo reference latitude were assumed). A file whose CRS is unknown is refused,
never read as latitude and longitude.

A dialect pack's yearly mesh analysis is its command line; the identity manifests and the
building history use it as a module.

Dependencies: shapely==2.1.2 (lxml through scripts/safe_xml.py)
"""
from __future__ import annotations

import hashlib
import math
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import city_declaration  # noqa: E402
from scripts.citygml_dialect import NS, crs_transformer  # noqa: E402
from scripts.citygml_xml import APPEARANCE_NS, BUILDING_TAGS, lod_of, qname  # noqa: E402
from scripts.safe_xml import element_bytes, safe_iterparse  # noqa: E402
from shapely import make_valid, normalize, set_precision, to_wkb
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

GML_NS = NS["gml"]
# geometry and appearance are not attributes, in every CityGML version
EXCLUDED_ATTRIBUTE_NS = {GML_NS} | APPEARANCE_NS
GML_ID = f"{{{GML_NS}}}id"
EARTH_METRES_PER_DEGREE = 111_320.0


@dataclass(frozen=True)
class Frame:
    """How a file's coordinates become metres: its CRS's transform to (latitude, longitude), the
    reference latitude of the equirectangular projection, and the posList dimension."""
    transform: object            # None: the CRS is unknown, and a coordinate is refused when read
    reference_latitude: float
    dimension: int = 3
    unknown: str = ""            # why the transform is missing

    def point(self, x: float, y: float) -> tuple[float, float]:
        if self.transform is None:
            raise ValueError(self.unknown)
        latitude, longitude = self.transform(x, y)
        return (longitude * EARTH_METRES_PER_DEGREE * math.cos(math.radians(self.reference_latitude)),
                latitude * EARTH_METRES_PER_DEGREE)


_ENVELOPE = re.compile(rb"<(?:\w+:)?Envelope\b([^>]*)>(.*?)</(?:\w+:)?Envelope>", re.S)
_ATTR = re.compile(rb'(srsName|srsDimension)="([^"]*)"')
_CORNER = re.compile(rb"<(?:\w+:)?(lowerCorner|upperCorner)[^>]*>([^<]+)<")


def frame_for(source, crs: "str | None" = None, reference_latitude: "float | None" = None,
              root: "Path | None" = None) -> Frame:
    """The frame of a CityGML file (a path, or its bytes): its Envelope's srsName and
    srsDimension, else the city's declared `crs` (root: the city clone, default the working
    directory); the reference latitude given, else the city's `map.center`, else the Envelope's
    centre rounded to 0.1 degree."""
    if isinstance(source, (bytes, bytearray)):
        head, path = bytes(source[:1 << 16]), "the file"
    else:
        path = source
        with open(source, "rb") as fh:
            head = fh.read(1 << 16)
    m = _ENVELOPE.search(head)
    attrs = dict((k.decode(), v.decode()) for k, v in _ATTR.findall(m.group(1))) if m else {}
    declared = city_declaration.read(root or Path.cwd())
    srs = attrs.get("srsName") or crs or declared.crs or ""
    transform = crs_transformer(srs)
    unknown = f"{path}: the CRS {srs or '(none declared)'} is unknown; the coordinates are not read"
    if transform is None:
        return Frame(None, reference_latitude or 0.0, int(attrs.get("srsDimension") or 3), unknown)
    if reference_latitude is None:
        centre = (declared.raw.get("map") or {}).get("center") if isinstance(declared.raw.get("map"), dict) else None
        if isinstance(centre, list) and len(centre) == 2:
            reference_latitude = float(centre[0])
        else:
            corners = {k.decode(): [float(v) for v in t.split()] for k, t in _CORNER.findall(m.group(2))} if m else {}
            if len(corners) == 2:
                lat_low = transform(*corners["lowerCorner"][:2])[0]
                lat_high = transform(*corners["upperCorner"][:2])[0]
                reference_latitude = round((lat_low + lat_high) / 2, 1)
            else:
                reference_latitude = 0.0
    return Frame(transform, reference_latitude, int(attrs.get("srsDimension") or 3))


# A geographic file without an Envelope, read as the tests' fixtures are (latitude, longitude, height)
LATLON = Frame(crs_transformer("EPSG:4326") or (lambda x, y: (x, y)), 0.0)


@dataclass
class Building:
    id: str
    city_code: str | None
    attrs: dict[str, str]
    lods: tuple[str, ...]
    geom: Polygon | MultiPolygon | None
    fingerprint_1mm: str | None


def local_name(value) -> str:
    """The local name of a tag ('{ns}local') or an element."""
    return (value if isinstance(value, str) else value.tag).rsplit("}", 1)[-1]


def norm_num(token: str) -> str:
    try:
        return format(Decimal(token).normalize(), "f")
    except (InvalidOperation, ValueError):
        return token


def ring_from_poslist(text: str, frame: Frame, dimension: "int | None" = None) -> list[tuple[float, float]]:
    """A posList's ring in metres; its dimension is its own srsDimension, else the frame's
    (D-4b5: a 2D posList was dropped as malformed)."""
    dimension = dimension or frame.dimension
    values = text.split()
    if len(values) < dimension * 4 or len(values) % dimension:
        return []
    points = []
    for offset in range(0, len(values), dimension):
        points.append(frame.point(float(values[offset]), float(values[offset + 1])))
    if points and points[0] != points[-1]:
        points.append(points[0])
    return points


def first_descendant(element: object, name: str) -> object | None:
    return next(
        (
            item
            for item in element.iter()
            if isinstance(item.tag, str) and local_name(item.tag) == name
        ),
        None,
    )


def linear_ring(container: object, frame: Frame) -> list[tuple[float, float]]:
    ring = first_descendant(container, "LinearRing")
    if ring is None:
        return []
    poslist = first_descendant(ring, "posList")
    if poslist is not None and poslist.text:
        own = poslist.get("srsDimension") or poslist.get("dimension")
        return ring_from_poslist(poslist.text, frame, int(own) if own and own.isdigit() else None)
    positions = [
        item
        for item in ring.iter()
        if isinstance(item.tag, str) and local_name(item.tag) == "pos" and item.text
    ]
    points = []
    for position in positions:
        values = position.text.split()
        if len(values) >= 2:
            points.append(frame.point(float(values[0]), float(values[1])))
    if points and points[0] != points[-1]:
        points.append(points[0])
    return points


def polygon_from_element(element: object, frame: Frame) -> Polygon | None:
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
    shell = linear_ring(exterior, frame)
    if len(shell) < 4:
        return None
    holes = []
    for item in element.iter():
        if not isinstance(item.tag, str) or local_name(item.tag) != "interior":
            continue
        hole = linear_ring(item, frame)
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


def lod0_geometry(building: object, frame: Frame = LATLON):
    """The building's footprint: its LOD0 roof edge / footprint, else its GroundSurface (the
    footprint of LoD1-4 data without LOD0, such as Bavarian LoD2)."""
    elements = [item for item in building.iter() if isinstance(item.tag, str)]
    lod0 = ([item for item in elements if local_name(item.tag) in {"lod0RoofEdge", "lod0FootPrint"}]
            or [item for item in elements if local_name(item.tag) == "GroundSurface"])
    polygons = []
    for lod_element in lod0:
        for item in lod_element.iter():
            if isinstance(item.tag, str) and local_name(item.tag) == "Polygon":
                polygon = polygon_from_element(item, frame)
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


def extract_attributes(building: object) -> dict[str, str]:
    collected: dict[str, list[str]] = defaultdict(list)

    def walk(element: object, path: str) -> None:
        for child in element:
            if not isinstance(child.tag, str):
                continue
            namespace, segment = qname(child)
            if namespace in EXCLUDED_ATTRIBUTE_NS:
                continue
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


def extract_lods(building: object) -> tuple[str, ...]:
    lods = set()
    for item in building.iter():
        if not isinstance(item.tag, str):
            continue
        name = local_name(item.tag)
        if name.startswith("lod") and lod_of(name[:4]) is not None:   # a geometry property: lod2Solid -> lod2
            lods.add(name[:4])
    return tuple(sorted(lods))


def extract_municipality(building: object, packs) -> str | None:
    """The building's municipality code: the first of `packs` that finds one in its bytes
    (Pack.municipality, the rule the gates use); None without one."""
    span = element_bytes(building) if packs else b""
    return next((code for pack in packs if (code := pack.municipality(span))), None)


def municipality_packs(pack=None) -> list:
    """The packs that say where a building's municipality is: the given one, else the city's
    declared one, else every pack that has a municipality (a tools checkout or a file read before
    any city declares it)."""
    import dialects
    chosen = pack or city_declaration.read(Path.cwd()).pack
    if chosen.municipality:
        return [chosen]
    return [] if pack else [p for p in dialects.packs() if p.municipality]


def drop_element(building: object) -> None:
    building.clear()
    parent = building.getparent()
    if parent is None:
        return
    parent.clear()
    grandparent = parent.getparent()
    if grandparent is not None:
        while parent.getprevious() is not None:
            del grandparent[0]


def load_buildings(path: Path, frame: "Frame | None" = None, pack=None) -> dict[str, Building]:
    """The buildings of a file by gml:id, read in its frame (frame_for, unless given); `city_code`
    is the municipality under the pack (the city's declared one, unless given)."""
    frame = frame or frame_for(path)
    packs = municipality_packs(pack)
    result = {}
    context = safe_iterparse(str(path), events=("end",), tag=BUILDING_TAGS, huge_tree=True)
    for _event, element in context:
        building_id = element.get(GML_ID)
        if building_id:
            geometry = lod0_geometry(element, frame)
            result[building_id] = Building(
                id=building_id,
                city_code=extract_municipality(element, packs),
                attrs=extract_attributes(element),
                lods=extract_lods(element),
                geom=geometry,
                fingerprint_1mm=geometry_fingerprint(geometry),
            )
        drop_element(element)
    return result


def distribution(values: Iterable[float], digits: int = 6) -> dict[str, float | int | None]:
    """count, min, the 10th / 50th / 90th percentile (linear, as statistics.quantiles'
    "inclusive"; W6: the percentile was computed by hand) and max."""
    data = list(values)
    if not data:
        return {"count": 0, "min": None, "p10": None, "median": None, "p90": None, "max": None}
    deciles = statistics.quantiles(data, n=10, method="inclusive") if len(data) > 1 else [data[0]] * 9
    return {
        "count": len(data),
        "min": round(min(data), digits),
        "p10": round(deciles[0], digits),
        "median": round(statistics.median(data), digits),
        "p90": round(deciles[8], digits),
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
