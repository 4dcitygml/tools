# CityGML-Attributeditor

> Deutsche Übersetzung des englischen Originals: [../../tools/attr_editor/README.md](../../tools/attr_editor/README.md).
> Bei Abweichungen gilt das englische Original.

Ein leichtes lokales Werkzeug, spezialisiert auf die Ansicht und Bearbeitung von Gebäudeattributen.
Es bietet nur einen Ablauf — "wählen Sie ein Gebäude auf einer 2D-Karte → sehen Sie alle seine Attribute →
korrigieren Sie sie und öffnen Sie einen Pull Request" — als Front-End für Beitragende (die Überprüfung wird von
bestehender CI und GitHub durchgeführt).

## Start

Starten Sie es einfach innerhalb dieses Klons (`--repo` wird auto-erkannt). Die einzige Abhängigkeit ist Python 3.9+. Unter macOS läuft es ohne Weiteres mit der in den Befehlszeilentools gebündelten python3:

```bash
python3 tools/attr_editor/app.py        # → öffnet http://localhost:8765
```

> **Für Einwohner und Mitarbeitende der Stadt**: der Editor wird aus dem Hub gestartet, den
> der Einzeilen-Befehl im README Ihrer Stadt installiert (siehe README des Hubs). Der Hub
> hält das GitHub-Konto, das die Stadt verwendet, hält die Daten aktuell und startet
> diesen Editor damit. Einen eigenen Download des Editors gibt es nicht mehr (die
> früheren eigenständigen ZIPs wurden mit hub-v1.2 eingestellt).

- Die Benutzeroberfläche lädt Leaflet / Cesium von CDNs (alles außer Kartenkacheln funktioniert offline).
- Der Änderungsvorschlag (PR) wird mit dem Konto geöffnet, das der Hub für die Stadt gespeichert hat (`~/.citygml/auth/<login>.json`); die eigenen GitHub-Anmeldungen des Computers werden nie verwendet. Ohne Konto stoppt das Senden, bevor etwas passiert, und der Bildschirm verweist auf die Einstellungen des Hubs; ohne Hub endet das Senden bei einer Vergleichs-URL auf GitHubs eigener Seite.
- Falls es mehrere Datenpakete gibt, wird das größte automatisch ausgewählt (`--data 13101` um es explizit anzugeben).

## Verwendung

1. Klicken Sie auf einen Mesh-Rahmen auf der Karte → Gebäudegrundrisse erscheinen (erstes Parsing dauert einige Sekunden)
2. Klicken Sie auf ein Gebäude → eine **3D-Vorschau an seiner tatsächlichen Position** erscheint unter der Karte und eine Attributkarte im rechten Bereich
   (LOD1/LOD2 schaltbar; für Gebäude ohne LOD2 sind die LOD2- und Textur-Schaltflächen ausgegraut. Teilbar über `?tile=&bid=` in der URL)
3. Klicken Sie auf einen Wert zum Inline-Bearbeiten (Code-Listen verwenden Dropdown-Listen) → Änderungen werden gelb angezeigt
4. Wenn Sie einen Wert bestätigen, wird ein **erforderliches Quellenauswahlfeld** in der gleichen Reihe geöffnet → wählen Sie das Dokument, das Sie überprüft haben
   ("unbekannt" und "noch nicht erstellt" können nicht als Quelle einer neuen Änderung gewählt werden.
   Das Senden ist blockiert, solange selbst ein Attribut keine Auswahl hat)
5. Wenn Sie einen Quellcode auswählen, wird ein elementspezifischer Hinweis zur Quelle hinzugefügt
   nach den Auflösungsregeln in [Quellenaufzeichnungsregeln](https://github.com/4dcitygml/city-template/blob/main/docs/provenance-rules.md). Codes, die upstream fehlen, werden auch automatisch zu `thematicSrcDesc` = R2-8 hinzugefügt.
   Für Attribute mit doppelten Namen, die keine elementspezifischen Hinweise unterstützen können, wird die Quelle auf Gebäude-Ebene synchronisiert und die Attributzuordnung wird im PR-Text beibehalten
6. "Änderungen senden" → URLs oder Hinweise hinzufügen, falls erforderlich → bestehen Sie die **Voreinreichungs-Überprüfung** (einzelnes Zielgebäude, XML-Format, geänderter Datei-Umfang, Quellverknüpfung) → der Änderungsvorschlag für den Administrator wird automatisch erstellt

Der PR-Titel und -Text werden automatisch aus den geänderten Elementen, den Vor-/Nach-Werten und den ausgewählten Quellen generiert. Zum Beispiel wird lesbarer Text wie "Überprüfung der Feldbegehung und Korrektur der Stockwerke über Grund von 2 auf 3" produziert, sodass Administratoren, die XML-Tag-Namen nicht kennen, dennoch den Grund für die Änderung verstehen können. Dieser generierte Text wird in der **Arbeitssprache des Repos** verfasst (`lang` in `4dcitygml.json`), denn seine Leser sind die Prüfer der Stadt; weicht sie von Ihrer Anzeigesprache ab, erscheint ein Hinweis über der Vorschau (siehe „Sprachpolitik“ im hub-README). Die Quellen aller Attribute werden nicht nur in der Benutzeroberfläche, sondern auch in der Einreichungs-API erneut validiert, sodass ein reiner Wertänderungs-Vorschlag nicht erstellt werden kann.

## Struktur und Design

| Datei | Rolle |
|---|---|
| `app.py` | Lokaler HTTP-Server (GML-Parsing, JSON-API, Blattwertersetzung, Git/PR) |
| `index.html` | 2D-Karte + Attributbereich + Bearbeitung + PR-Benutzeroberfläche |
| `viewer.html` | Einzelgebäude 3D-Ansicht mit Cesium (`?tile=<mesh>&bid=<gml:id>`) |

- Die Mesh-Auswahl ist absichtlich explizit: Der Benutzer klickt auf einen Mesh-Rahmen, bevor seine Gebäudegrundrisse geladen werden. Das Schwenken oder Zoomen der Karte schaltet Meshes nicht automatisch um, was verhindert, dass eine große GML-Datei unbeabsichtigt geparst wird, und hält das ausgewählte Arbeitsziel stabil.
- Die Bearbeitung serialisiert das XML nicht neu; sie **ersetzt nur Blattwerte im ursprünglichen Byte-Stream durch String-Ersetzung**
  (behält UTF-8-BOM, CRLF, Einrückung und Element-Reihenfolge; konsistent mit dem W6-minimal-diff-Gate).
- `gml:id`, Geometrie, `uro:buildingID` und `core:creationDate` sind immer schreibgeschützt.
- PRs sind **eine pro Gebäude** (Multi-Gebäude-Änderungen werden von CI automatisch abgelehnt, sodass die Benutzeroberfläche sie nie an erster Stelle erstellt).
- Die Voreinreichungs-Überprüfung wird lokal durchgeführt und inspiziert den Zustand nach der Änderung, ohne Dateien neu zu schreiben. Die gleiche Überprüfung wird zur Einreichungszeit erneut durchgeführt; Details wie vollständige XSD-Validierung werden von CI nach der Einreichung überprüft.
- Commits folgen dem Format `attr-fix(<attribut-name>): <alt> → <neu>` plus ein `Building: <uro:buildingID>` Trailer (die gleiche Konvention wie `scripts/suggest_commit.py`;
  `git log --grep "Building: <id>"` funktioniert).
