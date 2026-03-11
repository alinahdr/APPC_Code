"""
appc_kuk.py
============
Reads ELGA_APPC_KUK.xlsx, builds FHIR terminology resources
and uploads them to the local HAPI FHIR server.

Runs on the same HAPI FHIR server as appc_.py.
URLs use a 'kuk-' prefix to avoid conflicts with the ELGA APPC resources.

Requirements:
    pip install pandas openpyxl requests

Usage:
    python src/appc_kuk.py
    python src/appc_kuk.py --excel data/ELGA_APPC_KUK.xlsx
    python src/appc_kuk.py --test-only
    python src/appc_kuk.py --save-json
"""
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
import json
import argparse
import time
import pandas as pd

# ==========================
# CONFIGURATION
# ==========================
FHIR_BASE  = "http://localhost:8080/fhir"
HEADERS    = {"Content-Type": "application/fhir+json"}
EXCEL_FILE = "data/ELGA_APPC_KUK.xlsx"

# Separate URLs – no conflict with appc_.py resources
CS_LEISTUNG_URL = "http://elga.gv.at/fhir/CodeSystem/elga-kuk-leistung-codes"
CS_APPC_URL     = "http://elga.gv.at/fhir/CodeSystem/elga-kuk-appc-codes"
VS_URL          = "http://elga.gv.at/fhir/ValueSet/elga-kuk-leistung-codes"
CM_URL          = "http://elga.gv.at/fhir/ConceptMap/elga-kuk-leistung-to-appc"

# Filled after upload – used as fallback for instance-level $validate-code
CS_LEISTUNG_INSTANCE_ID = None


# ==========================
# READ EXCEL
# ==========================
def read_excel(filepath):
    """
    Reads ELGA_APPC_KUK.xlsx (single sheet: 'Tabelle1').
    Expected columns: 'Leistung', 'Leistung Text', 'APPC KR'
    Returns a list of entries: [{leistung_code, leistung_text, appc_kr}]
    """
    print(f"[Excel] Reading file: {filepath}")
    df = pd.read_excel(filepath, sheet_name="Tabelle1", dtype=str)

    # Normalize column names (strip whitespace)
    df.columns = [c.strip() for c in df.columns]

    required = {"Leistung", "Leistung Text", "APPC KR"}
    missing  = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in Excel file: {missing}\n"
                         f"Found columns: {list(df.columns)}")

    entries    = []
    seen_codes = set()
    skipped    = 0

    for _, row in df.iterrows():
        code    = str(row["Leistung"]).strip()
        display = str(row["Leistung Text"]).strip()
        appc    = str(row["APPC KR"]).strip()

        # Skip empty or invalid rows
        if not code or code == "nan" or not appc or appc == "nan":
            skipped += 1
            continue

        # Skip duplicates – keep first occurrence
        if code in seen_codes:
            skipped += 1
            continue
        seen_codes.add(code)

        entries.append({
            "leistung_code": code,
            "leistung_text": display if display != "nan" else code,
            "appc_kr":       appc
        })

    print(f"  Entries read:     {len(entries)}")
    if skipped:
        print(f"  Skipped:          {skipped} (empty or duplicate)")
    return entries


# ==========================
# BUILD FHIR RESOURCES
# ==========================
def build_leistung_code_system(entries):
    """
    Builds a CodeSystem for all KUK service codes (source catalog).
    Each concept includes:
      - code:    service code (e.g. 'MA4')
      - display: service description stored as human-readable text
                 (e.g. 'Digitale 2-Ebenen - Mammographie')
    """
    concepts = [
        {
            "code":    e["leistung_code"],
            "display": e["leistung_text"]
        }
        for e in entries
    ]

    return {
        "resourceType": "CodeSystem",
        "url":          CS_LEISTUNG_URL,
        "version":      "1.0",
        "name":         "ELGAKUKLeistungCodes",
        "title":        "ELGA KUK Service Codes",
        "status":       "active",
        "description":  "Austrian KUK service codes. Each code contains the service description as 'display'.",
        "content":      "complete",
        "count":        len(concepts),
        "concept":      concepts
    }


def build_appc_code_system(entries):
    """
    Builds a CodeSystem for all unique APPC KR codes (target catalog).
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
        "description":  "Austrian APPC-KR codes – target catalog for KUK service code mapping.",
        "content":      "complete",
        "count":        len(concepts),
        "concept":      concepts
    }


def build_value_set(entries):
    """
    Builds a ValueSet that includes all KUK service codes.
    """
    return {
        "resourceType": "ValueSet",
        "url":          VS_URL,
        "version":      "1.0",
        "name":         "ELGAKUKLeistungCodesVS",
        "title":        "ELGA KUK Service Codes ValueSet",
        "status":       "active",
        "description":  "All valid ELGA KUK service codes",
        "compose": {
            "include": [{
                "system": CS_LEISTUNG_URL
            }]
        }
    }


def build_concept_map(entries):
    """
    Builds a ConceptMap: service code -> APPC KR code.
    Single group (one sheet in the source Excel).
    """
    elements = [
        {
            "code":    e["leistung_code"],
            "display": e["leistung_text"],
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
        "title":        "ELGA KUK Mapping: Service Code -> APPC KR",
        "status":       "active",
        "description":  "Mapping of KUK service codes to APPC-KR codes.",
        "sourceUri":    VS_URL,
        "group": [{
            "source":  CS_LEISTUNG_URL,
            "target":  CS_APPC_URL,
            "element": elements
        }]
    }


# ==========================
# UPLOAD
# ==========================
def upload(resource, resource_type):
    """
    Uploads a FHIR resource to HAPI using conditional PUT (upsert).
    Works even if the resource already exists.
    Returns the instance ID.
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
        action = "updated" if r.status_code == 200 else "uploaded"
        print(f"  [OK] {resource_type} {action} -> ID: {rid}")

        # Store Leistung CodeSystem ID for $validate-code instance-level fallback
        if resource.get("url") == CS_LEISTUNG_URL:
            CS_LEISTUNG_INSTANCE_ID = rid

        return rid
    else:
        print(f"  [ERROR] {resource_type}: {r.status_code}")
        print(f"          {r.text[:300]}")
        return None


# ==========================
# MAIN
# ==========================
def main():
    parser = argparse.ArgumentParser(description="ELGA APPC KUK -> FHIR Terminology Server")
    parser.add_argument("--excel",     default=EXCEL_FILE, help="Path to ELGA_APPC_KUK.xlsx")
    parser.add_argument("--test-only", action="store_true", help="Only test, do not upload")
    parser.add_argument("--save-json", action="store_true", help="Save FHIR JSON files locally")
    args = parser.parse_args()

    print("=" * 55)
    print("  ELGA APPC KUK -> FHIR Terminology Server")
    print("=" * 55)

    # Step 1: Read Excel
    print("\n--- 1. Read Excel ---")
    entries = read_excel(args.excel)

    # Step 2: Build FHIR resources
    print("\n--- 2. Build FHIR resources ---")
    leistung_cs = build_leistung_code_system(entries)
    appc_cs     = build_appc_code_system(entries)
    vs          = build_value_set(entries)
    cm          = build_concept_map(entries)

    appc_count = len(set(e["appc_kr"] for e in entries))
    print(f"  CodeSystem service codes: {len(entries)} codes")
    print(f"  CodeSystem APPC KR:       {appc_count} unique APPC-KR codes")
    print(f"  ValueSet:                 1 (includes all service codes)")
    print(f"  ConceptMap:               1 group ({len(entries)} mappings)")

    # Optional: save JSON files locally
    if args.save_json:
        print("\n--- Save JSON files ---")
        files = {
            "kuk_leistung_code_system.json": leistung_cs,
            "kuk_appc_code_system.json":     appc_cs,
            "kuk_value_set.json":            vs,
            "kuk_concept_map.json":          cm
        }
        for fname, resource in files.items():
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(resource, f, ensure_ascii=False, indent=2)
            print(f"  Saved: {fname}")

    # Step 3: Upload
    if not args.test_only:
        print("\n--- 3. Upload resources ---")
        upload(leistung_cs, "CodeSystem")
        upload(appc_cs,     "CodeSystem")
        upload(vs,          "ValueSet")
        upload(cm,          "ConceptMap")
    else:
        print("\n[--test-only] Upload skipped")

    print("\nDone! Run tests with: python tests/test_appc_kuk.py")


if __name__ == "__main__":
    main()