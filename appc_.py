"""
elga_appc_upload.py
====================
Liest die ELGA_APPC.xlsx, baut FHIR Terminologie-Ressourcen
und lädt sie auf den HAPI FHIR Server.

Voraussetzung:
    pip install pandas openpyxl requests

Verwendung:
    python elga_appc_upload.py
    python elga_appc_upload.py --excel pfad/zur/datei.xlsx
    python elga_appc_upload.py --test-only   (nur $translate testen, nicht hochladen)
"""

import requests
import json
import argparse
import sys
import time
import pandas as pd

# ==========================
# KONFIGURATION
# ==========================
FHIR_BASE   = "http://localhost:8080/fhir"
HEADERS     = {"Content-Type": "application/fhir+json"}
EXCEL_FILE  = "ELGA_APPC.xlsx"

CS_PR_URL   = "http://elga.gv.at/fhir/CodeSystem/elga-appc-pr-codes"
CS_APPC_URL = "http://elga.gv.at/fhir/CodeSystem/elga-appc-codes"
VS_URL      = "http://elga.gv.at/fhir/ValueSet/elga-appc-pr-codes"
CM_URL      = "http://elga.gv.at/fhir/ConceptMap/elga-pr-to-appc"

# Wird nach dem Upload befüllt (für Instance-Level $validate-code Fallback)
CS_PR_INSTANCE_ID = None


# ==========================
# EXCEL EINLESEN
# ==========================
def read_excel(filepath):
    """
    Liest alle Sheets der ELGA_APPC.xlsx und gibt eine Liste
    von Einträgen zurück: [{pr_code, display, bodypart, appc, modalitaet}]
    """
    print(f"[Excel] Lese Datei: {filepath}")
    all_sheets = pd.read_excel(filepath, sheet_name=None, header=None)

    entries = []
    seen_pr = set()

    for sheet_name, df in all_sheets.items():

        # Header-Zeile automatisch finden
        header_row = pr_col = appc_col = proto_col = bodypart_col = None
        for i, row in df.iterrows():
            vals = [str(v) for v in row.values]
            if "PR-Code" in vals:
                header_row   = i
                pr_col       = vals.index("PR-Code")
                appc_col     = vals.index("APPC") if "APPC" in vals else None
                bodypart_col = vals.index("Dicom Bodypart") if "Dicom Bodypart" in vals else None
                proto_col    = (
                    vals.index("Untersuchungsprotokoll an der Modalität")
                    if "Untersuchungsprotokoll an der Modalität" in vals
                    else vals.index("Untersuchungsprotokoll")
                    if "Untersuchungsprotokoll" in vals
                    else None
                )
                break

        if header_row is None or appc_col is None:
            print(f"  [!] Sheet '{sheet_name}' übersprungen – kein PR-Code/APPC Header gefunden")
            continue

        sheet_count = 0
        for _, row in df.iloc[header_row + 1:].iterrows():
            pr    = str(row.iloc[pr_col]).strip()
            appc  = str(row.iloc[appc_col]).strip()
            proto = str(row.iloc[proto_col]).strip() if proto_col is not None else ""
            bp    = str(row.iloc[bodypart_col]).strip() if bodypart_col is not None else ""

            # Ungültige Zeilen überspringen
            if not pr or pr == "nan" or not appc or appc == "nan":
                continue

            # Duplikate überspringen (ersten Eintrag behalten)
            if pr in seen_pr:
                continue
            seen_pr.add(pr)

            entries.append({
                "modalitaet": sheet_name,
                "pr_code":    pr,
                "display":    proto if proto != "nan" else pr,
                "bodypart":   bp    if bp    != "nan" else "",
                "appc":       appc
            })
            sheet_count += 1

        print(f"  Sheet '{sheet_name}': {sheet_count} Einträge gelesen")

    print(f"[Excel] Gesamt: {len(entries)} eindeutige PR-Codes")
    return entries


# ==========================
# FHIR RESSOURCEN BAUEN
# ==========================
def build_pr_code_system(entries):
    """
    CodeSystem mit allen PR-Codes.
    Jeder Code hat: code, display (Untersuchungsprotokoll), property (bodypart, modalitaet)
    """
    concepts = []
    for e in entries:
        properties = []
        if e["bodypart"]:
            properties.append({"code": "bodypart",   "valueString": e["bodypart"]})
        if e["modalitaet"]:
            properties.append({"code": "modalitaet", "valueString": e["modalitaet"]})

        concept = {
            "code":     e["pr_code"],
            "display":  e["display"],
            "property": properties
        }
        concepts.append(concept)

    return {
        "resourceType": "CodeSystem",
        "url":          CS_PR_URL,
        "version":      "1.0",
        "name":         "ELGAAPPCPRCodes",
        "title":        "ELGA APPC PR-Codes",
        "status":       "active",
        "description":  "Österreichische Radiologie PR-Codes (Protocol Codes) aus der ELGA APPC Liste. Enthält alle Untersuchungsprotokolle aller Modalitäten (US, CT, MR, RÖ, etc.)",
        "content":      "complete",
        "count":        len(concepts),
        "property": [
            {
                "code":        "bodypart",
                "description": "DICOM Body Part",
                "type":        "string"
            },
            {
                "code":        "modalitaet",
                "description": "Radiologische Modalität (z.B. CT, MR, US)",
                "type":        "string"
            }
        ],
        "concept": concepts
    }


def build_appc_code_system(entries):
    """
    CodeSystem mit allen eindeutigen APPC-Codes als Ziel-Katalog.
    """
    seen = set()
    concepts = []
    for e in entries:
        if e["appc"] not in seen:
            seen.add(e["appc"])
            concepts.append({
                "code":    e["appc"],
                "display": e["appc"]
            })

    return {
        "resourceType": "CodeSystem",
        "url":          CS_APPC_URL,
        "version":      "1.0",
        "name":         "ELGAAPPCCodes",
        "title":        "ELGA APPC Codes",
        "status":       "active",
        "description":  "Österreichische APPC-Codes (Austrian Protocol for Procedure Codes) – Ziel-Katalog für das PR-Code Mapping",
        "content":      "complete",
        "count":        len(concepts),
        "concept":      concepts
    }


def build_value_set(entries):
    """ValueSet das alle PR-Codes aus dem CodeSystem einbindet."""
    return {
        "resourceType": "ValueSet",
        "url":          VS_URL,
        "version":      "1.0",
        "name":         "ELGAAPPCPRCodesVS",
        "title":        "ELGA APPC PR-Codes ValueSet",
        "status":       "active",
        "description":  "Alle gültigen ELGA APPC PR-Codes",
        "compose": {
            "include": [{
                "system": CS_PR_URL
            }]
        }
    }


def build_concept_map(entries):
    """
    ConceptMap: PR-Code → APPC Code.
    Gruppiert nach Modalität für bessere Übersicht.
    """
    by_modalitaet = {}
    for e in entries:
        m = e["modalitaet"]
        if m not in by_modalitaet:
            by_modalitaet[m] = []
        by_modalitaet[m].append(e)

    groups = []
    for modalitaet, modal_entries in by_modalitaet.items():
        elements = []
        for e in modal_entries:
            elements.append({
                "code":    e["pr_code"],
                "display": e["display"],
                "target": [{
                    "code":        e["appc"],
                    "display":     e["appc"],
                    "equivalence": "equivalent"
                }]
            })
        groups.append({
            "source":  CS_PR_URL,
            "target":  CS_APPC_URL,
            "element": elements
        })

    return {
        "resourceType": "ConceptMap",
        "url":          CM_URL,
        "version":      "1.0",
        "name":         "ELGAPRToAPPC",
        "title":        "ELGA Mapping: PR-Code → APPC",
        "status":       "active",
        "description":  "Mapping von österreichischen Radiologie PR-Codes auf APPC-Codes (Austrian Protocol for Procedure Codes)",
        "sourceUri":    VS_URL,
        "group":        groups
    }


# ==========================
# HOCHLADEN
# ==========================
def upload(resource, resource_type):
    """
    Lädt eine FHIR Ressource auf HAPI hoch.
    Verwendet conditional PUT (upsert) → funktioniert auch wenn Ressource schon existiert.
    Gibt die Instanz-ID zurück.
    """
    global CS_PR_INSTANCE_ID

    url_param = resource.get("url", "")
    r = requests.put(
        f"{FHIR_BASE}/{resource_type}?url={url_param}",
        headers=HEADERS,
        json=resource
    )
    if r.ok:
        rid = r.json().get("id", "?")
        action = "aktualisiert" if r.status_code == 200 else "hochgeladen"
        print(f"  [OK] {resource_type} {action} → ID: {rid}")

        # PR-CodeSystem ID merken für $validate-code Fallback
        if resource.get("url") == CS_PR_URL:
            CS_PR_INSTANCE_ID = rid

        return rid
    else:
        print(f"  [FEHLER] {resource_type}: {r.status_code}")
        print(f"           {r.text[:300]}")
        return None


# ==========================
# TESTEN
# ==========================
def test_translate(pr_code, expected_appc=None):
    """Testet $translate für einen PR-Code."""
    body = {
        "resourceType": "Parameters",
        "parameter": [
            {"name": "url",    "valueUri":  CM_URL},
            {"name": "system", "valueUri":  CS_PR_URL},
            {"name": "code",   "valueCode": pr_code}
        ]
    }
    r = requests.post(
        f"{FHIR_BASE}/ConceptMap/$translate",
        headers=HEADERS,
        json=body
    )
    if not r.ok:
        print(f"  [FEHLER] $translate für '{pr_code}': {r.status_code}")
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
        print(f"  [{status}] $translate '{pr_code}' → '{appc}'", end="")
        if expected_appc and appc != expected_appc:
            print(f"  (erwartet: '{expected_appc}')", end="")
        print()
    else:
        print(f"  [✗] $translate '{pr_code}' → kein Mapping gefunden")


def test_validate(pr_code, should_be_valid=True):
    """
    Testet $validate-code für einen PR-Code.
    Versucht zuerst GET type-level, dann Instance-Level als Fallback.
    """
    # Variante 1: GET type-level mit url + code als Query-Parameter
    r = requests.get(
        f"{FHIR_BASE}/CodeSystem/$validate-code",
        params={
            "url":  CS_PR_URL,
            "code": pr_code
        },
        headers=HEADERS
    )

    # Variante 2 Fallback: Instance-Level direkt auf die Ressource
    if not r.ok and CS_PR_INSTANCE_ID:
        r = requests.get(
            f"{FHIR_BASE}/CodeSystem/{CS_PR_INSTANCE_ID}/$validate-code",
            params={"code": pr_code},
            headers=HEADERS
        )

    # Variante 3 Fallback: POST mit coding-Objekt
    if not r.ok:
        body = {
            "resourceType": "Parameters",
            "parameter": [
                {
                    "name": "coding",
                    "valueCoding": {
                        "system": CS_PR_URL,
                        "code":   pr_code
                    }
                }
            ]
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
    print(f"  [{status}] $validate-code '{pr_code}' → gültig: {result}")


# ==========================
# MAIN
# ==========================
def main():
    parser = argparse.ArgumentParser(description="ELGA APPC → FHIR Terminologie-Server")
    parser.add_argument("--excel",     default=EXCEL_FILE, help="Pfad zur ELGA_APPC.xlsx")
    parser.add_argument("--test-only", action="store_true", help="Nur testen, nicht hochladen")
    parser.add_argument("--save-json", action="store_true", help="FHIR JSON Dateien lokal speichern")
    args = parser.parse_args()

    print("=" * 55)
    print("  ELGA APPC → FHIR Terminologie-Server")
    print("=" * 55)

    # 1. Excel einlesen
    print("\n--- 1. Excel einlesen ---")
    entries = read_excel(args.excel)

    # 2. FHIR Ressourcen bauen
    print("\n--- 2. FHIR Ressourcen bauen ---")
    pr_cs    = build_pr_code_system(entries)
    appc_cs  = build_appc_code_system(entries)
    vs       = build_value_set(entries)
    cm       = build_concept_map(entries)

    appc_count = len(set(e["appc"] for e in entries))
    print(f"  CodeSystem PR-Codes:  {len(entries)} Codes")
    print(f"  CodeSystem APPC:      {appc_count} eindeutige APPC-Codes")
    print(f"  ValueSet:             1 (referenziert alle PR-Codes)")
    print(f"  ConceptMap:           {len(cm['group'])} Gruppen (je Modalität)")

    # Optional: JSON lokal speichern
    if args.save_json:
        print("\n--- JSON Dateien speichern ---")
        files = {
            "elga_pr_code_system.json":   pr_cs,
            "elga_appc_code_system.json": appc_cs,
            "elga_value_set.json":        vs,
            "elga_concept_map.json":      cm
        }
        for fname, resource in files.items():
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(resource, f, ensure_ascii=False, indent=2)
            print(f"  Gespeichert: {fname}")

    # 3. Hochladen
    if not args.test_only:
        print("\n--- 3. Ressourcen hochladen ---")
        upload(pr_cs,   "CodeSystem")
        upload(appc_cs, "CodeSystem")
        upload(vs,      "ValueSet")
        upload(cm,      "ConceptMap")
    else:
        print("\n[--test-only] Upload übersprungen")

    # 4. Testen (kurz warten damit HAPI den Index aufbaut)
    print("\n--- 4. Tests ---")
    print("  [warte 2s auf HAPI-Index...]")
    time.sleep(2)

    print("\n  $validate-code (gültige Codes):")
    test_validate("US1AAUA",  should_be_valid=True)
    test_validate("CT1AAUA",  should_be_valid=True)
    test_validate("OP1AARA",  should_be_valid=True)

    print("\n  $validate-code (ungültiger Code):")
    test_validate("INVALID99", should_be_valid=False)

    print("\n  $translate (PR-Code → APPC):")
    test_translate("US1AAUA",  expected_appc="4.0.0.1-1")
    test_translate("CT1AAUA",  expected_appc="2.0.0.1-1")
    test_translate("OP1AARA",  expected_appc="2.1.0.1-2-1-2")
    test_translate("DX1AAUA",  expected_appc="1.0.0.1")
    test_translate("MR1AAUA",  expected_appc="3.0.0.1-1")

    print("\nFertig!")


if __name__ == "__main__":
    main()