# Integrierte Front-End-Plattform (Launcher)

> Deutsche Übersetzung des englischen Originals: [../../tools/hub/README.md](../../tools/hub/README.md).
> Bei Abweichungen gilt das englische Original.

> Die App präsentiert sich den Benutzern als **"Building Data Editing Tools"** (Bildschirmtitel).
> Die Verteilungs-ZIP und ihr oberster Ordner heißen **`citygml-hub`**; im Code und in Issues
> heißt das integrierte Frontend einfach "hub".

Ein Dashboard, das **den Attributeditor und den Textureditor über Schaltflächen auf einem einzigen Bildschirm startet** und den Status Ihrer Pull Requests / Issues zusammen mit Ihren Erfolgsabzeichen anzeigt. Beitragende können dies als Einstiegspunkt nutzen, ohne sich überlegen zu müssen, "welches Skript ich ausführen soll".

## Start

```bash
python3 tools/hub/app.py        # → öffnet http://localhost:8760
```

Die einzige Abhängigkeit ist die Python 3.9+-Standardbibliothek. `gh` (GitHub CLI) ist **nicht erforderlich**. Das Auflisten von PRs / Issues erfordert eine GitHub-Verbindung. Falls nicht verbunden, können Sie sich über die Schaltfläche "Mit GitHub verbinden" auf dem Dashboard mit dem Geräteflow verbinden (siehe unten).
(Falls der Rechner des Entwicklers gh installiert hat, wird sein Token automatisch wiederverwendet.)

## Ersteinrichtung (#59 / #86)

Beim Start ohne lokales Klone wird der **Einrichtungsbildschirm** angezeigt. Das Prinzip ist dasselbe wie die Startseite — "ein Bildschirm, eine Aktion" — und es gibt darüber hinaus **keine Eingabefelder**.

| | Bildschirm | Aktion |
|---|---|---|
| 1 | Diesen Computer mit GitHub verbinden | [Verbinden] → den 8-stelligen Code notieren → [Nummer kopieren und GitHub öffnen] → auf der anderen Registerkarte genehmigen → zur ursprünglichen Registerkarte zurückkehren |
| 2 | Ihre eigene Kopie erstellen | [Kopie erstellen] (`POST /repos/:owner/:repo/forks`) |
| 3 | Die Daten importieren | [Importieren] (`git clone`, mit Fortschrittsprotokoll) |
| 4 | Alles bereit | [Loslegen] |

- **Wir fragen nicht nach der Fork-URL** (das Werkzeug kennt den Speicherort des Forks). **Wir fragen nicht nach Name oder E-Mail** (diese werden von GitHub abgerufen und automatisch in `git config` eingetragen; falls die E-Mail privat ist, wird eine noreply-Adresse zusammengestellt).
  **Wir fragen auch nicht nach dem Speicherort** (Standard: Documents-Ordner; eine Änderung ist in "Erweiterte Einstellungen" möglich).
- Genehmigung, Fork-Erstellung und Clone-Abschluss werden **automatisch durch Polling erkannt**, und der Bildschirm wird automatisch weitergeleitet (der Benutzer muss nie "Weiter" drücken).
- Unmittelbar nach Erhalt des 8-stelligen Codes wird kein weiterer Tab automatisch geöffnet. Der Benutzer öffnet GitHub über eine Schaltfläche erst, nachdem er "den GitHub-Tab schließen und zurückkehren" gelesen hat. Währenddessen ändert sich auch der Titel des ursprünglichen Tabs zu **"← hierher zurück"**.
- Die E-Mail "A third-party OAuth application has been added to your account", die nach der GitHub-Verbindung ankommt, ist eine normale Benachrichtigung über die abgeschlossene Verbindung. Der Bildschirm erläutert, dass dies nicht mit Mac-/Windows-Sicherheitsabfragen zu tun hat und dass keine weitere Aktion erforderlich ist.
- Für Konten, die die Quelldaten unter dem Einladungsmodell noch nicht erreichen können (privates Repository), wird statt des Fork-Bildschirms ein **"Warten auf Ihre Einladung"-Bildschirm** angezeigt: Er zeigt den GitHub-Benutzernamen in großem Text an und eine **"Anfragetextvorlage kopieren"-Schaltfläche** kopiert eine Vorlagennachricht mit dem Benutzernamen, um sie dem Administrator zu senden
  (der Benutzer muss die Nachricht nie selbst verfassen). **Ausstehende Einladungen werden automatisch vom Server genehmigt**, sodass der Benutzer die Einladungs-E-Mail nicht bemerken muss
  (der Bildschirm wird automatisch weitergeleitet, sobald sie ankommt, #96). Der Wartbildschirm teilt mit, dass das Terminal als Teil der "Ersteinrichtung" offen bleibt, um den Browser-Bildschirm zu bedienen, und dass der Benutzer den Bildschirm schließen und später einfach dieselbe **"start-windows.bat"** starten kann (auf dem Mac dieselbe Starterdatei), um fortzufahren — es ist nicht erforderlich, die GitHub-Verbindung zu wiederholen.
  Keine vorherige Einladung erforderlich = Sie müssen die GitHub-Kontonummer des Empfängers zum Zeitpunkt der Verteilung nicht kennen.
- Der Clone-Speicherort wird mit dem Attributeditor gemeinsam genutzt (`~/.citygml_attr_editor.json`). Für private Verteilung wird `--mode private` (oder `mode`/`inviteUrl` in `preset.json`) die Einladungsleitlinie angezeigt (#78).
- Nach bestätigter Einladung informiert der Bildschirm vorab, dass das Erstellen der Arbeitskopie bis zu einer Minute dauern kann und dass sich der Fortschrittstext während des Datenimports mehrere Minuten lang nicht ändern kann. Falls der Import fehlschlägt, werden Teildaten nicht gelöscht; ein kostenlos verfügbarer alternatives Speicherverzeichnis wird automatisch ausgewählt und [Erneut importieren] wird fortgesetzt.
- Nach Abschluss des Imports wird der Bildschirm nicht automatisch geschlossen; der Fertigstellungsbildschirm wird angezeigt, bis [Loslegen] gedrückt wird. Im ersten Dashboard wird nur der "Attributeditor" als **"hier starten"** empfohlen.
- Wenn der Attributeditor zum ersten Mal geöffnet wird, wird die Operationsfolge "blaues Quadrat (Mesh) auf der Karte → hellblaues Gebäude → Attribute auf der rechten Seite" erläutert. Eine Kurzfassung der Operationsfolge bleibt nach Schließen des Leitfadens auf dem Bildschirm erhalten.
- Nach der Bearbeitung von Attributen einfach "Änderungen senden": Die GitHub-Verbindung des Hubs wird wiederverwendet, um automatisch die Änderung für den Administrator zu erstellen. Falls das Senden fehlschlägt, bleiben die Bearbeitungen auf dem Bildschirm und können sicher erneut versucht werden.

### GitHub-Authentifizierung (OAuth-Geräteflow)

Ein Anfänger-Mac hat weder `gh` noch Git-Anmeldedaten (`gh` ist nicht einmal in den Befehlszeilentools enthalten). Daher **implementieren wir den OAuth-Geräteflow selbst mit nur der Standardbibliothek**, um die Authentifizierung mit "eine Schaltfläche + ein 8-stelliger Code" ohne Öffnen eines Terminals abzuschließen.

- Die `client_id` ist **öffentliche Information** (der Geräteflow benötigt kein client_secret). Stellen Sie sie über `oauthClientId` in `preset.json` oder die Umgebungsvariable `CITYGML_OAUTH_CLIENT_ID` bereit.
- Falls nicht gesetzt, nutzen wir `gh auth token` (Entwicklerrechner gehen damit durch).
  **Um auf Ihrem eigenen Rechner denselben "Verbinden"-Bildschirm wie ein Anfänger zu sehen, starten Sie mit `CITYGML_HUB_NO_GH=1`.**
- Das erhaltene Token wird in `~/.citygml_auth.json` (0600) gespeichert. Nur wenn das gebündelte Git verwendet wird, wird es über **`credential.helper store` mit der dedizierten Datei `~/.citygml_git_credentials` (0600)** an Git übergeben. Falls ein bereits konfiguriertes Git ausgewählt wird, wird sein Credential Helper nicht geändert. In beiden Fällen bleibt die origin-URL einfach, sodass das Token nicht in Screen Sharing oder `git remote -v` sichtbar ist.
- Der erforderliche Umfang ist `repo` (Clone und Push privater Repositories).
- Der benötigte Scope ist `public_repo`: **Lese- und Schreibzugriff auf die öffentlichen Repositories des Kontos** — notwendig und ausreichend für Fork / Push / Pull Request auf die öffentlichen Stadt-Repos. Private Repositories sind nicht umfasst. Derselbe Schreibumfang wird auch auf dem Bildschirm **vor** der Autorisierung (`hub.setup_connect_scope`) genannt, sodass Nutzer vor dem Drücken von Authorize wissen, was sie erlauben. (Nur falls ein Stadt-Repo privat würde, müsste auf `repo` zurückgewechselt werden.)
- Beim Neustart, wenn der GitHub-Benutzer mit dem gespeicherten Token bestätigt werden kann, wird der 8-stellige Code-Bildschirm übersprungen. In diesem Fall zeigt der Bildschirm "Ihre vorherige GitHub-Verbindung wurde wiederverwendet", sodass es nicht wie unbeabsichtigter automatischer Fortschritt aussieht.

> **Die Registrierung einer OAuth-App ist erforderlich** (kostenlos, nur einmal): GitHub-Einstellungen → Entwicklereinstellungen → OAuth Apps → Neue OAuth App → **überprüfen Sie "Device Flow aktivieren"** → geben Sie die ausgestellte Client-ID in `preset.json` ein. Das Client Secret wird nicht verwendet.

### Sprachpolitik (der Leser entscheidet)

Die Sprache erzeugter Texte richtet sich nach dem **Leser**, nicht nach dem Ort des Codes:

| Leser | Beispiele | Sprache |
|---|---|---|
| Die Person am Bildschirm | Menüs, Anleitungen, Fehlermeldungen, Einrichtung | UI-Sprache (`CITYGML_LANG` > Konfiguration > OS-Locale > en) |
| Die Prüfer der Stadt / das öffentliche Protokoll | erzeugter PR-Titel und -Text, Attributnamen im PR-Text | **Arbeitssprache des Repos** (`lang` in `4dcitygml.json`, sonst en) |
| Maschinen | Branch-Präfixe (`edit/`, `tex/`), Commit-Titel und -Text, `Building:`-Trailer, `<!--sec:reason-->` / `<!--cp:key-->`-Anker, CI-Platzhalter-Literale | festes Englisch / Literale — nie übersetzt |
| Die beitragende Person selbst | eingegebene Begründung / Hinweise | bleibt wie geschrieben |

Wissenswerte Konsequenzen:

- Weichen Repo- und UI-Sprache voneinander ab, zeigt der Attribut-Editor über
  der PR-Vorschau einen Hinweis („Dieser Vorschlag wird auf … verfasst“). Die
  Vorschau selbst wird **serverseitig vom selben Code gerendert, der den PR
  erstellt** (`/api/pr-preview`) — Vorschau und PR können nicht auseinanderlaufen.
- Die erzeugten ja/de-Titelpräfixe (`属性修正`/`テクスチャ`,
  `Attributkorrektur`/`Textur…`) entsprechen absichtlich den Titel-Fallbacks in
  `review_kind()` und den CI-Skripten, sodass auch manuelle PRs ohne
  Branch-Präfix klassifiziert werden (vertraglich getestet:
  `tests/test_repo_language.py`).
- Beim Squash-Merge wandert der PR-Titel in die Titelzeile der Historie
  (Übungs-Repos werden periodisch zurückgesetzt; der `Building:`-Trailer-Vertrag
  liegt im Commit-Text und bleibt unberührt).

## Layout der Verteilungs-ZIP

Nur **drei Elemente sind oben** im extrahierten Ordner sichtbar. Dinge, die "für den Benutzer bedeutungslos" sind, wie `index.html` oder `.py`-Dateien, sind **in `program/` verborgen** (#86).

```
citygml-hub/
├─ READ-ME-FIRST.html   ← der einzige Einstiegspunkt (= tools/hub/getting-started.html)
├─ start-mac.command             ← unter Windows: start-windows.bat
└─ program/                    index.html, hub.py, .bat, Lizenzen,
                                  PortableGit, PythonPortable (nur Windows)
```

Die Windows-ZIP bündelt das **embeddable package** von python.org als `PythonPortable/`
(gepinnte Version + SHA-256, zur Build-Zeit verifiziert), sodass keine Python-Installation erforderlich ist;
`start-windows.bat` bevorzugt es und fällt auf ein System `py`/`python` zurück.
(Entscheidung 2026-08-28 — kein gefrorenes Executable.)
Das Windows `PortableGit/` behält seinen Namen aus Kompatibilitätsgründen mit bestehenden Suchpfaden; sein Inhalt ist **MinGit**, die offizielle minimale Git-for-Windows-Konfiguration für App-Bundling. Falls ein Git auf PATH unter Windows bereits `user.name` und `user.email` global konfiguriert hat, hat dieses Git und sein bestehendes Credential-Setup Vorrang. Falls eines nicht gesetzt ist, Git nicht startet oder Git fehlt, wird das gebündelte MinGit verwendet.

- Die ① und ② in den Dateinamen zeigten früher die **Reihenfolge zum Öffnen**. Der erklärende Text (`READ-ME-FIRST.txt`) ist nicht enthalten, da **zwei Einstiege Verwirrung verursachen** (sein Inhalt ist in der HTML-Datei enthalten).
- Startdateien sind **nicht in einem Ordner** platziert: die einzelne zusätzliche "Ordner öffnen"-Operation würde ein Dropout-Risiko darstellen.
- `app.py`'s `BUNDLE_DIRS` sucht nach `PortableGit` / `PythonPortable` / `preset.json` sowohl neben sich selbst als auch in `program/`. `start-mac.command` macht dasselbe (funktioniert auch mit einem flachen Layout während der Entwicklung).

## Startseite (getting-started.html)

**Benutzer werden aufgefordert, diese zuerst zu öffnen** (ein Doppelklick öffnet den Browser; kein Server erforderlich).
Ihre Rolle beschränkt sich auf **klar zu erklären, "wie man startet"** (sie hat keine Schaltflächen, Tests oder Diagnostiken). Sie konzentriert sich darauf, Menschen sicher durch den Punkt zu bringen, an dem viele aufgeben — **"Doppelklick auf die Startdatei → die Sicherheitswarnung des Betriebssystems"** — indem sie versichert, dass dies normal und sicher ist.

Designprinzipien (der #86 Neubau von Grund auf):

- **Ein Bildschirm, eine Aktion.** Mit "Weiter" fortfahren und das Ende mit Fortschrittsanzeige zeigen.
  Mac: 6 Schritte (blockiert → erlauben → öffnen → Ordnerzugriffsberechtigung → Komponenten) /
  Windows: 3 Schritte (Start → Weitere Informationen → Ausführen) + ein Abschlussbildschirm.
- **Der Bildschirm zeigt nur "was jetzt zu tun ist".** Sicherheitsdetails und Fehlerbehebung sind in [wenn etwas schiefgeht] enthalten (Informationen sind verborgen, nicht gelöscht).
- **Keine Fachbegriffe.** Fork → "Ihre eigene Kopie", Clone → "die Daten importieren",
  Befehlszeilentools → "echte Apple-Komponenten", Signierung/Gatekeeper → "ein Bestätigungsbildschirm, der nur beim ersten Mal angezeigt wird".
- **Mit Bildern zeigen.** SVG-Schemadiagramme werden verwendet; für Windows SmartScreen werden die Bildschirme vor dem Klick auf "Weitere Informationen" und mit "Ausführen" separat reproduziert, um echten Screenshots zu entsprechen.
- Das Doppelklicken der Startdatei führt dazu, dass **der Hub den Browser automatisch öffnet**, sodass die Startseite keine "Öffnen"-Schaltfläche hat.
- **Kontoverwaltung, Anmeldung, (private) Einladungsanfragen, Forking und Datenabruf werden vom Hub's "Erste Schritte nach dem Start" (#59)** mit Statusanzeige geführt (eine statische `file://` HTML-Seite kann nicht `gh`/`git` ausführen).

## Startpfad (Windows: gebündeltes Python; Entscheidung 2026-08-28)

Der Hub `app.py` ist **eine einzelne Datei ohne Abhängigkeiten von Nachbardateien**, auf jedem Betriebssystem als einfaches `.py` verteilt — es gibt **genau einen Startpfad und kein gefrorenes Executable**, sodass das, was läuft, immer inspizierbar ist. Die Windows-ZIP bündelt alles Notwendige:

- **Python**: das python.org **embeddable package**, als `PythonPortable/` gebündelt
  (Version + SHA-256 gepinnt; siehe `THIRD_PARTY_NOTICES.md` im Repository-Root).
  Der Launcher (`start-windows.bat` = `packaging/start-windows.bat`) versucht
  **`PythonPortable/` → lokales `py`/`python`** in dieser Reihenfolge, sodass keine Python-Installation erforderlich ist.
- **Git**: MinGit unter dem Kompatibilitätsnamen `PortableGit/`. Ein bestehendes konfiguriertes Git auf PATH hat Vorrang; sonst wird das gebündelte MinGit automatisch erkannt — **keine Git-Installation erforderlich**.
- Der Hub selbst erkennt auch `PythonPortable/` über `python_cmd()` und startet die geklonten Editoren (`tools/*/app.py`) mit demselben Python (`sys.executable` Ausbreitung plus explizite Erkennung als Sicherheitsmaßnahme).
- Das Erkennungsergebnis kann unter `/api/status` unter `runtime` überprüft werden (Git-/Python-Pfad, gebündelt).
- macOS bündelt keine Binärdateien (M1): `start-mac.command` verwendet die CLT `python3`
  (`PythonPortable/` wird dort auch berücksichtigt, falls jemals gebündelt).

> `PythonPortable/` und `PortableGit/` befinden sich in `program/` in der ZIP (auf der gleichen Ebene wie `hub.py`).

## Was es kann

| Feature | Beschreibung |
|---|---|
| **Werkzeugstart** | Startet den Attributeditor (:8765) / Textureditor (:8766) als Unterprozesse und öffnet sie im Browser. Falls bereits laufen, "Öffnen". |
| **Ersteinrichtung** | Wenn es keinen Clone gibt, wird durch GitHub-Authentifizierung (`/api/auth/start`) → Fork-Erstellung (`/api/setup/fork`) → Clone (`/api/setup/clone`) **mit nur Schaltflächen** fortgesetzt. Der Status ist in `/api/setup/status` konsolidiert; der Bildschirm fragt alle 2 Sekunden ab und wird automatisch weitergeleitet. |
| **Konto / Repository** | Zeigt den Git-Branch, Benutzer und GitHub-Verbindungsstatus an. Falls nicht verbunden, führt "Mit GitHub verbinden" (Geräteflow) sofort aus. |
| **Ihre PRs / Issues** | Listet die PRs und Issues auf, die Sie erstellt haben, mit Status (offen/geschlossen/zusammengeführt) und **ob es eine Antwort gab** (Review/Kommentare). Nur vorübergehende CI-Fehler ohne datenspezifische Punkte zum Bestätigen können über automatische Überprüfung "erneut ausgeführt" werden. Datenfehler werden nach Behebung zur automatischen Überprüfung weitergeleitet; Zurückbleiben gegenüber der neuesten Version wird zum erneuten Importieren weitergeleitet. |
| **Bildschirm zur Administrator-Genehmigung** | Gruppiert die von Attribut-/Textureditor eingehenden Änderungsvorschläge nach Gebäude-ID und zeigt sie in zwei Zuständen an, je nachdem, wer als Nächstes tätig wird: "warten auf Genehmiger-Bestätigung" und "warten auf Antragsteller-Maßnahme". Der Wartezustand trägt Grund-Labels wie CI, Genehmiger, neueste Version importieren oder automatische Überprüfung läuft. Alle 11 Überprüfungen — Beschreibung, Änderungseinheit, Konsistenz mit neuester Version, CityGML-Format, Geometrie, Attribute, Topologie und so weiter — werden als bestanden = grün, nicht anwendbar = grau, fehlgeschlagen = rot angezeigt. Die Topologie-Überprüfung wird beim ersten Mal jedes Gebäudes (und bei Geometrieänderungen) ausgeführt; spätere Läufe, die die Form nicht ändern, sind nicht anwendbar. CI lehnt einen PR nie mechanisch ab; es kommentiert die zu bestätigenden Punkte und arbeitet sie mit dem Antragsteller aus. Genehmigende können auch Bestätigungskommentare aus 5 Vorlagen oder Freitext senden und Änderungsanfragen erfassen. Vollständiger Verlauf, japanische Attributnamen, Vor-/Nach-Werte, unterstützende Dokumente, das permanente 3D-Modell und Google Maps können alle auf dem gleichen Bildschirm überprüft werden, und die Genehmigung wird erfasst. Die ausgewählte Gebäude-ID wird in der URL beibehalten, sodass das gleiche Gebäude nach einem Neulade angezeigt wird. `/review.html?demo=1` lässt Sie die Operationen üben, ohne echte Daten oder Änderungsverlauf zu berühren. |
| **Erfolgsabzeichen** | Zeigt einen Rang basierend auf der Anzahl zusammengeführter PRs (✨→🌱→🌿→🌳→🏛️) und die verbleibende Anzahl zum nächsten Rang. |
| **Problem / Vorschläge** | Erstellt ein UX-Feedback-Issue aus einem In-Hub-Formular mit der verbundenen GitHub-Authentifizierung. Keine erneute GitHub-Anmeldung erforderlich. Betreff, Zweck und Umgebung werden vorab gefüllt, und das Erfolgsabzeichen und die gesamte Anzahl zusammengeführter PRs des Absenders werden automatisch als Referenz für Administratoren erfasst. |

Um die Administrator-Karten während einer Demo auszublenden, verwenden Sie `/?admin=off`; um sie anzuzeigen, verwenden Sie `/?admin=on`.
Sie werden auch angezeigt, wenn kein Parameter vorhanden ist. `admin=0` und `admin=false` werden ebenfalls als ausgeblendet behandelt.

## Struktur

| Datei | Rolle |
|---|---|
| `app.py` | Lokaler HTTP-Server (Status, Beitrags-API, Unterprozessstart). Port 8760. In der ZIP: `program/hub.py`. |
| `index.html` | Dashboard-Benutzeroberfläche. In der ZIP: `program/index.html`. |
| `review.html` | Administrator-Benutzeroberfläche für Überprüfung und Genehmigung des Änderungsverlaufs pro Gebäude. |
| `getting-started.html` | Einstiegspunkt der Verteilungs-ZIP (Startleitfaden-Assistent). In der ZIP: `READ-ME-FIRST.html`. |
| `packaging/start-mac.command` / `.bat` | Starter für die `.py`-Version. In der ZIP: `start-mac.command` (Mac) / `program/start-windows.bat` (Windows). |

- Jedes Werkzeug wird auf seinem Standard-Port gestartet (attr_editor=8765 / tex_editor=8766); falls bereits lauschend, wird es wiederverwendet.
- Die GitHub-API wird direkt über REST / GraphQL mit der Standardbibliothek aufgerufen (keine `gh` CLI-Abhängigkeit). Beitragsdaten werden in einer Anfrage mit denselben GraphQL wie gh abgerufen (einschließlich PR reviewDecision), 30 Sekunden lang zwischengespeichert, wobei "Aktualisieren" ein erneutes Abrufen erzwingt.
- Die Authentifizierung verwendet das Geräteflow-Token (`~/.citygml_auth.json`). Falls gh vorhanden ist, wird sein Token auch als Fallback verwendet.
