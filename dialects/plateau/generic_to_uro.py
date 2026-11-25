#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Restore URO property wrappers encoded as 3DCityDB generic attributes.

This is the inverse of ``xslt/uro_to_generic.xsl``. Only generic attribute
sets whose name starts with ``uro:`` are consumed; all other generic
attributes remain untouched.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dialects.plateau import semantic_registry  # noqa: E402
from scripts.citygml_xml import qname, sub, to_bytes  # noqa: E402
from scripts.safe_xml import safe_fromstring  # noqa: E402

GEN = "http://www.opengis.net/citygml/generics/2.0"
BLDG = "http://www.opengis.net/citygml/building/2.0"


def qn(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def detect_uro_namespace(citygml: bytes) -> "str | None":
    """The i-UR namespace of the reviewed source's edition (the registry's one sniffer, W5: this
    module had its own and a 3.2 default), or None when it declares no registered edition."""
    edition = semantic_registry.detect_edition(citygml)
    return semantic_registry.namespace_of(edition) if edition else None


def _sort_key(element) -> tuple[str, int]:
    name = (element.get("name") or "")[len("uro:") :]
    wrapper, separator, index = name.partition("#")
    try:
        return wrapper, int(index) if separator else 0
    except ValueError:
        return wrapper, 0


def restore_generic_uro(raw: bytes, *, uro_namespace: Optional[str] = None) -> tuple[bytes, int]:
    """Return CityGML bytes with encoded URO sets restored and their count."""
    namespace = uro_namespace
    root = safe_fromstring(raw, huge_tree=True)   # hardened, any DOCTYPE refused (it had its own parser)
    restored = 0

    for building in root.iter(qn(BLDG, "Building")):
        sets = [
            child
            for child in building
            if isinstance(child.tag, str)
            and qname(child) == (GEN, "genericAttributeSet")
            # a set named "uro:" alone names no wrapper: it stays as it is (D-4b1: it was removed
            # and not written again, so its content was lost)
            and (child.get("name") or "")[len("uro:"):].split("#", 1)[0]
            and (child.get("name") or "").startswith("uro:")
        ]
        if sets and not namespace:   # never a guessed edition (W5: it was 3.2)
            raise ValueError("encoded i-UR sets but no i-UR edition: give --uro-namespace, or restore against a source that declares one")
        wrappers: list = []
        for attribute_set in sorted(sets, key=_sort_key):
            restored += 1
            wrapper_name = (attribute_set.get("name") or "")[len("uro:") :].split("#", 1)[0]
            wrapper = building.makeelement(qn(namespace, wrapper_name), nsmap={"uro": namespace})
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

            path_elements: dict = {"": wrapper}
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
                        element = sub(parent, qn(namespace, component))
                        path_elements[prefix] = element
                    parent = element
                leaf = sub(parent, qn(namespace, parts[-1]), text)
                if name in units:
                    leaf.set("uom", units[name])
                if name in code_spaces:   # both when the leaf has both (D-4b6: the codeSpace was lost)
                    leaf.set("codeSpace", code_spaces[name])
            wrappers.append(wrapper)

        for attribute_set in sets:
            building.remove(attribute_set)
        for wrapper in wrappers:
            building.append(wrapper)

    if restored == 0:
        return raw, 0
    declaration = raw.lstrip().startswith(b"<?xml")
    return to_bytes(root, declaration), restored


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
