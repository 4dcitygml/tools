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

Die einzige Abhängigkeit ist die Python 3.9+-Standardbibliothek. `gh` (GitHub CLI) ist **nicht erforderlich**. Das Auflisten von PRs / Issues erfordert eine GitHub-Verbindung. Wenn nicht verbunden, wählen Sie einfach das Konto der Stadt auf dem Kontobildes (siehe unten); die `gh`-Anmeldung dieses Computers wird dort als eine Wahlmöglichkeit angeboten, wird aber niemals eigenständig verwendet.

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
  (der Bildschirm wird automatisch weitergeleitet, sobald sie ankommt, #96). Der Wartbildschirm teilt mit, dass das Terminal als Teil der "Ersteinrichtung" offen bleibt, um den Browser-Bildschirm zu bedienen, und dass der Benutzer den Bildschirm schließen und später einfach **die Werkzeuge erneut starten** kann (Desktop-Symbol oder die eine Befehlszeile), um fortzufahren — es ist nicht erforderlich, die GitHub-Verbindung zu wiederholen.
  Keine vorherige Einladung erforderlich = Sie müssen die GitHub-Kontonummer des Empfängers zum Zeitpunkt der Verteilung nicht kennen.
- Der Clone-Speicherort wird mit dem Attributeditor gemeinsam genutzt (`~/.citygml_attr_editor.json`). Für private Verteilung wird `--mode private` (oder `mode`/`inviteUrl` in `preset.json`) die Einladungsleitlinie angezeigt (#78).
- Nach bestätigter Einladung informiert der Bildschirm vorab, dass das Erstellen der Arbeitskopie bis zu einer Minute dauern kann und dass sich der Fortschrittstext während des Datenimports mehrere Minuten lang nicht ändern kann. Falls der Import fehlschlägt, werden Teildaten nicht gelöscht; ein kostenlos verfügbarer alternatives Speicherverzeichnis wird automatisch ausgewählt und [Erneut importieren] wird fortgesetzt.
- Nach Abschluss des Imports wird der Bildschirm nicht automatisch geschlossen; der Fertigstellungsbildschirm wird angezeigt, bis [Loslegen] gedrückt wird. Im ersten Dashboard wird nur der "Attributeditor" als **"hier starten"** empfohlen.
- Wenn der Attributeditor zum ersten Mal geöffnet wird, wird die Operationsfolge "blaues Quadrat (Mesh) auf der Karte → hellblaues Gebäude → Attribute auf der rechten Seite" erläutert. Eine Kurzfassung der Operationsfolge bleibt nach Schließen des Leitfadens auf dem Bildschirm erhalten.
- Nach der Bearbeitung von Attributen einfach "Änderungen senden": Die GitHub-Verbindung des Hubs wird wiederverwendet, um automatisch die Änderung für den Administrator zu erstellen. Falls das Senden fehlschlägt, bleiben die Bearbeitungen auf dem Bildschirm und können sicher erneut versucht werden.

### GitHub-Authentifizierung (OAuth-Geräteflow)

Ein Anfänger-Mac hat weder `gh` noch Git-Anmeldedaten (`gh` ist nicht einmal in den Befehlszeilentools enthalten). Daher **implementieren wir den OAuth-Geräteflow selbst mit nur der Standardbibliothek**, um die Authentifizierung mit "eine Schaltfläche + ein 8-stelliger Code" ohne Öffnen eines Terminals abzuschließen.

- Die `client_id` ist **öffentliche Information** (der Geräteflow benötigt kein client_secret). Stellen Sie sie über `oauthClientId` in `preset.json` oder die Umgebungsvariable `CITYGML_OAUTH_CLIENT_ID` bereit.
- **Ein Konto pro Stadt, ausdrücklich ausgewählt (hub-v1.3).** Der Kontobildes bietet drei Möglichkeiten, jeweils ein Klick: die `gh`-Anmeldung dieses Computers (angezeigt mit ihrem Login, wenn `gh` angemeldet ist), ein auf diesem Computer zuvor verbundenes Konto oder eine neue Anmeldung mit dem 8-stelligen Code. Nichts wird automatisch verwendet; die Wahl wird pro Stadt aufgezeichnet (`cities[<owner/repo>].login` in den gemeinsamen Einstellungen) und nie an eine andere Stadt vererbt.
- Jedes Konto wird in `~/.citygml/auth/<login>.json` (0600) zusammen mit `<login>.git-credentials` im git-credential-store-Format gespeichert. Netzwerk-git-Befehle des Hubs und der Editoren übergeben auf jeder Plattform `-c credential.helper=` und `-c credential.https://github.com.helper=store --file=<diese Datei>`, sodass der Schlüsselbund, Manager oder globaler Store des Computers nie abgerufen wird und das Token nie in einer Befehlszeile angezeigt wird. Die Commit-Identität (`<id>+<login>@users.noreply.github.com`) wird in die **lokale** Git-Konfiguration des Klons geschrieben; die globale Identität des Computers wird in den Einstellungen angezeigt, aber nicht verwendet und nicht geändert.
- Auch ohne Konto übergeben Netzwerk-git-Befehle `-c credential.helper=`: Die Anmeldedaten des Computers werden nie verwendet, ein Push scheitert mit der klaren Meldung „kein Konto verbunden“, und die Editoren verweisen auf die Einstellungen des Hubs. Wechselt das Konto eines bestehenden Klons, wird `origin` auf den Fork dieses Kontos umgestellt (der Schritt „Kopie anlegen“ läuft, falls der Fork noch fehlt); das Stadt-Repository bleibt unberührt.
- Ein Token, das GitHub ablehnt (401), trennt das Konto ab und sagt dies ("wurde widerrufen"); Einstellungen → "Trennen" / "Löschen" entfernen die Dateien auf diesem Computer (das Widerrufen auf GitHub erfolgt in Einstellungen → Anwendungen → Autorisierte OAuth Apps).
- Frühere Versionen behielten ein Token in `~/.citygml_auth.json` und schrieben auf einigen Computern einen globalen Credential Helper, der auf die Klartextdatei `~/.citygml_git_credentials` verweist. Der erste Start von hub-v1.3 zeigt einen einmaligen Handover-Bildschirm mit den gefundenen Inhalten, migriert das Token in eine Kontodatei und bietet an, nur das zu entfernen, das die frühere Version geschrieben hat.
- Die Beschriftung „GitHub-Anmeldung dieses Computers“ wird nur aus der Konfigurationsdatei der GitHub CLI gelesen; ihr Token wird erst beim Drücken dieser Wahl gelesen (`CITYGML_HUB_NO_GH=1` blendet die Wahl aus). Vorschläge werden mit dem Konto der Stadt oder auf der GitHub-Seite eröffnet – die Editoren rufen nie `gh pr create` auf. Jeder Prozess entfernt beim Start die git-Überschreibungen der Shell (`GIT_AUTHOR_*`, `GIT_ASKPASS`, `GIT_SSH*`, …).
- Wenn eine Stadt von einer Organisation mit **OAuth App-Zugriffsbeschränkungen** gehostet wird (bei neuen Organisationen standardmäßig aktiviert), antwortet die Forkerstellung und PR-Erstellung mit 403, bis ein Organisationsinhaber der App Zugriff gewährt; der Hub übersetzt diese Nachricht und weist auf Organization access unter der Einstellungsseite der App hin.
- Der erforderliche Scope ist `public_repo`: **Lese- und Schreibzugriff auf die öffentlichen Repositories des Kontos** — notwendig und ausreichend für Fork / Push / Pull Request auf die öffentlichen Stadt-Repos. Private Repositories sind nicht umfasst. Derselbe Schreibumfang wird auch auf dem Bildschirm **vor** der Autorisierung (`hub.setup_connect_scope`) genannt, sodass Nutzer vor dem Drücken von Authorize wissen, was sie erlauben. (Nur falls ein Stadt-Repo privat würde, müsste auf `repo` zurückgewechselt werden.)
- Beim Neustart wird das für die Stadt aufgezeichnete Konto ohne Bildschirm verwendet. Eine Stadt ohne aufgezeichnetes Konto zeigt immer zuerst den Kontobildes.

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

## Layout des installierten Programms

Die Release-ZIP ist die Nutzlast des Einzeilen-Installers, kein Download, den jemand öffnet:
ihr einziger Eintrag auf oberster Ebene ist `program/`, entpackt nach `citygml-tools/citygml-hub/<tag>/`.

```
citygml-tools/
├─ citygml.sh | citygml.ps1        Starter pro Benutzer (aus program/ kopiert; das Desktop-Symbol führt ihn aus)
└─ citygml-hub/<hub-vX.Y.Z>/program/
      hub.py, index.html, review.html, setup.html, settings.html
      runtime.py, accounts.py, git_sync.py, shortcuts.py, pr_classification.py
      attr_editor/, tex_editor/, i18n/, themes/, Lizenzen,
      PortableGit/ und PythonPortable/ (nur Windows)
```

`runtime.py` ist die eine Stelle, die dieses Layout kennt, die Dateien der Person (Einstellungen,
Konten, Werkzeugordner — alle aus HOME abgeleitet), das zu verwendende git und python, wie ein
Klon seine Stadt benennt und wie GitHub erreicht wird; der Hub und die Editoren importieren es und
die anderen gemeinsamen Module über ihren Namen. Die eigene Git-Konfiguration der Person entscheidet
nie, welches git läuft: die Identität wird in den Klon geschrieben und die Zugangsdaten werden pro
Befehl übergeben (siehe `docs/client-runtime-contract.md`).

## Startpfad (Windows: gebündeltes Python; Entscheidung 2026-08-28)

Der Hub und die Editoren sind einfache `.py`-Dateien neben den gemeinsamen Modulen, auf jedem Betriebssystem als Quelltext verteilt — es gibt **genau einen Startpfad und kein gefrorenes Executable**, sodass das, was läuft, immer inspizierbar ist. Die Windows-ZIP bündelt alles Notwendige:

- **Python**: das python.org **embeddable package**, als `PythonPortable/` gebündelt
  (Version + SHA-256 gepinnt; siehe `THIRD_PARTY_NOTICES.md` im Repository-Root).
  Das Launcher-Skript im Benutzerordner (`citygml.ps1`) startet `program/hub.py` mit
  `program/PythonPortable/python.exe`; der in `program/` mitgelieferte Ersatzstarter
  (`start-windows.bat` = `packaging/start-windows.bat`) versucht
  **`PythonPortable/` → lokales `py`/`python`** in dieser Reihenfolge. In beiden Fällen ist keine Python-Installation erforderlich.
- **Git**: MinGit unter dem Kompatibilitätsnamen `PortableGit/`, verwendet, sobald es vorhanden ist (`runtime.git_exe()`); sonst das git auf PATH — **keine Git-Installation erforderlich**.
- Der Hub startet die gebündelten Editoren (`program/attr_editor/app.py`, `program/tex_editor/app.py`) mit demselben Python (`runtime.python_exe()`); aus einem Stadt-Klon wird nie Code ausgeführt.
- Das Erkennungsergebnis kann unter `/api/status` unter `runtime` überprüft werden (Git-/Python-Pfad, gebündelt).
- macOS bündelt keine Binärdateien (M1): das Launcher-Skript im Benutzerordner (`citygml.sh`)
  verwendet das `python3` auf PATH (Apples Command Line Tools); `packaging/start-mac.command`
  dient nur dem Start aus einem Quellcode-Checkout.

> `PythonPortable/` und `PortableGit/` befinden sich in `program/` in der ZIP (auf der gleichen Ebene wie `hub.py`).

## Was es kann

| Feature | Beschreibung |
|---|---|
| **Werkzeugstart** | Startet den Attributeditor (:8765) / Textureditor (:8766) als Unterprozesse und öffnet sie im Browser. Falls bereits laufen, "Öffnen". |
| **Ersteinrichtung** | Wenn es keinen Clone gibt, wird durch GitHub-Authentifizierung (`/api/auth/start`) → Fork-Erstellung (`/api/setup/fork`) → Clone (`/api/setup/clone`) **mit nur Schaltflächen** fortgesetzt. Der Status ist in `/api/setup/status` konsolidiert; der Bildschirm fragt alle 2 Sekunden ab und wird automatisch weitergeleitet. |
| **Konto / Repository** | Zeigt den Git-Branch, Benutzer und GitHub-Verbindungsstatus an. Ohne Konto verlinkt auf den Kontobildes; "Einstellungen" öffnet die städtischen Einstellungsseite (Konto, Kopie auf GitHub, Datenordner, Version, Desktop-Symbol, gespeicherte Konten, Neustart, wie Sie die Tools entfernen). |
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
| `setup.html` | Ersteinrichtung und Kontobildschirm (solange es keinen Klon oder kein Konto für die Stadt gibt). |
| `settings.html` | Einstellungen pro Stadt (Konto, Kopie auf GitHub, Datenordner, Version, Desktop-Symbol, Entfernen der Werkzeuge). |
| `review.html` | Administrator-Benutzeroberfläche für Überprüfung und Genehmigung des Änderungsverlaufs pro Gebäude. |
| `packaging/start-mac.command` / `.bat` | Starter für den Start aus einem Quellcode-Checkout. Die Verteilungs-ZIP hat keinen Starter mehr auf oberster Ebene (hub-v1.2.0): sie ist die Nutzlast des Einzeilen-Installers, der sie nach `citygml-tools/citygml-hub/<tag>/` entpackt und `program/hub.py` startet; `program/citygml.sh` / `citygml.ps1` sind die Launcher-Skripte, die er in den Benutzerordner kopiert. |

- Jedes Werkzeug wird auf seinem Standard-Port gestartet (attr_editor=8765 / tex_editor=8766); falls bereits lauschend, wird es wiederverwendet.
- Die GitHub-API wird direkt über REST / GraphQL mit der Standardbibliothek aufgerufen (keine `gh` CLI-Abhängigkeit). Beitragsdaten werden in einer Anfrage mit denselben GraphQL wie gh abgerufen (einschließlich PR reviewDecision), 30 Sekunden lang zwischengespeichert, wobei "Aktualisieren" ein erneutes Abrufen erzwingt.
- Die Authentifizierung verwendet das Token des für die Stadt aufgezeichneten Kontos
  (`~/.citygml/auth/<login>.json`); `gh` wird niemals eigenständig herangezogen.
