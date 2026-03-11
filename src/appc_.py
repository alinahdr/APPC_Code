"""
appc_.py
=========
Reads ELGA_APPC.xlsx, builds FHIR terminology resources
and uploads them to the local HAPI FHIR server.

Requirements:
    pip install pandas openpyxl requests

Usage:
    python src/appc_.py
    python src/appc_.py --excel data/ELGA_APPC.xlsx
    python src/appc_.py --test-only
    python src/appc_.py --save-json
"""

import requests
import json
import argparse
import time
import pandas as pd
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ==========================
# CONFIGURATION
# ==========================
FHIR_BASE   = "http://localhost:8080/fhir"
HEADERS     = {"Content-Type": "application/fhir+json"}
EXCEL_FILE  = "../data/ELGA_APPC.xlsx"

CS_PR_URL   = "http://elga.gv.at/fhir/CodeSystem/elga-appc-pr-codes"
CS_APPC_URL = "http://elga.gv.at/fhir/CodeSystem/elga-appc-codes"
VS_URL      = "http://elga.gv.at/fhir/ValueSet/elga-appc-pr-codes"
CM_URL      = "http://elga.gv.at/fhir/ConceptMap/elga-pr-to-appc"

# Filled after upload – used as fallback for instance-level $validate-code
CS_PR_INSTANCE_ID = None


# ==========================
# READ EXCEL
# ==========================
def read_excel(filepath):
    """
    Reads all sheets of ELGA_APPC.xlsx.
    Returns a list of entries: [{pr_code, display, bodypart, appc, modalitaet}]
    Each sheet represents one imaging modality (US, CT, MR, etc.)
    """
    print(f"[Excel] Reading file: {filepath}")
    all_sheets = pd.read_excel(filepath, sheet_name=None, header=None)

    entries = []
    seen_pr = set()

    for sheet_name, df in all_sheets.items():

        # Automatically detect header row by looking for 'PR-Code' column
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
            print(f"  [!] Sheet '{sheet_name}' skipped – no PR-Code/APPC header found")
            continue

        sheet_count = 0
        for _, row in df.iloc[header_row + 1:].iterrows():
            pr    = str(row.iloc[pr_col]).strip()
            appc  = str(row.iloc[appc_col]).strip()
            proto = str(row.iloc[proto_col]).strip() if proto_col is not None else ""
            bp    = str(row.iloc[bodypart_col]).strip() if bodypart_col is not None else ""

            # Skip empty or invalid rows
            if not pr or pr == "nan" or not appc or appc == "nan":
                continue

            # Skip duplicates – keep first occurrence
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

        print(f"  Sheet '{sheet_name}': {sheet_count} entries read")

    print(f"[Excel] Total: {len(entries)} unique PR codes")
    return entries


# ==========================
# BUILD FHIR RESOURCES
# ==========================
def build_pr_code_system(entries):
    """
    Builds a CodeSystem for all PR codes (source catalog).
    Each concept includes:
      - code:    PR code (e.g. 'US1AAUA')
      - display: examination protocol name (human-readable)
      - property: DICOM bodypart and imaging modality
    """
    concepts = []
    for e in entries:
        properties = []
        if e["bodypart"]:
            properties.append({"code": "bodypart",   "valueString": e["bodypart"]})
        if e["modalitaet"]:
            properties.append({"code": "modalitaet", "valueString": e["modalitaet"]})

        concepts.append({
            "code":     e["pr_code"],
            "display":  e["display"],
            "property": properties
        })

    return {
        "resourceType": "CodeSystem",
        "url":          CS_PR_URL,
        "version":      "1.0",
        "name":         "ELGAAPPCPRCodes",
        "title":        "ELGA APPC PR-Codes",
        "status":       "active",
        "description":  "Austrian radiology PR codes (Protocol Codes) from the ELGA APPC list. Contains all examination protocols for all modalities (US, CT, MR, RO, etc.)",
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
                "description": "Imaging modality (e.g. CT, MR, US)",
                "type":        "string"
            }
        ],
        "concept": concepts
    }


def build_appc_code_system(entries):
    """
    Builds a CodeSystem for all unique APPC codes (target catalog).
    """
    seen     = set()
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
        "description":  "Austrian APPC codes (Austrian Protocol for Procedure Codes) – target catalog for PR code mapping",
        "content":      "complete",
        "count":        len(concepts),
        "concept":      concepts
    }


def build_value_set(entries):
    """
    Builds a ValueSet that includes all PR codes from the CodeSystem.
    """
    return {
        "resourceType": "ValueSet",
        "url":          VS_URL,
        "version":      "1.0",
        "name":         "ELGAAPPCPRCodesVS",
        "title":        "ELGA APPC PR-Codes ValueSet",
        "status":       "active",
        "description":  "All valid ELGA APPC PR codes",
        "compose": {
            "include": [{
                "system": CS_PR_URL
            }]
        }
    }


def build_concept_map(entries):
    """
    Builds a ConceptMap: PR code -> APPC code.
    Grouped by modality for better readability.
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
        "title":        "ELGA Mapping: PR-Code -> APPC",
        "status":       "active",
        "description":  "Mapping of Austrian radiology PR codes to APPC codes (Austrian Protocol for Procedure Codes)",
        "sourceUri":    VS_URL,
        "group":        groups
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
    global CS_PR_INSTANCE_ID

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

        # Store PR CodeSystem ID for $validate-code instance-level fallback
        if resource.get("url") == CS_PR_URL:
            CS_PR_INSTANCE_ID = rid

        return rid
    else:
        print(f"  [ERROR] {resource_type}: {r.status_code}")
        print(f"          {r.text[:300]}")
        return None


# ==========================
# MAIN
# ==========================
def main():
    parser = argparse.ArgumentParser(description="ELGA APPC -> FHIR Terminology Server")
    parser.add_argument("--excel",     default=EXCEL_FILE, help="Path to ELGA_APPC.xlsx")
    parser.add_argument("--test-only", action="store_true", help="Only test, do not upload")
    parser.add_argument("--save-json", action="store_true", help="Save FHIR JSON files locally")
    args = parser.parse_args()

    print("=" * 55)
    print("  ELGA APPC -> FHIR Terminology Server")
    print("=" * 55)

    # Step 1: Read Excel
    print("\n--- 1. Read Excel ---")
    entries = read_excel(args.excel)

    # Step 2: Build FHIR resources
    print("\n--- 2. Build FHIR resources ---")
    pr_cs   = build_pr_code_system(entries)
    appc_cs = build_appc_code_system(entries)
    vs      = build_value_set(entries)
    cm      = build_concept_map(entries)

    appc_count = len(set(e["appc"] for e in entries))
    print(f"  CodeSystem PR codes:  {len(entries)} codes")
    print(f"  CodeSystem APPC:      {appc_count} unique APPC codes")
    print(f"  ValueSet:             1 (includes all PR codes)")
    print(f"  ConceptMap:           {len(cm['group'])} groups (one per modality)")

    # Optional: save JSON files locally
    if args.save_json:
        print("\n--- Save JSON files ---")
        files = {
            "elga_pr_code_system.json":   pr_cs,
            "elga_appc_code_system.json": appc_cs,
            "elga_value_set.json":        vs,
            "elga_concept_map.json":      cm
        }
        for fname, resource in files.items():
            with open(fname, "w", encoding="utf-8") as f:
                json.dump(resource, f, ensure_ascii=False, indent=2)
            print(f"  Saved: {fname}")

    # Step 3: Upload
    if not args.test_only:
        print("\n--- 3. Upload resources ---")
        upload(pr_cs,   "CodeSystem")
        upload(appc_cs, "CodeSystem")
        upload(vs,      "ValueSet")
        upload(cm,      "ConceptMap")
    else:
        print("\n[--test-only] Upload skipped")

    print("\nDone! Run tests with: python tests/test_appc_.py")


if __name__ == "__main__":
    main()