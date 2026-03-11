"""
test_appc_.py
==============
Tests for the ELGA APPC FHIR terminology resources.

Tests all three FHIR terminology operations:
  - $validate-code  : checks if a PR code exists in the CodeSystem
  - $translate      : maps a PR code to its APPC code via ConceptMap
  - $lookup         : returns the display text, DICOM bodypart and modality

Requirements:
    - HAPI FHIR server running on localhost:8080
    - Resources uploaded via: python src/appc_.py

Usage:
    python tests/test_appc_.py
"""

import requests
import sys
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# ==========================
# CONFIGURATION
# ==========================
FHIR_BASE   = "http://localhost:8080/fhir"
HEADERS     = {"Content-Type": "application/fhir+json"}

CS_PR_URL   = "http://elga.gv.at/fhir/CodeSystem/elga-appc-pr-codes"
CM_URL      = "http://elga.gv.at/fhir/ConceptMap/elga-pr-to-appc"

# Tracks pass/fail counts
results = {"passed": 0, "failed": 0}


# ==========================
# HELPER
# ==========================
def ok(msg):
    results["passed"] += 1
    print(f"  [✓] {msg}")

def fail(msg):
    results["failed"] += 1
    print(f"  [✗] {msg}")


# ==========================
# $VALIDATE-CODE
# ==========================
def test_validate(pr_code, should_be_valid=True):
    """
    Tests $validate-code for a given PR code.
    Tries GET type-level first, then falls back to POST with coding object.
    """
    # Attempt 1: GET type-level with url + code as query parameters
    r = requests.get(
        f"{FHIR_BASE}/CodeSystem/$validate-code",
        params={"url": CS_PR_URL, "code": pr_code},
        headers=HEADERS
    )

    # Attempt 2 fallback: POST with coding object
    if not r.ok:
        body = {
            "resourceType": "Parameters",
            "parameter": [{
                "name": "coding",
                "valueCoding": {"system": CS_PR_URL, "code": pr_code}
            }]
        }
        r = requests.post(
            f"{FHIR_BASE}/CodeSystem/$validate-code",
            headers=HEADERS,
            json=body
        )

    if not r.ok:
        fail(f"$validate-code '{pr_code}': HTTP {r.status_code}")
        return

    params = r.json().get("parameter", [])
    result = next((p["valueBoolean"] for p in params if p["name"] == "result"), False)

    if result == should_be_valid:
        ok(f"$validate-code '{pr_code}' -> valid: {result}")
    else:
        fail(f"$validate-code '{pr_code}' -> valid: {result} (expected: {should_be_valid})")


# ==========================
# $TRANSLATE
# ==========================
def test_translate(pr_code, expected_appc):
    """
    Tests $translate for a given PR code.
    Verifies that the returned APPC code matches the expected value.
    """
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
        fail(f"$translate '{pr_code}': HTTP {r.status_code}")
        return

    params = r.json().get("parameter", [])
    result = next((p["valueBoolean"] for p in params if p["name"] == "result"), False)

    if not result:
        fail(f"$translate '{pr_code}' -> no mapping found")
        return

    # Extract the mapped APPC code from the response
    appc = "?"
    match = next((p for p in params if p["name"] == "match"), None)
    if match:
        for part in match.get("part", []):
            if part["name"] == "concept":
                appc = part["valueCoding"]["code"]

    if appc == expected_appc:
        ok(f"$translate '{pr_code}' -> '{appc}'")
    else:
        fail(f"$translate '{pr_code}' -> '{appc}' (expected: '{expected_appc}')")


# ==========================
# $LOOKUP
# ==========================
def test_lookup(pr_code, expected_display=None):
    """
    Tests $lookup for a given PR code.
    Prints display text, DICOM bodypart and imaging modality.
    """
    r = requests.get(
        f"{FHIR_BASE}/CodeSystem/$lookup",
        params={"system": CS_PR_URL, "code": pr_code},
        headers=HEADERS
    )

    if not r.ok:
        fail(f"$lookup '{pr_code}': HTTP {r.status_code}")
        return

    params  = r.json().get("parameter", [])
    display = next((p.get("valueString") for p in params if p["name"] == "display"), "?")

    # Extract bodypart and modality from property list
    bodypart   = "-"
    modalitaet = "-"
    for p in params:
        if p.get("name") == "property":
            parts = p.get("part", [])
            key   = next((x.get("valueCode")   for x in parts if x.get("name") == "code"),  None)
            val   = next((x.get("valueString") for x in parts if x.get("name") == "value"), None)
            if key == "bodypart":
                bodypart = val or "-"
            elif key == "modalitaet":
                modalitaet = val or "-"

    # Check display text if expected value was provided
    if expected_display is None or display == expected_display:
        ok(f"$lookup '{pr_code}'")
    else:
        fail(f"$lookup '{pr_code}' -> display mismatch (expected: '{expected_display}', got: '{display}')")

    # Always print the full result for visibility
    print(f"        display:    '{display}'")
    print(f"        bodypart:   '{bodypart}'")
    print(f"        modality:   '{modalitaet}'")


# ==========================
# RUN TESTS
# ==========================
def main():
    print("=" * 55)
    print("  Tests: ELGA APPC")
    print("=" * 55)

    # --- $validate-code: valid codes ---
    print("\n  $validate-code (valid codes):")
    test_validate("US1AAUA",  should_be_valid=True)
    test_validate("CT1AAUA",  should_be_valid=True)
    test_validate("OP1AARA",  should_be_valid=True)
    test_validate("MR1AAUA",  should_be_valid=True)
    test_validate("DX1AAUA",  should_be_valid=True)

    # --- $validate-code: invalid code ---
    print("\n  $validate-code (invalid code):")
    test_validate("INVALID99", should_be_valid=False)

    # --- $translate ---
    print("\n  $translate (PR code -> APPC code):")
    test_translate("US1AAUA", expected_appc="4.0.0.1-1")
    test_translate("CT1AAUA", expected_appc="2.0.0.1-1")
    test_translate("OP1AARA", expected_appc="2.1.0.1-2-1-2")
    test_translate("DX1AAUA", expected_appc="1.0.0.1")
    test_translate("MR1AAUA", expected_appc="3.0.0.1-1")

    # --- $lookup ---
    print("\n  $lookup (display text, bodypart, modality):")
    test_lookup("US1AAUA")
    test_lookup("CT1AAUA")
    test_lookup("MR1AAUA")

    # --- Summary ---
    total = results["passed"] + results["failed"]
    print(f"\n{'=' * 55}")
    print(f"  Results: {results['passed']}/{total} passed", end="")
    if results["failed"] > 0:
        print(f"  |  {results['failed']} FAILED")
        sys.exit(1)
    else:
        print("  | All passed!")


if __name__ == "__main__":
    main()