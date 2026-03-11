"""
elga_appc_kuk_upload.py
========================
Liest die ELGA_APPC_KUK.xlsx, baut FHIR Terminologie-Ressourcen
und lädt sie auf den HAPI FHIR Server.

Voraussetzung:
    pip install pandas openpyxl requests

Verwendung:
    python elga_appc_kuk_upload.py
    python elga_appc_kuk_upload.py --excel pfad/zur/datei.xlsx
    python elga_appc_kuk_upload.py --test-only   (nur testen, nicht hochladen)
    python elga_appc_kuk_upload.py --save-json   (FHIR JSON lokal speichern)

Hinweis:
    Läuft auf demselben HAPI FHIR Server wie elga_appc_upload.py.
    Die URLs sind bewusst verschieden (kuk- Präfix), damit beide
    Ressourcen-Sets nebeneinander existieren können.
"""

import requests
import json
import argparse
import time
import pandas as pd

# ==========================
# KONFIGURATION
# ==========================
FHIR_BASE  = "http://localhost:8080/fhir"
HEADERS    = {"Content-Type": "application/fhir+json"}
EXCEL_FILE = "ELGA_APPC_KUK.xlsx"

# Eigene URLs – kein Konflikt mit dem bestehenden elga_appc_upload.py
CS_LEISTUNG_URL = "http://elga.gv.at/fhir/CodeSystem/elga-kuk-leistung-codes"
CS_APPC_URL     = "http://elga.gv.at/fhir/CodeSystem/elga-kuk-appc-codes"
VS_URL          = "http://elga.gv.at/fhir/ValueSet/elga-kuk-leistung-codes"
CM_URL          = "http://elga.gv.at/fhir/ConceptMap/elga-kuk-leistung-to-appc"

# Wird nach dem Upload befüllt (für Instance-Level $validate-code Fallback)
CS_LEISTUNG_INSTANCE_ID = None


# ==========================
# EXCEL EINLESEN
# ==========================
def read_excel(filepath):
    """
    Liest die ELGA_APPC_KUK.xlsx (ein Sheet: 'Tabelle1').
    Erwartet Spalten: 'Leistung', 'Leistung Text', 'APPC KR'
    Gibt eine Liste von Einträgen zurück:
        [{ leistung_code, display, appc_kr }]
    """
    print(f"[Excel] Lese Datei: {filepath}")
    df = pd.read_excel(filepath, sheet_name="Tabelle1", dtype=str)

    # Spaltennamen normalisieren (Leerzeichen entfernen)
    df.columns = [c.strip() for c in df.columns]

    required = {"Leistung", "Leistung Text", "APPC KR"}
    missing  = required - set(df.columns)
    if missing:
        raise ValueError(f"Fehlende Spalten in der Excel-Datei: {missing}\n"
                         f"Gefundene Spalten: {list(df.columns)}")

    entries    = []
    seen_codes = set()
    skipped    = 0

    for _, row in df.iterrows():
        code    = str(row["Leistung"]).strip()
        display = str(row["Leistung Text"]).strip()
        appc    = str(row["APPC KR"]).strip()

        # Ungültige Zeilen überspringen
        if not code or code == "nan" or not appc or appc == "nan":
            skipped += 1
            continue

        # Duplikate überspringen
        if code in seen_codes:
            skipped += 1
            continue
        seen_codes.add(code)

        entries.append({
            "leistung_code": code,
            "leistung_text": display if display != "nan" else code,  # Leistung Text gespeichert
            "appc_kr":       appc
        })

    print(f"  Einträge gelesen:   {len(entries)}")
    if skipped:
        print(f"  Übersprungen:       {skipped} (leer oder Duplikat)")
    print(f"  Eindeutige Codes:   {len(entries)}")
    return entries


# ==========================
# FHIR RESSOURCEN BAUEN
# ==========================
def build_leistung_code_system(entries):
    """
    CodeSystem mit allen KUK Leistungs-Codes (Quell-Katalog).
    - code:    Leistungs-Code (z.B. 'MA4')
    - display: Leistung Text (z.B. 'Digitale 2-Ebenen - Mammographie')
               → wird als menschenlesbarer Name dauerhaft gespeichert
    """
    concepts = [
        {
            "code":    e["leistung_code"],
            "display": e["leistung_text"]   # <-- Leistung Text hier gespeichert
        }
        for e in entries
    ]

    return {
        "resourceType": "CodeSystem",
        "url":          CS_LEISTUNG_URL,
        "version":      "1.0",
        "name":         "ELGAKUKLeistungCodes",
        "title":        "ELGA KUK Leistungs-Codes",
        "status":       "active",
        "description":  "Österreichische KUK Leistungs-Codes. "
                        "Jeder Code enthält den Leistung Text als 'display'.",
        "content":      "complete",
        "count":        len(concepts),
        "concept":      concepts
    }


def build_appc_code_system(entries):
    """
    CodeSystem mit allen eindeutigen APPC KR Codes (Ziel-Katalog).
    """
    seen     = set()
    concepts = []
    for e in entries:
        if e["appc_kr"] not in seen:
            seen.add(e["appc_kr"])
            concepts.append({
                "code":    e["appc_kr"],
                "display": e["appc_kr"]
            })

    return {
        "resourceType": "CodeSystem",
        "url":          CS_APPC_URL,
        "version":      "1.0",
        "name":         "ELGAKUKAPPCCodes",
        "title":        "ELGA KUK APPC-KR Codes",
        "status":       "active",
        "description":  "Österreichische APPC-KR Codes – Ziel-Katalog für das KUK Leistungs-Code Mapping.",
        "content":      "complete",
        "count":        len(concepts),
        "concept":      concepts
    }


def build_value_set(entries):
    """ValueSet das alle Leistungs-Codes einbindet."""
    return {
        "resourceType": "ValueSet",
        "url":          VS_URL,
        "version":      "1.0",
        "name":         "ELGAKUKLeistungCodesVS",
        "title":        "ELGA KUK Leistungs-Codes ValueSet",
        "status":       "active",
        "description":  "Alle gültigen ELGA KUK Leistungs-Codes",
        "compose": {
            "include": [{
                "system": CS_LEISTUNG_URL
            }]
        }
    }


def build_concept_map(entries):
    """
    ConceptMap: Leistungs-Code → APPC KR Code.
    Alles in einer Gruppe (da nur ein Sheet).
    """
    elements = [
        {
            "code":    e["leistung_code"],
            "display": e["leistung_text"],   # Leistung Text mitgeführt im Mapping
            "target": [{
                "code":        e["appc_kr"],
                "display":     e["appc_kr"],
                "equivalence": "equivalent"
            }]
        }
        for e in entries
    ]

    return {
        "resourceType": "ConceptMap",
        "url":          CM_URL,
        "version":      "1.0",
        "name":         "ELGAKUKLeistungToAPPC",
        "title":        "ELGA KUK Mapping: Leistungs-Code → APPC KR",
        "status":       "active",
        "description":  "Mapping von KUK Leistungs-Codes auf APPC-KR Codes.",
        "sourceUri":    VS_URL,
        "group": [{
            "source":  CS_LEISTUNG_URL,
            "target":  CS_APPC_URL,
            "element": elements
        }]
    }


# ==========================
# HOCHLADEN
# ==========================
def upload(resource, resource_type):
    """
    Lädt eine FHIR Ressource auf HAPI hoch (conditional PUT / upsert).
    Gibt die Instanz-ID zurück.
    """
    global CS_LEISTUNG_INSTANCE_ID

    url_param = resource.get("url", "")
    r = requests.put(
        f"{FHIR_BASE}/{resource_type}?url={url_param}",
        headers=HEADERS,
        json=resource
    )
    if r.ok:
        rid    = r.json().get("id", "?")
        action = "aktualisiert" if r.status_code == 200 else "hochgeladen"
        print(f"  [OK] {resource_type} {action} → ID: {rid}")

        if resource.get("url") == CS_LEISTUNG_URL:
            CS_LEISTUNG_INSTANCE_ID = rid

        return rid
    else:
        print(f"  [FEHLER] {resource_type}: {r.status_code}")
        print(f"           {r.text[:300]}")
        return None


# ==========================
# TESTEN
# ==========================
def test_validate(code, should_be_valid=True):
    """Testet $validate-code für einen Leistungs-Code."""
    # Variante 1: GET type-level
    r = requests.get(
        f"{FHIR_BASE}/CodeSystem/$validate-code",
        params={"url": CS_LEISTUNG_URL, "code": code},
        headers=HEADERS
    )

    # Variante 2 Fallback: Instance-Level
    if not r.ok and CS_LEISTUNG_INSTANCE_ID:
        r = requests.get(
            f"{FHIR_BASE}/CodeSystem/{CS_LEISTUNG_INSTANCE_ID}/$validate-code",
            params={"code": code},
            headers=HEADERS
        )

    # Variante 3 Fallback: POST mit coding
    if not r.ok:
        body = {
            "resourceType": "Parameters",
            "parameter": [{
                "name": "coding",
                "valueCoding": {"system": CS_LEISTUNG_URL, "code": code}
            }]
        }
        r = requests.post(
            f"{FHIR_BASE}/CodeSystem/$validate-code",
            headers=HEADERS,
            json=body
        )

    if not r.ok:
        print(f"  [FEHLER] $validate-code: {r.status_code} {r.text[:100]}")
        return

    params  = r.json().get("parameter", [])
    result  = next((p["valueBoolean"] for p in params if p["name"] == "result"), False)
    correct = result == should_be_valid
    status  = "✓" if correct else "✗"
    print(f"  [{status}] $validate-code '{code}' → gültig: {result}")


def test_translate(code, expected_appc=None):
    """Testet $translate für einen Leistungs-Code."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "url",    "valueUri":  CM_URL},
            {"name": "system", "valueUri":  CS_LEISTUNG_URL},
            {"name": "code",   "valueCode": code}
        ]
    }
    r = requests.post(
        f"{FHIR_BASE}/ConceptMap/$translate",
        headers=HEADERS,
        json=body
    )
    if not r.ok:
        print(f"  [FEHLER] $translate für '{code}': {r.status_code}")
        return

    params = r.json().get("parameter", [])
    result = next((p["valueBoolean"] for p in params if p["name"] == "result"), False)

    if result:
        match = next((p for p in params if p["name"] == "match"), None)
        appc  = "?"
        if match:
            for part in match.get("part", []):
                if part["name"] == "concept":
                    appc = part["valueCoding"]["code"]

        status = "✓" if (expected_appc is None or appc == expected_appc) else "✗"
        print(f"  [{status}] $translate '{code}' → '{appc}'", end="")
        if expected_appc and appc != expected_appc:
            print(f"  (erwartet: '{expected_appc}')", end="")
        print()
    else:
        print(f"  [✗] $translate '{code}' → kein Mapping gefunden")


def test_lookup(code, expected_display=None):
    """
    Testet $lookup – gibt den gespeicherten Leistung Text zurück.
    So kann man prüfen ob der Text korrekt abgespeichert wurde.
    """
    r = requests.get(
        f"{FHIR_BASE}/CodeSystem/$lookup",
        params={"system": CS_LEISTUNG_URL, "code": code},
        headers=HEADERS
    )
    if not r.ok:
        print(f"  [FEHLER] $lookup '{code}': {r.status_code}")
        return

    params  = r.json().get("parameter", [])
    display = next((p.get("valueString") for p in params if p["name"] == "display"), "?")
    correct = expected_display is None or display == expected_display
    status  = "✓" if correct else "✗"
    print(f"  [{status}] $lookup '{code}' → '{display}'")


# ==========================
# MAIN
# ==========================
def main():
    parser = argparse.ArgumentParser(description="ELGA APPC KUK → FHIR Terminologie-Server")
    parser.add_argument("--excel",     default=EXCEL_FILE, help="Pfad zur ELGA_APPC_KUK.xlsx")
    parser.add_argument("--test-only", action="store_true", help="Nur testen, nicht hochladen")
    parser.add_argument("--save-json", action="store_true", help="FHIR JSON Dateien lokal speichern")
    args = parser.parse_args()

    print("=" * 55)
    print("  ELGA APPC KUK → FHIR Terminologie-Server")
    print("=" * 55)

    # 1. Excel einlesen
    print("\n--- 1. Excel einlesen ---")
    entries = read_excel(args.excel)

    # 2. FHIR Ressourcen bauen
    print("\n--- 2. FHIR Ressourcen bauen ---")
    leistung_cs = build_leistung_code_system(entries)
    appc_cs     = build_appc_code_system(entries)
    vs          = build_value_set(entries)
    cm          = build_concept_map(entries)

    appc_count = len(set(e["appc_kr"] for e in entries))
    print(f"  CodeSystem Leistungs-Codes: {len(entries)} Codes")
    print(f"  CodeSystem APPC KR:         {appc_count} eindeutige APPC-KR Codes")
    print(f"  ValueSet:                   1 (referenziert alle Leistungs-Codes)")
    print(f"  ConceptMap:                 1 Gruppe ({len(entries)} Mappings)")

    # Optional: JSON lokal speichern
    if args.save_json:
        print("\n--- JSON Dateien speichern ---")
        files = {
            "kuk_leistung_code_system.json": leistung_cs,
            "kuk_appc_code_system.json":     appc_cs,
            "kuk_value_set.json":            vs,
            "kuk_concept_map.json":          cm
        }
        for fname, resource in files.items():
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(resource, f, ensure_ascii=False, indent=2)
            print(f"  Gespeichert: {fname}")

    # 3. Hochladen
    if not args.test_only:
        print("\n--- 3. Ressourcen hochladen ---")
        upload(leistung_cs, "CodeSystem")
        upload(appc_cs,     "CodeSystem")
        upload(vs,          "ValueSet")
        upload(cm,          "ConceptMap")
    else:
        print("\n[--test-only] Upload übersprungen")

    # 4. Testen
    print("\n--- 4. Tests ---")
    print("  [warte 2s auf HAPI-Index...]")
    time.sleep(2)

    # Erste 3 Codes aus der Excel als Testfälle
    test_codes = [e["leistung_code"] for e in entries[:3]]

    print("\n  $validate-code (gültige Codes):")
    for code in test_codes:
        test_validate(code, should_be_valid=True)

    print("\n  $validate-code (ungültiger Code):")
    test_validate("INVALID_KUK_99", should_be_valid=False)

    print("\n  $translate (Leistungs-Code → APPC KR):")
    for e in entries[:3]:
        test_translate(e["leistung_code"], expected_appc=e["appc_kr"])

    print("\n  $lookup (Leistung Text abrufen):")
    for e in entries[:3]:
        test_lookup(e["leistung_code"], expected_display=e["leistung_text"])

    print("\nFertig!")


if __name__ == "__main__":
    main()