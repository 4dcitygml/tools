# CityGML LOD2 Textur-Editor

> Deutsche Übersetzung des englischen Originals: [../../tools/tex_editor/README.md](../../tools/tex_editor/README.md).
> Bei Abweichungen gilt das englische Original.

Ein lokales Werkzeug, das sich auf das **Ersetzen oder Neu-Hinzufügen von Fassadenfotos (LOD2-Texturen)**
von Gebäuden spezialisiert hat. Ein Schwester-Werkzeug des [Attribut-Editors](attr_editor-README.md), es
bietet einen Ablauf: "wählen Sie ein Gebäude auf einer 2D-Karte → wählen Sie ein Gesicht in der 3D-/Gesichtsliste →
schneiden Sie ein Foto zu und fügen Sie es ein → PR".
Fotos können auch **neu hinzugefügt** zu LOD2-Daten ohne Texturen werden, mit den gleichen Operationen (#119).

## Start

```bash
python3 tools/tex_editor/app.py        # → öffnet http://localhost:8766
```

- Die einzige Abhängigkeit ist Python 3.9+ (gemeinsam genutzte Mechanismen werden wiederverwendet, indem attr_editor/app.py geladen wird).
- Wenn vom Inneren des Klons aus gestartet, wird `--repo` automatisch erkannt. Es kann zusammen mit dem Attribut-Editor ausgeführt werden (verschiedene Ports).
- Wie im Attribut-Editor ist die Mesh-Auswahl absichtlich manuell: Gebäudegrundrisse werden nur nach dem Klicken des Benutzers auf einen Mesh-Rahmen geladen, niemals automatisch, nur weil die Karte geschwenkt oder vergrößert wurde.

## Verwendung

1. Klicken Sie auf einen Mesh-Rahmen auf der Karte → klicken Sie auf ein Gebäude (Gebäude mit LOD2 sind berechtigt.
   Gebäude ohne Texturen gelangen in den **Neu-Textur-Modus**, in dem Sie mit den gleichen Operationen auf einen einfachen Platzhalter einfügen)
2. Wählen Sie eine Wand über die **Wandkarten** im rechten Bereich (zusammengesetzte Fassaden-Miniaturbilder)
   oder durch **Klicken auf ein Gesicht in der 3D-Ansicht**
   - Weil LOD2-Wände in feine Polygone pro Geschossband aufgeteilt werden, wird die Bearbeitung in
     **"Wand"-Einheiten durchgeführt, die nach normaler Richtung gruppiert werden** (z. B. ein 40-Gesicht-Hochhaus → 4 Wände +
     Dach usw.). Dächer und ähnliches werden pro Gesicht bearbeitet
3. Ziehen Sie ein Foto per Drag & Drop → **ziehen Sie die vier Ecken in der Reihenfolge oben links → oben rechts →
   unten rechts → unten links**, um die ganze Wand anzupassen (Neigung und Keystone werden automatisch korrigiert) → [Anwenden] **backt es automatisch auf alle Polygone, die die Wand zusammensetzen** und zeigt eine Vorschau in 3D
   - Das Backen löst eine Pro-Gesicht-Affin-Transformation durch kleinste Quadrate aus der eins-zu-eins-Entsprechung zwischen textureCoordinates und Polygon-Vertizes, also Orientierung, Spiegelungen und dünne Streifen werden korrekt behandelt
4. Nach dem Anwenden werden die **Vor-/Nach-3D-Gebäude nebeneinander angezeigt** über dem
   flachen Bildvergleich im rechten Bereich. Das Drehen oder Vergrößern einer von ihnen synchronisiert die andere auf den gleichen Sichtpunkt, sodass Sie direkt vergleichen können, wie sie in das ganze Gebäude passt. Darunter werden **vor/nach flache Bilder** pro Wand/Gesicht angezeigt. Die Bearbeitungs-3D-Ansicht auf der linken Seite kann auch temporär auf die ursprüngliche Textur mit [Schalten Sie die Bearbeitungs-3D auf Vorher] revertiert werden
5. Mehrere Wände/Gesichter können zusammen bearbeitet werden (nur innerhalb des gleichen Gebäudes) → wenn zufrieden, [Pull Request erstellen]. Der erzeugte PR-Titel und -Text werden in der **Arbeitssprache des Repos** verfasst (`lang` in `4dcitygml.json`; siehe „Sprachpolitik“ im hub-README) — Commit-Meldungen bleiben Englisch.

### Kamera-Ausrichtungsmodus (Batch-Einfügen aus einem Foto)

Aus einer einzigen Straßenecken-Aufnahme können Sie alle in dem Foto sichtbaren Gesichter auf einmal aktualisieren (einschließlich Penthouse und Rücksprung-Seiten).

1. [📷 Batch-Einfügen aus einem Foto] → Foto ablegen
2. Klicken Sie auf entsprechende Punkte-Paare, in der Reihenfolge **gelber Vertex-Marker in der 3D-Ansicht → die gleiche Stelle im Foto**, mindestens 6 Paare
   - **Falls Sie einen Punkt an der unteren Kante einer Wand auswählen, erscheint eine große Anweisung**:
     im Foto klicken Sie nicht auf den Fuß der Wand, sondern auf **den Punkt direkt unterhalb der Ecke des Daches (die Grenze mit dem Boden)**. Die Wände des Modells sind vertikal abgelegte Flächen aus der Dachumriss-Linie (Trauflinie), sodass bei Gebäuden mit Traufüberstand sie nicht mit dem echten Wandfuß übereinstimmen
     (die generische Behebung für den #109-Korrespondenz-Offset; mit der Dachecke und dem Punkt direkt darunter können vertikal getrennte Korrespondenz-Punkte gewonnen werden und stabilisieren die Ausrichtung)
3. [Ausrichten] → die Kamera-Pose wird geschätzt (normalisierte DLT); bestätigen Sie, dass das hellblaue Drahtmodell das Gebäude im Foto überlagert (der Umprojektion-Fehler wird angezeigt; falls es falsch ist, fügen Sie Korrespondenz-Punkte hinzu)
4. [Erstellen Sie Einfügekandidaten] → generiert ein **Sichtbarkeitgeprüftes Patch** pro Gesicht, akzeptieren oder ablehnen mit Kontrollkästchen (der sichtbare Bruchteil wird angezeigt; Vorschau in 3D) → [Anwenden]

**Unsichtbare Pixel behalten die ursprüngliche Textur** (Pixel hinter der Kamera, verdeckt vom Gebäude selbst oder außerhalb des Rahmens werden durch einen Z-Puffer-Test ausgeschlossen, und Grenzen werden gefedert). Das Wiederholen mit Fotos aus anderen Winkeln aktualisiert das ganze Gebäude schrittweise. Hinweis: **Verdeckungen andere als das Gebäude selbst** — benachbarte Gebäude, Straßenbäume usw. — können nicht erkannt werden, daher deaktivieren Sie Gesichter, wo sie angezeigt werden.
   - Eingereichte Fotos werden mit **CC0 1.0** mit Ihrer Zustimmung bereitgestellt
     ([Datenbeitragspolitik](https://github.com/4dcitygml/city-template/blob/main/docs/data-contribution-policy.md), einschließlich Vorsicht für Gesichter, Kennzeichen usw.)

## Bearbeitungsmethode — Atlas-Backen (wichtige Designpunkte)

In echten Daten ist die Struktur "1 Bild = 1 ParameterizedTexture, kein Bildfreigabe zwischen Gebäuden, mehrere Gesichter des gleichen Gebäudes teilen sich ein Bild (Atlas)", sodass eine Ersetzung:

1. Perspektiv-korrekt das Foto auf einer Browser-Canvas und **backen Sie es in den UV-Bereich einer Kopie des ursprünglichen Atlas-Bildes**
2. **Fügen Sie das neue Bild unter einem inhalt-adressierten Namen hinzu (`tex_<first 12 of sha256>.jpg`)**
   (bestehende Bilder sind unberührt = R1)
3. In der GML, **ersetzen Sie exakt eine `app:imageURI` Blatt-Wert mit einer Eindeutigkeits-verifizierten Übereinstimmung** (UV und XML-Struktur unverändert)

Dies hält die Differenz minimal — "1 GML-Zeile + 1 neues Bild" — und bleibt konsistent mit dem bestehenden CI (texture_check zählt es als ein Gebäude mit einer Erscheinungsänderung (a) → Klassifizierung=einfach, und die R3-hängede Überprüfung wird bestanden, da das neue Bild zusammen übernommen wird). In dem seltenen Fall, dass ein Atlas über mehrere Gebäude geteilt wird, wird dieses Gesicht als nicht bearbeitbar angezeigt (ein Sicherheitsnetz).

## Ton-Anpassung

- Zur Anwendungszeit werden eingereichte Fotos **automatisch auf "den aktuellen Ton des Gesichts, das ersetzt wird"** korrigiert (nachdem die Batch-Konvertierung abgeschlossen ist, hat die Stadt Sonne/Schatten-Direktionalität gebacken, daher müssen wir dem Pro-Gesicht-gemessenen Ziel statt einem Stadt-breiten Ziel entsprechen:
  Nord-Gesichter bleiben Nord-ähnlich, Süd-Gesichter bleiben Süd-ähnlich). In YCbCr verwendet Luma Mittelwert und Varianz, Chroma verwendet nur den Mittelwert = Weißabgleich. Sättigung wird erhalten, sodass beabsichtigte Farbänderungen wie Neuanstrich nicht verloren gehen. Es kommt mit einem "Stärke"-Schieber und manueller Helligkeits-/Farbtemperatur-Feinabstimmung.
  `tone_standard.json` ist der Standard für die Batch-Konvertierung (Anfangsvorbereitung)
- Die Batch-Konvertierung aller bestehenden Texturen (Anfangsvorbereitung vor der Veröffentlichung) ist `scripts/retone_textures.py`
  (--stats / --write-standard Referenzfoto / --preview / --apply; verwendet Pillow).
  Die Aufgabenteilung: Die Batch-Seite wendet **eine einzelne Transformation für die ganze Stadt** an (behält Pro-Gebäude hell/dunkel-Charakter), während die Einreichungsseite **jedes Foto** auf den Standard anpasst (absorbiert Unterschiede in Aufnahmebedingungen)

### Vergleichen von Tönen als Team (das Play-Off-Skript)

```bash
python3 tools/tex_editor/tone_battle.py                 # öffnet 4 Tabs: original / lift=100 / 115 / 130
python3 tools/tex_editor/tone_battle.py --lifts 105,120       # ändern Sie die Kandidaten
```

Die 3D-Überprüfung zeigte, dass "die Dunkelheit nicht Unterbelichtung ist, sondern **gebackener Schatten**
(sonnenbeleuchtete Gesichter sind korrekt belichtet)", daher ist der Standard die **Schattenhebungs-Methode** (eine Tonkurve, die nur die dunklen Teile hebt; sonnenbeleuchtete Bereiche unverändert).
Es funktioniert richtig, selbst wenn sonnenbeleuchtete und Schattierungsgesichter in einem Atlas nebeneinander existieren. Einmal entschieden, reparieren Sie den Standard mit `python3 scripts/retone_textures.py --set-lift <value>`.

Konvertierte Texturen für die Kandidaten-Töne werden automatisch generiert (ein paar Minuten beim ersten Mal; Pillow erforderlich), und Tabs mit dem gleichen Gebäude werden pro Ton ausgerichtet (der Ton-Name wird im Header angezeigt und die Header-Farbe ändert sich ebenfalls, daher keine Verwechslungen). Drücken Sie LOD2 → Textur im 3D-Bereich zum Vergleichen; verwenden Sie den `--set-lift`-Befehl oben, um den gewählten Wert aufzuzeichnen.

### Fototipps

- **Vorzugsweise bei bedecktem Wetter oder schattige Stunden** (bei Sonnenschein werden Sonnenschatten in Wände gebacken und können nicht durch Korrektur entfernt werden)
- Richten Sie die Wand so quadratisch wie möglich aus, und halten Sie das Thema nahe der Mitte des Fotos, um Verzeichnungsfehler zu vermeiden
- Zur Vorsicht bei Gesichtern, Kennzeichen usw., siehe die [Datenbeitragspolitik](https://github.com/4dcitygml/city-template/blob/main/docs/data-contribution-policy.md)

## Umfang der Unterstützung

- **Ersetzung** von Gesichtern, die bereits Texturen haben (v1)
- **Neu-Hinzufügen** zu Gebäuden ohne Texturen (#119):
  - **Ein Atlas pro Gebäude, neuer Pfad nur beim ersten Mal**: beim ersten Hinzufügen wird ein Atlas generiert, der die UVs aller LOD2-Wandgesichter des Gebäudes auf einmal definiert
    (ungefotografierte Wände erhalten einfache Platzhalter-Pixel). **Ab dem zweiten Mal wird es in den Ersetzungsmodus zusammengefasst**
  - UVs werden aus den Wand-Koordinaten s×h der Wand-Cluster (normale Richtung) generiert. **Nur Wände** (neues Einfügen auf Dachoberseiten und Löschen liegen außerhalb des Bereichs)
  - Die einzige Schreibvorgabe zu der GML ist das Einfügen eines Appearance-Blocks (`xmlns:app` ist elementlokal deklariert; bestehende Elemente und Geometrie unverändert; BOM/CRLF erhalten; konsistent mit R1/R3 und den CI-Überprüfungen; XSD-validiert)
  - Neue Hinzufügungen erhalten keine automatische Ton-Korrektur (es gibt kein "aktuellen Ton des Gesichts, das ersetzt wird"; manuelle Helligkeits- und Farbtemperatur-Anpassung ist verfügbar)
- Da das Backen den ganzen Atlas als JPEG neu codiert (Qualität 0.92), werden andere Gesichter im gleichen Atlas auch leicht neu komprimiert (visuell im Wesentlichen unverändert)
