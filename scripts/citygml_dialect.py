#!/usr/bin/env python3
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
"""Which CityGML a file speaks, and where its coordinates are: one answer for the editors, the
hub and the CI preview, for every city (CityGML 1.0 and 2.0; a projected CRS such as UTM or a
state plane in US feet as well as latitude/longitude).

- ``ns_for_root(root)``: the namespaces of the file's own CityGML version.
- ``crs_transformer(srs_name)``: (x, y) -> (lat, lon); the identity for lat/lon data; None when the
  CRS is missing or unknown (no map, never "treat as lat/lon").
  No external dependency (it runs in the hub bundle). A transformer of a CRS in US feet carries
  ``z_scale`` (heights are in feet too).
- ``file_transformer(root, ns, declared)``: the transformer of the file's own Envelope srsName, else
  of the city's declared `crs`.

Shipped next to the hub as program/citygml_dialect.py.
"""
from __future__ import annotations

import math
import re

NS = {
    "core": "http://www.opengis.net/citygml/2.0",
    "bldg": "http://www.opengis.net/citygml/building/2.0",
    "gml": "http://www.opengis.net/gml",
    "app": "http://www.opengis.net/citygml/appearance/2.0",
    "gen": "http://www.opengis.net/citygml/generics/2.0",
    "xlink": "http://www.w3.org/1999/xlink",
}


_GRS80_A = 6378137.0
_GRS80_F = 1 / 298.257222101
_US_FT = 1200.0 / 3937.0  # US survey foot [m]


def _tm_inverse(E: float, N: float, lon0_deg: float, *, k0: float = 0.9996,
                E0: float = 500000.0, N0: float = 0.0) -> "tuple[float, float]":
    """Inverse transverse Mercator (UTM) → (lat, lon) [deg]. GRS80 (practically equal to ETRS89/WGS84)."""
    a, f = _GRS80_A, _GRS80_F
    e2 = f * (2 - f)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    M = (N - N0) / k0
    mu = M / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    ep2 = e2 / (1 - e2)
    C1 = ep2 * math.cos(phi1) ** 2
    T1 = math.tan(phi1) ** 2
    N1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    R1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    D = (E - E0) / (N1 * k0)
    lat = phi1 - (N1 * math.tan(phi1) / R1) * (
        D ** 2 / 2 - (5 + 3 * T1 + 10 * C1 - 4 * C1 ** 2 - 9 * ep2) * D ** 4 / 24
        + (61 + 90 * T1 + 298 * C1 + 45 * T1 ** 2 - 252 * ep2 - 3 * C1 ** 2) * D ** 6 / 720)
    lon = math.radians(lon0_deg) + (
        D - (1 + 2 * T1 + C1) * D ** 3 / 6
        + (5 - 2 * C1 + 28 * T1 - 3 * C1 ** 2 + 8 * ep2 + 24 * T1 ** 2) * D ** 5 / 120
    ) / math.cos(phi1)
    return math.degrees(lat), math.degrees(lon)


def _lcc_inverse_2263(E_ft: float, N_ft: float) -> "tuple[float, float]":
    """Inverse EPSG:2263 (NAD83 / New York Long Island, US feet) → (lat, lon)."""
    a, f = _GRS80_A, _GRS80_F
    e = math.sqrt(f * (2 - f))
    lat1, lat2 = math.radians(41 + 2 / 60), math.radians(40 + 40 / 60)
    lat0, lon0 = math.radians(40 + 10 / 60), math.radians(-74.0)
    E0 = 984250.0 * _US_FT
    x, y = E_ft * _US_FT - E0, N_ft * _US_FT

    def m(phi):
        return math.cos(phi) / math.sqrt(1 - e ** 2 * math.sin(phi) ** 2)

    def t(phi):
        return (math.tan(math.pi / 4 - phi / 2)
                / ((1 - e * math.sin(phi)) / (1 + e * math.sin(phi))) ** (e / 2))

    n = (math.log(m(lat1)) - math.log(m(lat2))) / (math.log(t(lat1)) - math.log(t(lat2)))
    F = m(lat1) / (n * t(lat1) ** n)
    rho0 = a * F * t(lat0) ** n
    rho = math.copysign(math.hypot(x, rho0 - y), n)
    tp = (rho / (a * F)) ** (1 / n)
    theta = math.atan2(x, rho0 - y)
    phi = math.pi / 2 - 2 * math.atan(tp)
    for _ in range(6):
        phi = math.pi / 2 - 2 * math.atan(
            tp * ((1 - e * math.sin(phi)) / (1 + e * math.sin(phi))) ** (e / 2))
    return math.degrees(phi), math.degrees(theta / n + lon0)


# Geographic CRSs whose coordinates are written latitude first (EPSG axis order): no conversion.
# JGD2011 (6668) and JGD2011 + height (6697), WGS 84 (4326, 4979), ETRS89 (4258, 4937),
# NAD83 (4269), JGD2000 (4612).
LATLON_EPSG = frozenset({4326, 4979, 4258, 4937, 4269, 4612, 6668, 6697})


def _latlon(x, y):
    return x, y


def crs_transformer(srs_name: str):
    """srsName -> (x, y) -> (lat, lon) transformer; None when the CRS is missing or unknown.

    A geographic CRS of LATLON_EPSG gets the identity (its values are latitude, longitude). Formula
    implementation without external dependencies for: UTM (ETRS89/WGS84, urn:adv notation and
    EPSG:258xx/326xx) and EPSG:2263 (NY Long Island). Anything else is unknown: the caller shows no
    map and no preview, never reading the values as latitude and longitude (v2.0.0 phase 1-2)."""
    s = str(srs_name or "")
    m = re.search(r"UTM[ _]?zone[ _]?(\d{1,2})|UTM(\d{1,2})", s)
    if m:
        zone = int(m.group(1) or m.group(2))
        if 1 <= zone <= 60:
            return lambda x, y: _tm_inverse(x, y, zone * 6 - 183)
    m = re.search(r"EPSG(?::+|/\d+/)(\d+)", s)   # EPSG:6697, urn:...:EPSG::6697, .../EPSG/0/6697
    if m:
        code = int(m.group(1))
        if code in LATLON_EPSG:
            return _latlon
        if 25801 <= code <= 25860:  # ETRS89 / UTM
            zone = code - 25800
            return lambda x, y: _tm_inverse(x, y, zone * 6 - 183)
        if 32601 <= code <= 32660:  # WGS84 / UTM north
            zone = code - 32600
            return lambda x, y: _tm_inverse(x, y, zone * 6 - 183)
        if code == 2263:
            def tf(x, y):
                return _lcc_inverse_2263(x, y)
            tf.z_scale = _US_FT  # vertical is also US feet → the caller multiplies z by this
            return tf
    return None


# CityGML 1.0: the same modules, the version part of the namespaces rewritten (gml is shared)
NS10 = {k: v.replace("/2.0", "/1.0") for k, v in NS.items()}


def list_key(codespace: str) -> str:
    """A codeSpace's list as a path below the code lists folder: `<edition>/X.xml` for a list kept
    under its edition's folder, `X.xml` for the current one (D-4b4: the file name alone was
    taken, so a carried list was judged as the current edition's list of that name)."""
    path = codespace.replace("\\", "/")
    at = path.rfind("codelists/")
    return path[at + len("codelists/"):] if at >= 0 else path.rsplit("/", 1)[-1]


def ns_for_root(root) -> dict:
    """Return the namespace dict matching the file's CityGML version (a 1.0 file's root
    element is in `…/citygml/1.0`)."""
    m = re.match(r"\{(.+?)\}", root.tag or "")
    return NS10 if m and m.group(1).endswith("/citygml/1.0") else NS


def ns_for_bytes(raw: bytes) -> dict:
    """ns_for_root for a file that is not parsed: the version its CityModel declares at the start."""
    return NS10 if b"http://www.opengis.net/citygml/1.0" in raw[:8192] else NS


def file_transformer(root, ns: dict, declared: str = ""):
    """The transformer of the file's CRS: its Envelope's srsName, else the CRS the city declares
    (4dcitygml.json `crs`); None when neither is a CRS this module knows."""
    env = root.find(f"{{{ns['gml']}}}boundedBy/{{{ns['gml']}}}Envelope")
    return crs_transformer((env.get("srsName") if env is not None else "") or declared)
