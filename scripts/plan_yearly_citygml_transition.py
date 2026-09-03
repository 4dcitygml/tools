#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Pre-sort yearly CityGML comparison results into attribute-line PRs with semantically equivalent changes.

Takes JSON output from ``analyze_yearly_citygml_mesh.py`` and, counting change dimensions per building,
creates PR candidates by attribute lines such as floor count, use, zone, and source quality.
While a single commit handles only one buildingID, the same buildingID may appear across multiple attribute PRs.

Combinations of normalized change paths are not used for PR splitting; instead, output after all attribute PRs
are complete as a release gate to verify consistency with the official annual version. Shape, LOD, and source ID
are also created as separate candidates apart from attributes.

Add/remove/split/merge candidates are not mixed into one-to-one correspondence groups but are collected separately
as lifecycle-requiring review. This output is not a finalized PR plan but a reproducible pre-sort before humans
verify codelist semantics, address equivalence, rebuilding, etc.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path


INDEX_SUFFIX = re.compile(r"\[\d+\]$")
TRANSITION = re.compile(r"(?P<old>\d{4})-(?P<new>\d{4})")
SOURCE_EVIDENCE_ONLY_FAMILIES = {"source_identity"}


def base_path(path: str) -> str:
    """Strip the trailing index of repeated elements, folding paths with the same meaning together."""

    return INDEX_SUFFIX.sub("", path)


def is_source_metadata(path: str) -> bool:
    """Return whether the path is building data-quality / provenance metadata."""

    return "dataqualityattribute" in path.lower()


def is_address(path: str) -> bool:
    return path.startswith("/address/")


def transition_name(path: Path) -> str:
    match = TRANSITION.search(path.stem)
    return f"{match.group('old')}->{match.group('new')}" if match else path.stem


def signature_id(paths: tuple[str, ...]) -> str:
    """Return a stable identifier for the same path set even when display order or counts change."""

    digest = hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()
    return f"paths-{digest[:12]}"


def pr_counts_with_pilots(family_counts: list[int]) -> tuple[int, int]:
    """Return the basic PR count of change families and the execution PR count with single-building pilots split out."""

    positive = [count for count in family_counts if count > 0]
    basic = len(positive)
    with_pilots = sum(1 if count == 1 else 2 for count in positive)
    return basic, with_pilots


def attribute_family(path: str) -> str:
    """Assign a changed path to the attribute family of a yearly PR narrowed to a single meaning."""

    lower = path.lower()
    if is_address(path):
        return "address"
    if "lodtype" in lower or "lod1heighttype" in lower:
        return "lod_quality"
    if is_source_metadata(path):
        return "source_quality"
    if "disasterriskattribute" in lower:
        return "disaster_risk"
    if path in {"/storeysAboveGround", "/storeysBelowGround"}:
        return "storeys"
    if (
        path in {"/class", "/usage"}
        or "detailedUsage" in path
        or "landUseType" in path
    ):
        return "usage_class_landuse"
    if (
        any(
            name in path
            for name in (
                "areaClassificationType",
                "districtsAndZonesType",
                "specifiedBuildingCoverageRate",
                "specifiedFloorAreaRate",
                "urbanPlanType",
                "[@name=地区計画]",
            )
        )
    ):
        return "planning_zoning_rates"
    if any(
        name in path
        for name in (
            "creationDate",
            "measuredHeight",
            "yearOfConstruction",
            "buildingRoofEdgeArea",
            "buildingFootprintArea",
            "totalFloorArea",
            "fireproofStructureType",
            "buildingStructureType",
            "surveyYear",
        )
    ):
        return "survey_building_detail"
    if "buildingIDAttribute/" in path:
        return "source_identity"
    if "stringAttribute" in path or "KeyValuePairAttribute" in path:
        return "generic_attributes"
    return "other_attributes"


def classify_match(match: dict, geometry_tolerance_m: float) -> tuple[str, dict]:
    paths = sorted({base_path(path) for path in match.get("changed_paths", [])})
    source_metadata = any(is_source_metadata(path) for path in paths)
    address = any(is_address(path) for path in paths)
    residual_paths = [
        path for path in paths if not is_source_metadata(path) and not is_address(path)
    ]
    geometry_over_tolerance = (match.get("hausdorff_m") or 0.0) > geometry_tolerance_m
    geometry_fingerprint_changed = not match.get("geometry_equal_1mm", True)
    lod = bool(match.get("lod_changed"))

    if geometry_over_tolerance or lod:
        lane = "geometry_or_lod_review"
    elif residual_paths:
        lane = "attribute_review"
    elif source_metadata:
        lane = "source_metadata_review"
    elif address:
        lane = "representation_review"
    else:
        lane = "unchanged"

    dimensions = {
        "source_metadata": source_metadata,
        "address": address,
        "attribute_residual": bool(residual_paths),
        "geometry_over_tolerance": geometry_over_tolerance,
        "geometry_fingerprint_changed_1mm": geometry_fingerprint_changed,
        "lod": lod,
        "gml_id_changed": bool(match.get("id_changed")),
    }
    return lane, {
        "source_gml_id_old": match.get("old_id"),
        "source_gml_id_new": match.get("new_id"),
        "dimensions": dimensions,
        "normalized_changed_paths": paths,
    }


def plan_file(path: Path, geometry_tolerance_m: float) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    lanes: dict[str, list[dict]] = defaultdict(list)
    dimension_counts: Counter[str] = Counter()
    signatures: Counter[tuple[str, ...]] = Counter()
    signature_records: dict[tuple[str, ...], list[dict]] = defaultdict(list)
    family_records: dict[str, dict[tuple[str | None, str | None], set[str]]] = defaultdict(
        lambda: defaultdict(set)
    )
    normalized_path_counts: Counter[str] = Counter()

    for match in data.get("matches", []):
        lane, record = classify_match(match, geometry_tolerance_m)
        record["review_lane"] = lane
        lanes[lane].append(record)
        for name, present in record["dimensions"].items():
            if present:
                dimension_counts[name] += 1
        signature = tuple(record["normalized_changed_paths"])
        signatures[signature] += 1
        signature_records[signature].append(record)
        normalized_path_counts.update(signature)
        id_pair = (record["source_gml_id_old"], record["source_gml_id_new"])
        for changed_path in signature:
            family_records[attribute_family(changed_path)][id_pair].add(changed_path)

    lane_order = (
        "geometry_or_lod_review",
        "attribute_review",
        "source_metadata_review",
        "representation_review",
        "unchanged",
    )
    summary = data.get("summary", {})
    unmatched_old = data.get("unmatched_old_ids", [])
    unmatched_new = data.get("unmatched_new_ids", [])
    changed_signatures = {
        signature: records
        for signature, records in signature_records.items()
        if any(record["review_lane"] != "unchanged" for record in records)
    }
    memberships: dict[tuple[str | None, str | None], set[str]] = defaultdict(set)
    for family, records in family_records.items():
        if family in SOURCE_EVIDENCE_ONLY_FAMILIES:
            continue
        for id_pair in records:
            memberships[id_pair].add(family)
    for records in signature_records.values():
        for record in records:
            id_pair = (record["source_gml_id_old"], record["source_gml_id_new"])
            dimensions = record["dimensions"]
            if dimensions["geometry_fingerprint_changed_1mm"]:
                memberships[id_pair].add("geometry_1mm_fingerprint")
            if dimensions["lod"]:
                memberships[id_pair].add("lod_presence")
            if dimensions["gml_id_changed"]:
                memberships[id_pair].add("source_gml_id")
            memberships[id_pair]
    membership_counts = [len(families) for families in memberships.values()]
    synthetic_counts = {
        "geometry_1mm_fingerprint": sum(
            record["dimensions"]["geometry_fingerprint_changed_1mm"]
            for records in signature_records.values()
            for record in records
        ),
        "lod_presence": sum(
            record["dimensions"]["lod"]
            for records in signature_records.values()
            for record in records
        ),
        "source_gml_id": sum(
            record["dimensions"]["gml_id_changed"]
            for records in signature_records.values()
            for record in records
        ),
    }
    data_family_counts = [
        len(records)
        for family, records in family_records.items()
        if family not in SOURCE_EVIDENCE_ONLY_FAMILIES
    ] + list(synthetic_counts.values())
    basic_data_prs, data_prs_with_pilots = pr_counts_with_pilots(data_family_counts)

    return {
        "transition": transition_name(path),
        "input_file": path.name,
        "source_scope": data.get("source_scope"),
        "counts": {
            "old_buildings": summary.get("old_buildings"),
            "new_buildings": summary.get("new_buildings"),
            "matched_one_to_one": summary.get("matched_one_to_one"),
            "unmatched_old_records": len(unmatched_old),
            "unmatched_new_records": len(unmatched_new),
            "split_candidates": summary.get("split_candidates", 0),
            "merge_candidates": summary.get("merge_candidates", 0),
            "normalized_changed_path_signatures": len(signatures),
            "final_path_signature_groups": len(changed_signatures),
        },
        "dimension_counts_not_mutually_exclusive": dict(sorted(dimension_counts.items())),
        "exclusive_review_lane_counts": {
            name: len(lanes.get(name, [])) for name in lane_order
        },
        "review_lanes": {
            name: {
                "count": len(lanes.get(name, [])),
                "source_gml_id_pairs": [
                    {
                        "old": record["source_gml_id_old"],
                        "new": record["source_gml_id_new"],
                    }
                    for record in lanes.get(name, [])
                ],
            }
            for name in lane_order
        },
        "final_path_signature_gate": [
            {
                "signature_id": signature_id(signature),
                "building_count": len(records),
                "review_lane_counts": dict(
                    sorted(Counter(record["review_lane"] for record in records).items())
                ),
                "normalized_changed_paths": list(signature),
                "source_gml_id_pairs": [
                    {
                        "old": record["source_gml_id_old"],
                        "new": record["source_gml_id_new"],
                    }
                    for record in records
                ],
            }
            for signature, records in sorted(
                changed_signatures.items(), key=lambda item: (-len(item[1]), item[0])
            )
        ],
        "attribute_family_pr_candidates": [
            {
                "attribute_family": family,
                "apply_mode": "source_evidence_only"
                if family in SOURCE_EVIDENCE_ONLY_FAMILIES
                else "citygml_pr",
                "building_count": len(records),
                "allowed_normalized_paths": sorted(
                    {path for paths in records.values() for path in paths}
                ),
                "source_gml_id_pairs": [
                    {"old": old_id, "new": new_id}
                    for old_id, new_id in sorted(records)
                ],
            }
            for family, records in sorted(family_records.items())
        ],
        "attribute_family_plan_summary": {
            "attribute_family_prs": sum(
                family not in SOURCE_EVIDENCE_ONLY_FAMILIES
                for family in family_records
            ),
            "source_evidence_only_families": sorted(
                family
                for family in family_records
                if family in SOURCE_EVIDENCE_ONLY_FAMILIES
            ),
            "attribute_family_building_commits": sum(
                len(records)
                for family, records in family_records.items()
                if family not in SOURCE_EVIDENCE_ONLY_FAMILIES
            ),
            "including_synthetic_matched_building_commits": sum(membership_counts),
            "matched_building_pr_membership_min": min(membership_counts, default=0),
            "matched_building_pr_membership_median": statistics.median(membership_counts)
            if membership_counts
            else 0,
            "matched_building_pr_membership_max": max(membership_counts, default=0),
        },
        "pr_count_summary_excluding_schema_lifecycle_layout": {
            "basic_data_change_family_prs": basic_data_prs,
            "data_prs_with_one_building_pilot_per_family": data_prs_with_pilots,
            "note": (
                "Single-building families are completed as single-building PRs. "
                "Excludes schema, lifecycle, layout-only, and survey-limit-exceeded sub-branches."
            ),
        },
        "synthetic_non_attribute_pr_candidates": [
            {
                "change_family": "geometry_1mm_fingerprint",
                "building_count": synthetic_counts["geometry_1mm_fingerprint"],
                "note": "LOD0 1 mm fingerprint. Production geometry hash including all LOD, elevation, and raw coordinate diffs required separately.",
            },
            {
                "change_family": "lod_presence",
                "building_count": synthetic_counts["lod_presence"],
            },
            {
                "change_family": "source_gml_id",
                "building_count": synthetic_counts["source_gml_id"],
            },
        ],
        "lifecycle_review": {
            "note": "Unmatched old/new counts are record counts, not commit/PR counts before relationship confirmation.",
            "unmatched_old_source_gml_ids": unmatched_old,
            "unmatched_new_source_gml_ids": unmatched_new,
            "split_candidate_details": data.get("split_candidate_details", []),
            "merge_candidate_details": data.get("merge_candidate_details", []),
        },
        "normalized_changed_path_counts": [
            {"path": name, "building_count": count}
            for name, count in normalized_path_counts.most_common()
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", type=Path, nargs="+", help="Yearly comparison JSON file(s)")
    parser.add_argument("--output", type=Path, help="Output JSON (default: stdout)")
    parser.add_argument(
        "--geometry-tolerance-m",
        type=float,
        default=0.01,
        help="Hausdorff distance threshold for geometry review (default 0.01 m)",
    )
    args = parser.parse_args()

    result = {
        "title": "Yearly CityGML Pre-Sort",
        "status": "provisional_review_plan",
        "pr_grouping_policy": "attribute_family",
        "path_signature_role": "final_release_gate",
        "identity_note": (
            "ID column shows gml:id from comparison source. Before applying to publication history, confirm persistent ID mapping for uro:buildingID."
        ),
        "geometry_tolerance_m": args.geometry_tolerance_m,
        "review_lane_priority": [
            "lifecycle_review",
            "geometry_or_lod_review",
            "attribute_review",
            "source_metadata_review",
            "representation_review",
            "unchanged",
        ],
        "transitions": [plan_file(path, args.geometry_tolerance_m) for path in args.inputs],
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
