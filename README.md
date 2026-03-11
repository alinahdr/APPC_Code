# 🏥 ELGA APPC – FHIR Terminologie-Server

> Liest österreichische Radiologie-Codes aus Excel, baut FHIR-Terminologie-Ressourcen und lädt sie auf einen lokalen HAPI FHIR Server hoch.

![Python](https://img.shields.io/badge/Python-3.x-blue?logo=python)
![FHIR](https://img.shields.io/badge/FHIR-R4-orange)
![HAPI](https://img.shields.io/badge/HAPI%20FHIR-latest-green)
![Docker](https://img.shields.io/badge/Docker-required-blue?logo=docker)

---

## 📋 Inhaltsverzeichnis

- [Überblick](#-überblick)
- [Was machen die Skripte?](#-was-machen-die-skripte)
- [FHIR Grundlagen](#-fhir-grundlagen)
- [Die 3 FHIR-Ressourcen](#-die-3-fhir-ressourcen)
- [Operations](#-operations)
- [Einrichtung & Start](#-einrichtung--start)
- [Fehlerbehebung](#-fehlerbehebung)

---

## 🔍 Überblick

### ELGA_APPC.xlsx – Radiologie PR-Codes
Enthält österreichische Radiologie-Codes aufgeteilt auf 10 Sheets (je Modalität):

| Sheet | Modalität |
|-------|-----------|
| US | Ultraschall |
| CT | Computertomographie |
| MR | MRT |
| RÖ | Röntgen |
| NUK | Nuklearmedizin |
| … | … |

Jede Zeile verbindet einen **PR-Code** (interner Protokoll-Code, z.B. `US1AAUA`) mit einem **APPC-Code** (österreichischer Standardcode, z.B. `4.0.0.1-1`).

### ELGA_APPC_KUK.xlsx – KUK Leistungs-Codes
Enthält KUK-interne Leistungs-Codes in einem einzelnen Sheet (`Tabelle1`):

| Spalte | Inhalt | Beispiel |
|--------|--------|---------|
| `Leistung` | Interner Code | `MA4` |
| `Leistung Text` | Beschreibung | `Digitale 2-Ebenen - Mammographie` |
| `APPC KR` | Ziel-Code | `7` |

---

## ⚙️ Was machen die Skripte?

### appc_.py – ELGA APPC Upload
```
1. Excel einlesen       →  alle 10 Sheets (1588 PR-Codes)
2. FHIR-Ressourcen bauen →  CodeSystem, ValueSet, ConceptMap
3. Hochladen            →  auf HAPI FHIR Server (localhost:8080)
4. Testen               →  $validate-code + $translate
```

**Ergebnis nach dem Durchlauf:**
```
[✓] $validate-code 'US1AAUA' → gültig: True
[✓] $validate-code 'INVALID99' → gültig: False
[✓] $translate 'US1AAUA' → '4.0.0.1-1'
[✓] $translate 'CT1AAUA' → '2.0.0.1-1'
```

### appc_kuk.py – KUK Upload
```
1. Excel einlesen       →  1 Sheet (1053 Leistungs-Codes)
2. FHIR-Ressourcen bauen →  CodeSystem, ValueSet, ConceptMap
3. Hochladen            →  auf denselben HAPI FHIR Server
4. Testen               →  $validate-code + $translate + $lookup
```

**Ergebnis nach dem Durchlauf:**
```
[✓] $validate-code 'MA4' → gültig: True
[✓] $translate 'MA4' → '7'
[✓] $lookup 'MA4' → 'Digitale 2-Ebenen - Mammographie'
```

> ⚠️ **Reihenfolge:** Zuerst `appc_.py`, dann `appc_kuk.py` ausführen.

---

## 📚 FHIR Grundlagen

### Was ist FHIR?

**FHIR** (Fast Healthcare Interoperability Resources) ist ein internationaler Standard für den Austausch medizinischer Daten. Statt Excel-Tabellen verwendet FHIR strukturierte JSON-Objekte über eine REST-API:

```http
GET    /fhir/Patient/123          → Patient abrufen
POST   /fhir/Patient              → Patienten anlegen
PUT    /fhir/CodeSystem?url=...   → CodeSystem anlegen / aktualisieren
```

### FHIR Operations (das `$`-Zeichen)

Neben normalen REST-Methoden gibt es **Operations** – spezielle Aktionen erkennbar am `$`:

```http
GET  /fhir/CodeSystem/$validate-code?url=...&code=US1AAUA
GET  /fhir/CodeSystem/$lookup?system=...&code=US1AAUA
POST /fhir/ConceptMap/$translate
```

### Ressourcen direkt abrufen

Jede Ressource ist über zwei Wege erreichbar:

```http
# Über URL (empfohlen – stabil über Docker-Neustarts hinweg)
GET /fhir/CodeSystem?url=http://elga.gv.at/fhir/CodeSystem/elga-appc-pr-codes

# Über ID (kürzer, aber ändert sich nach Docker-Neustart)
GET /fhir/CodeSystem/1000
```

### ✅ Verfügbare Operations prüfen

```
http://localhost:8080/fhir/metadata
```

> ⚠️ **Wenn `validate-code` nicht auftaucht:** Docker-Container neu starten mit `-e hapi.fhir.cr.enabled=true`

---

## 🧩 Die 3 FHIR-Ressourcen

```
CodeSystem  →  der Katalog        (welche Codes existieren?)
ValueSet    →  die Auswahlliste   (welche Codes sind hier gültig?)
ConceptMap  →  die Übersetzung    (welcher Code entspricht welchem?)
```

Die drei Ressourcen sind über **URLs** miteinander verbunden – diese URLs sind eindeutige Identifier, keine echten Webadressen. HAPI sucht intern nach einem CodeSystem mit genau dieser URL.

| Operation | Fragt ab | Antwort |
|---|---|---|
| `$lookup` | CodeSystem | Was bedeutet der Code? → Text |
| `$validate-code` | ValueSet | Existiert der Code? → true/false |
| `$translate` | ConceptMap | Welcher andere Code gehört dazu? → Code |

### 1. CodeSystem – der Katalog

```json
{
  "resourceType": "CodeSystem",
  "url": "http://elga.gv.at/fhir/CodeSystem/elga-appc-pr-codes",
  "content": "complete",
  "concept": [
    { "code": "US1AAUA", "display": "Ultraschall Abdomen" },
    { "code": "CT1AAUA", "display": "CT Abdomen" }
  ]
}
```

### 2. ValueSet – die Auswahlliste

Referenziert ein oder mehrere CodeSystems über deren URL:

```json
{
  "resourceType": "ValueSet",
  "url": "http://elga.gv.at/fhir/ValueSet/elga-appc-pr-codes",
  "compose": {
    "include": [{ "system": "http://elga.gv.at/fhir/CodeSystem/elga-appc-pr-codes" }]
  }
}
```

> 💡 Das `"system"` ist nur ein Identifier – HAPI sucht intern nach einem CodeSystem mit dieser URL. Die URL muss nicht im Internet erreichbar sein.

### 3. ConceptMap – die Übersetzungstabelle

```json
{
  "resourceType": "ConceptMap",
  "group": [{
    "source": "http://elga.gv.at/fhir/CodeSystem/elga-appc-pr-codes",
    "target": "http://elga.gv.at/fhir/CodeSystem/elga-appc-codes",
    "element": [
      { "code": "US1AAUA", "target": [{ "code": "4.0.0.1-1", "equivalence": "equivalent" }] }
    ]
  }]
}
```

> ⚠️ **Reihenfolge beim Hochladen wichtig:** CodeSystems zuerst, dann ValueSet, dann ConceptMap – sonst kennt HAPI die referenzierten URLs noch nicht.

---

## 🔧 Operations

### `$validate-code` – Ist der Code gültig?

```http
GET /fhir/CodeSystem/$validate-code
    ?url=http://elga.gv.at/fhir/CodeSystem/elga-appc-pr-codes
    &code=US1AAUA
```

**Response:**
```json
{ "parameter": [{ "name": "result", "valueBoolean": true }] }
```

### `$translate` – Welcher APPC-Code gehört dazu?

```http
POST /fhir/ConceptMap/$translate
```
```json
{
  "resourceType": "Parameters",
  "parameter": [
    { "name": "url",    "valueUri":  "http://elga.gv.at/fhir/ConceptMap/elga-pr-to-appc" },
    { "name": "system", "valueUri":  "http://elga.gv.at/fhir/CodeSystem/elga-appc-pr-codes" },
    { "name": "code",   "valueCode": "US1AAUA" }
  ]
}
```

**Response:**
```json
{
  "parameter": [
    { "name": "result", "valueBoolean": true },
    { "name": "match", "part": [
        { "name": "concept", "valueCoding": { "code": "4.0.0.1-1" } }
    ]}
  ]
}
```

### `$lookup` – Was bedeutet dieser Code?

```http
GET /fhir/CodeSystem/$lookup
    ?system=http://elga.gv.at/fhir/CodeSystem/elga-appc-pr-codes
    &code=US1AAUA

→ display: "Ultraschall Abdomen"
```

> 💡 `$lookup` und `$translate` machen verschiedene Dinge: `$lookup` gibt den **Text** zurück, `$translate` gibt den **gemappten Code** zurück. Für beides gleichzeitig beide Operationen nacheinander aufrufen.

---

## 🚀 Einrichtung & Start

### Voraussetzungen

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)
- Python 3.x
- Pakete: `pip install pandas openpyxl requests`
- `ELGA_APPC.xlsx` und `ELGA_APPC_KUK.xlsx` im selben Ordner wie die Skripte

### 1. HAPI FHIR Server starten

```bash
docker run -p 8080:8080 -e hapi.fhir.cr.enabled=true hapiproject/hapi:latest
```

| Parameter | Bedeutung |
|-----------|-----------|
| `-p 8080:8080` | Port des Containers nach außen weiterleiten |
| `-e hapi.fhir.cr.enabled=true` | **Clinical Reasoning aktivieren** – wird für `$validate-code` benötigt |
| `hapiproject/hapi:latest` | Offizielles HAPI FHIR Docker-Image |

Nach dem Start erreichbar unter:
- FHIR API: http://localhost:8080/fhir
- HAPI Web-UI: http://localhost:8080
- Alle CodeSystems: http://localhost:8080/fhir/CodeSystem
- Alle ValueSets: http://localhost:8080/fhir/ValueSet
- Alle ConceptMaps: http://localhost:8080/fhir/ConceptMap
- Operations prüfen: http://localhost:8080/fhir/metadata

### 2. Skripte ausführen

```bash
# Schritt 1: ELGA APPC hochladen
python appc_.py

# Schritt 2: KUK hochladen
python appc_kuk.py
```

**Weitere Optionen (beide Skripte):**
```bash
python appc_.py --test-only          # nur testen, nicht hochladen
python appc_.py --excel andere.xlsx  # andere Excel-Datei
python appc_.py --save-json          # FHIR JSON lokal speichern
```

> ⚠️ **Wichtig:** Docker hat keinen persistenten Speicher. Nach jedem Docker-Neustart müssen **beide Skripte** erneut ausgeführt werden.

---

## 🗂 Ressourcen auf dem Server nach vollständigem Durchlauf

| ID | Ressource | Inhalt |
|---|---|---|
| 1000 | CodeSystem | 1588 PR-Codes (ELGA APPC) |
| 1001 | CodeSystem | 731 APPC-Codes |
| 1002 | ValueSet | alle PR-Codes |
| 1003 | ConceptMap | PR-Code → APPC Mapping |
| 1004 | CodeSystem | 1053 KUK Leistungs-Codes |
| 1005 | CodeSystem | 146 APPC-KR Codes |
| 1006 | ValueSet | alle KUK Leistungs-Codes |
| 1007 | ConceptMap | Leistung → APPC KR Mapping |

> ⚠️ Die IDs können nach einem Docker-Neustart abweichen. Für stabile Abfragen immer die URL verwenden.

---

## 🛠 Fehlerbehebung

| Problem | Ursache | Lösung |
|---------|---------|--------|
| `$validate-code` gibt immer `False` | `hapi.fhir.cr.enabled` fehlt | Docker neu starten **mit** `-e hapi.fhir.cr.enabled=true` |
| `Connection refused` | HAPI Server läuft nicht | `docker run …` starten und warten bis Server bereit ist |
| Sheet wird übersprungen | Falscher Spaltenname | Excel prüfen: Spalten müssen exakt `PR-Code` und `APPC` heißen |
| Ressourcen fehlen nach Neustart | Docker nicht persistent | Beide Skripte nach jedem Docker-Neustart erneut ausführen |
| `validate-code` nicht in `/metadata` | CR-Modul nicht aktiv | Siehe erste Zeile dieser Tabelle |
| KUK: Spalte nicht gefunden | Falscher Spaltenname | Excel prüfen: `Leistung`, `Leistung Text`, `APPC KR` |

---

## 📁 Projektstruktur

```
APPC_Code/
├── appc_.py                # ELGA APPC Skript
├── appc_kuk.py             # KUK Leistungs-Codes Skript
├── ELGA_APPC.xlsx          # Quelldaten ELGA (nicht im Repo)
├── ELGA_APPC_KUK.xlsx      # Quelldaten KUK (nicht im Repo)
└── README.md               # Diese Dokumentation
```

---

## 🔗 Referenzen

- [HAPI FHIR Server](https://github.com/hapifhir/hapi-fhir-jpaserver-starter)
- [HL7 FHIR Spezifikation](https://www.hl7.org/fhir/)
- [FHIR Terminologie Operations](https://www.hl7.org/fhir/terminology-service.html)
- [FHIRPath Spezifikation](https://hl7.org/fhirpath/)