# Einrichtungsleitfaden (für Anfänger)

> Deutsche Übersetzung des englischen Originals: [../../tools/attr_editor/setup-guide.md](../../tools/attr_editor/setup-guide.md).
> Bei Abweichungen gilt das englische Original.

Selbst ohne Programmierumgebung können Sie Korrektionen von Gebäudeattributen mit dem Attributeditor vorschlagen.
Die Anfangseinrichtung (Schritte 1–4 hier) wird nur einmal benötigt. Es dauert ungefähr 30 Minuten
(die meiste Zeit wird auf den Datenabruf gewartet).

Was Sie benötigen: eine Internetverbindung, ein GitHub-Konto (kostenlos) und ungefähr 10 GB freier Speicherplatz.

---

## 1. Erstellen Sie ein GitHub-Konto

1. Öffnen Sie <https://github.com/signup>
2. Geben Sie Ihre E-Mail-Adresse, ein Passwort und einen Benutzernamen (ASCII-Buchstaben und Ziffern) ein, um sich zu registrieren
3. Geben Sie den Code aus der Bestätigungs-E-Mail ein, um die Registrierung abzuschließen

## 2. Erstellen Sie einen "Fork" (Ihre eigene Kopie) der Gebäudedaten

1. Während Sie angemeldet sind, öffnen Sie <https://github.com/4dcitygml/sample-tokyo-station/fork>
2. Drücken Sie die grüne Schaltfläche **Kopie erstellen**
3. Notieren Sie sich die URL der resultierenden Seite
   (`https://github.com/<Ihre-ID>/sample-tokyo-station`) — Sie werden sie später benötigen

## 3. Installieren Sie die erforderlichen Werkzeuge (zum Abrufen von Daten und Senden von Vorschlägen)

### Unter macOS

1. Öffnen Sie die Terminal-App (Launchpad → "Sonstiges"), geben Sie `git --version` ein und drücken Sie Enter
   → falls der Bildschirm "Befehlszeilenentwicklertools installieren" angezeigt wird, drücken Sie "Installieren"
   (**zusammen mit Git wird dadurch auch die python3 installiert, die zum Ausführen des Editors erforderlich ist**)
2. Installieren Sie den Credential-Manager **Git Credential Manager**:
   laden Sie herunter und installieren Sie `gcm-osx-*.pkg` von
   <https://github.com/git-ecosystem/git-credential-manager/releases>

### Unter Windows

**Dieser Schritt ist nicht erforderlich.** Die "All-in-One-ZIP" in Schritt 4 bündelt alle erforderlichen
Werkzeuge (Git und den Credential-Manager). Sie müssen nichts installieren.

> Mit dem Credential-Manager installiert müssen Sie beim ersten Mal, wenn Sie einen Vorschlag senden, nur
> **sich bei GitHub im Browser anmelden**, um sich zu authentifizieren (keine manuelle Eingabe von
> Passwörtern oder Tokens erforderlich).

## 4. Starten Sie den Attributeditor

### Unter macOS (extrahieren Sie die ZIP und eine Terminal-Zeile; keine Warnungen)

1. Laden Sie **`citygml-attr-editor-macos.zip`** von der [Releases-Seite](https://github.com/4dcitygml/tools/releases) herunter und doppelklicken Sie zum Extrahieren
2. Geben Sie im Terminal `python3 ` ein (mit einem nachfolgenden Leerzeichen) — **drücken Sie nicht sofort die Enter-Taste** — dann **ziehen Sie die app.py aus dem extrahierten Ordner in das Terminal-Fenster**. Bestätigen Sie, dass die Zeile `python3 /Users/…/app.py` lautet, drücken Sie dann Enter
   (falls Sie `>>>` sehen, haben Sie zu früh auf Enter gedrückt; geben Sie `exit()` + Enter ein, um zu beenden, und versuchen Sie es erneut. Details befinden sich auch im Ordner "READ-ME-FIRST.txt")
3. Der **Einrichtungsbildschirm** erscheint im Browser; fügen Sie die **Fork-URL** ein, die Sie in Schritt 2 notiert haben, und drücken Sie **Importieren** → der Abruf der Daten (einige GB) beginnt. Wenn dies abgeschlossen ist, wird der Editor-Bildschirm automatisch geöffnet
4. An diesem Punkt wird **"Attribute Editor.command" automatisch auf Ihrem Desktop erstellt**.
   **Doppelklicken Sie von nun an einfach darauf** (kein Terminal mehr erforderlich. Es wurde auf Ihrem Mac erstellt, daher erscheint auch keine Sicherheitswarnung)

> Falls Sie sich mit dem Terminal auskennen, können Sie auch in einer Zeile ohne die ZIP starten:
> `curl -sL https://raw.githubusercontent.com/4dcitygml/tools/main/tools/attr_editor/app.py -o ~/attr_editor.py && python3 ~/attr_editor.py`

### Unter Windows (extrahieren Sie die ZIP und doppelklicken Sie; keine Installation)

1. Laden Sie **`citygml-attr-editor-windows-full.zip`** (All-in-One, Git gebündelt, ungefähr 100 MB) von der [Releases-Seite](https://github.com/4dcitygml/tools/releases) herunter
2. Klicken Sie mit der rechten Maustaste auf die ZIP → **Alles extrahieren**. Extrahieren Sie an einen **flachen Ort wie den Desktop**
   (tiefe Ordner können die Pfadlängenbegrenzung erreichen)
3. Doppelklicken Sie auf **`start-windows.bat`** im extrahierten Ordner (es startet das gebündelte Python — keine Installation erforderlich).
   Falls beim ersten Mal eine Sicherheitswarnung angezeigt wird, können Sie über **Weitere Informationen → Trotzdem ausführen** fortfahren
   (dies erscheint, weil die Verteilung nicht signiert ist; es ist nicht abnormal)
4. Der **Einrichtungsbildschirm** erscheint im Browser; fügen Sie die Fork-URL ein und drücken Sie **Importieren**

> Falls ein Git auf PATH bereits `user.name` und `user.email` global konfiguriert hat, bevorzugt die All-in-One-ZIP auch das bestehende Git und sein Credential-Setup. Falls Sie Git und Python 3.9+ selbst installiert haben, funktioniert `start-windows.bat` auch damit
> (die gebündelten Versionen werden einfach nicht verwendet).

## 5. Alltägliche Verwendung

1. Starten Sie die App (Mac: "Attribute Editor.command" auf dem Desktop / Windows: doppelklicken Sie start-windows.bat)
2. Klicken Sie auf einen Mesh-Rahmen auf der Karte → klicken Sie auf ein Gebäude → klicken Sie auf Attributwerte auf der rechten Seite, um sie zu korrigieren
3. **Änderungen senden** → geben Sie den Grund und die Belege für die Korrektur ein → **Diese Änderungen senden**
4. Falls Sie sich über den Hub mit GitHub verbunden haben, wird der Änderungsvorschlag für den Administrator automatisch erstellt
5. Wenn "Senden abgeschlossen. Warten auf Administrator-Bestätigung" angezeigt wird, sind Sie fertig

> Nur falls "GitHub öffnen, um das Senden zu beenden" angezeigt wird —
> z. B. bei eigenständiger Verwendung ohne Hub — drücken Sie die grüne Schaltfläche **Pull Request erstellen** auf der verlinkten Seite, und drücken Sie die gleiche Schaltfläche noch einmal auf dem Bestätigungsbildschirm.

## Fehlerbehebung

| Symptom | Was zu tun ist |
|---|---|
| Kein Bildschirm wird nach dem Start angezeigt | Überprüfen Sie die Fehlermeldungen im schwarzen Fenster (Protokoll). Öffnen Sie <http://localhost:8765/> direkt im Browser |
| "git nicht gefunden" Fehler erscheint | Wiederholen Sie Schritt 3 und starten Sie die App neu |
| Der Clone wird unterbrochen | Versuchen Sie es erneut auf einer stabilen Verbindung (löschen Sie zuerst den Zielordner) |
| Authentifizierungsfehler beim Senden | Überprüfen Sie, dass der Credential-Manager (Schritt 3) installiert ist |
| Ich möchte die Daten aktualisieren | Beenden Sie die App, führen Sie dann `git pull` im Clone-Ordner aus (oder verwenden Sie "Sync fork" auf Ihrer GitHub-Fork-Seite → klonen Sie erneut) |
| (Mac) Ich habe den Launcher gelöscht | Führen Sie `python3 ~/Documents/sample-tokyo-station/tools/attr_editor/app.py` im Terminal aus und er wird neu erstellt |
