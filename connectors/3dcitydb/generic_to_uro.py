#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Restore URO property wrappers encoded as 3DCityDB generic attributes.

This is the inverse of ``xslt/uro_to_generic.xsl``. Only generic attribute
sets whose name starts with ``uro:`` are consumed; all other generic
attributes remain untouched.
"""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import Optional

from lxml import etree

DEFAULT_URO = "https://www.geospatial.jp/iur/uro/3.2"
GEN = "http://www.opengis.net/citygml/generics/2.0"
BLDG = "http://www.opengis.net/citygml/building/2.0"
_URO_NS_RE = re.compile(rb"https?://[^\"']+/iur/uro/[0-9.]+")


def qn(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def detect_uro_namespace(citygml: bytes) -> str:
    """Infer the URO namespace from the reviewed source, or use URO 3.2."""
    match = _URO_NS_RE.search(citygml)
    return match.group(0).decode("utf-8") if match else DEFAULT_URO


def _sort_key(element: etree._Element) -> tuple[str, int]:
    name = (element.get("name") or "")[len("uro:") :]
    wrapper, separator, index = name.partition("#")
    try:
        return wrapper, int(index) if separator else 0
    except ValueError:
        return wrapper, 0


def restore_generic_uro(raw: bytes, *, uro_namespace: Optional[str] = None) -> tuple[bytes, int]:
    """Return CityGML bytes with encoded URO sets restored and their count."""
    namespace = uro_namespace or DEFAULT_URO
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True
    )
    tree = etree.parse(BytesIO(raw), parser)
    root = tree.getroot()
    restored = 0

    for building in root.iter(qn(BLDG, "Building")):
        sets = [
            child
            for child in building
            if isinstance(child.tag, str)
            and etree.QName(child).namespace == GEN
            and etree.QName(child).localname == "genericAttributeSet"
            and (child.get("name") or "").startswith("uro:")
        ]
        wrappers: list[etree._Element] = []
        for attribute_set in sorted(sets, key=_sort_key):
            restored += 1
            wrapper_name = (attribute_set.get("name") or "")[len("uro:") :].split("#", 1)[0]
            if not wrapper_name:
                continue
            wrapper = etree.Element(qn(namespace, wrapper_name), nsmap={"uro": namespace})
            children = list(attribute_set)
            code_spaces: dict[str, str] = {}
            units: dict[str, str] = {}
            for child in children:
                name = child.get("name") or ""
                value = child.find(qn(GEN, "value"))
                text = (value.text or "") if value is not None else ""
                if name.endswith("@codeSpace"):
                    code_spaces[name[: -len("@codeSpace")]] = text
                elif name.endswith("@uom"):
                    units[name[: -len("@uom")]] = text

            path_elements: dict[str, etree._Element] = {"": wrapper}
            for child in children:
                name = child.get("name") or ""
                if not name or name.endswith("@codeSpace") or name.endswith("@uom"):
                    continue
                value = child.find(qn(GEN, "value"))
                text = (value.text or "") if value is not None else ""
                parts = [part for part in name.split("/") if part]
                if not parts:
                    continue
                parent = wrapper
                prefix = ""
                for component in parts[:-1]:
                    prefix = component if not prefix else f"{prefix}/{component}"
                    element = path_elements.get(prefix)
                    if element is None:
                        element = etree.SubElement(parent, qn(namespace, component))
                        path_elements[prefix] = element
                    parent = element
                leaf = etree.SubElement(parent, qn(namespace, parts[-1]))
                leaf.text = text
                if name in units:
                    leaf.set("uom", units[name])
                elif name in code_spaces:
                    leaf.set("codeSpace", code_spaces[name])
            wrappers.append(wrapper)

        for attribute_set in sets:
            building.remove(attribute_set)
        for wrapper in wrappers:
            building.append(wrapper)

    if restored == 0:
        return raw, 0
    declaration = raw.lstrip().startswith(b"<?xml")
    return etree.tostring(root, encoding="UTF-8", xml_declaration=declaration), restored


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--uro-namespace")
    args = parser.parse_args()
    output, count = restore_generic_uro(
        args.input.read_bytes(), uro_namespace=args.uro_namespace
    )
    args.output.write_bytes(output)
    print(f"restored {count} URO generic attribute set(s): {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
