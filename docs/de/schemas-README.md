<!-- Copyright (c) 2026 4dcitygml -->
<!-- SPDX-License-Identifier: Apache-2.0 (dieses README nur; gebündelte Schemas behalten ihre eigenen Lizenzen) -->

# schemas/ — Schemas für die Offline-XSD-Validierung (gebündelt)

> Deutsche Übersetzung des englischen Originals: [../../schemas/README.md](../../schemas/README.md).
> Bei Abweichungen gilt das englische Original.

Ein lokaler Spiegel von Drittanbieter-XSD-Schemas, der `scripts/validate_citygml.py`
erlaubt, CityGML/PLATEAU **ohne Netzwerkzugriff** zu validieren.

## Layout

- `schemas.opengis.net/` — die **CityGML 2.0** Module + **GML 3.1.1** (verteilt von OGC)
- `www.w3.org/` — xlink / SMIL 2.0 (W3C)
- `docs.oasis-open.org/` — xAL 2.0 (OASIS)
- `master.xsd` — die Validierungs-Wurzel, die alle Namespaces oben plus das gebündelte i-UR (2.0/3.0/3.1/3.2) importiert. `http(s)://` Referenzen innerhalb der Schemas werden in diesem Spiegel durch den lxml Resolver in `validate_citygml.py` aufgelöst.
- i-UR (`uro/2.0–3.2`, `urf/2.0–3.2`): 3.0–3.2 stammen aus `schemas/iur/` der offiziellen PLATEAU-Verteilungs-ZIP; 2.0 (die Ausgabe der PLATEAU-Datensätze 2020–2021) wurde am 2026-09-02 aus dem offiziellen Schemaverzeichnis https://www.geospatial.jp/iur/schemas/ geholt. 1.4/1.5 werden nicht mehr veröffentlicht und sind nicht gebündelt; 4.0 zielt auf CityGML 3.0 und liegt außerhalb dieses Validators.

Verifiziert, dass echte Daten (PLATEAU-verteilt GML, Dutzende von MB / auf der Größenordnung von Tausenden von Gebäuden) `valid=True` ergibt (ungefähr 1.3 Sekunden). Kompilierung und Validierung funktionieren selbst mit dem Netzwerk abgeschnitten.

## Quellen und Aktualisierungen

Die Dateien wurden durch rekursives Folgen von `xsd:import`/`include` von jedem Verteiler gewonnen (ein Abschluss von 51 Dateien). Sie entsprechen den URLs, auf die der `xsi:schemaLocation` von echten GML-Dateien und i-UR-Importen verweist. Zur Aktualisierung, erneut abrufen mit dem gleichen Verfahren.

## Lizenzen (wichtig)

**Jede .xsd in diesem Verzeichnis ist ein Drittanbieter-Werk** und wird durch die Lizenz des Verteilers geregelt (die Apache-2.0 dieses Repositorys wird nicht angewendet):

- OGC-Schemas (CityGML / GML): die OGC-Schema-Nutzungsbedingungen (wiederverteilbar)
- W3C (xlink / SMIL): die W3C-Software-/Dokument-Lizenz
- OASIS (xAL): die OASIS-Schema-Nutzungsbedingungen

Sie werden gebündelt (vendorisiert) für die Bequemlichkeit der Validierung; die Rechte an den Originalen gehören den jeweiligen Organisationen.
