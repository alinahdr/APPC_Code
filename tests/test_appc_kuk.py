"""
test_appc_kuk.py
=================
Tests for the ELGA APPC KUK FHIR terminology resources.

Tests all three FHIR terminology operations:
  - $validate-code  : checks if a service code exists in the CodeSystem
  - $translate      : maps a service code to its APPC KR code via ConceptMap
  - $lookup         : returns the human-readable service description

Requirements:
    - HAPI FHIR server running on localhost:8080
    - Resources uploaded via: python src/appc_kuk.py

Usage:
    python tests/test_appc_kuk.py
"""

import requests
import sys
import os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ==========================
# CONFIGURATION
# ==========================
FHIR_BASE       = "http://localhost:8080/fhir"
HEADERS         = {"Content-Type": "application/fhir+json"}

CS_LEISTUNG_URL = "http://elga.gv.at/fhir/CodeSystem/elga-kuk-leistung-codes"
CM_URL          = "http://elga.gv.at/fhir/ConceptMap/elga-kuk-leistung-to-appc"

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
def test_validate(code, should_be_valid=True):
    """
    Tests $validate-code for a given service code.
    Tries GET type-level first, then falls back to POST with coding object.
    """
    # Attempt 1: GET type-level with url + code as query parameters
    r = requests.get(
        f"{FHIR_BASE}/CodeSystem/$validate-code",
        params={"url": CS_LEISTUNG_URL, "code": code},
        headers=HEADERS
    )

    # Attempt 2 fallback: POST with coding object
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
        fail(f"$validate-code '{code}': HTTP {r.status_code}")
        return

    params = r.json().get("parameter", [])
    result = next((p["valueBoolean"] for p in params if p["name"] == "result"), False)

    if result == should_be_valid:
        ok(f"$validate-code '{code}' -> valid: {result}")
    else:
        fail(f"$validate-code '{code}' -> valid: {result} (expected: {should_be_valid})")


# ==========================
# $TRANSLATE
# ==========================
def test_translate(code, expected_appc):
    """
    Tests $translate for a given service code.
    Verifies that the returned APPC KR code matches the expected value.
    """
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
        fail(f"$translate '{code}': HTTP {r.status_code}")
        return

    params = r.json().get("parameter", [])
    result = next((p["valueBoolean"] for p in params if p["name"] == "result"), False)

    if not result:
        fail(f"$translate '{code}' -> no mapping found")
        return

    # Extract the mapped APPC KR code from the response
    appc = "?"
    match = next((p for p in params if p["name"] == "match"), None)
    if match:
        for part in match.get("part", []):
            if part["name"] == "concept":
                appc = part["valueCoding"]["code"]

    if appc == expected_appc:
        ok(f"$translate '{code}' -> '{appc}'")
    else:
        fail(f"$translate '{code}' -> '{appc}' (expected: '{expected_appc}')")


# ==========================
# $LOOKUP
# ==========================
def test_lookup(code, expected_display=None):
    """
    Tests $lookup for a given service code.
    Verifies that the returned display text matches the expected value.
    """
    r = requests.get(
        f"{FHIR_BASE}/CodeSystem/$lookup",
        params={"system": CS_LEISTUNG_URL, "code": code},
        headers=HEADERS
    )

    if not r.ok:
        fail(f"$lookup '{code}': HTTP {r.status_code}")
        return

    params  = r.json().get("parameter", [])
    display = next((p.get("valueString") for p in params if p["name"] == "display"), "?")

    if expected_display is None or display == expected_display:
        ok(f"$lookup '{code}' -> '{display}'")
    else:
        fail(f"$lookup '{code}' -> display mismatch (expected: '{expected_display}', got: '{display}')")


# ==========================
# RUN TESTS
# ==========================
def main():
    print("=" * 55)
    print("  Tests: ELGA APPC KUK")
    print("=" * 55)

    # --- $validate-code: valid codes ---
    print("\n  $validate-code (valid codes):")
    test_validate("MA4",      should_be_valid=True)
    test_validate("MA6",      should_be_valid=True)
    test_validate("MA ERG",   should_be_valid=True)
    test_validate("MA CORE",  should_be_valid=True)
    test_validate("MAS",      should_be_valid=True)

    # --- $validate-code: invalid code ---
    print("\n  $validate-code (invalid code):")
    test_validate("INVALID_KUK_99", should_be_valid=False)

    # --- $translate ---
    print("\n  $translate (service code -> APPC KR code):")
    test_translate("MA4",     expected_appc="7")
    test_translate("MA6",     expected_appc="7")
    test_translate("MA ERG",  expected_appc="7")
    test_translate("MA GAL",  expected_appc="7-1")
    test_translate("MA PRAEP",expected_appc="7-2")

    # --- $lookup ---
    print("\n  $lookup (service description):")
    test_lookup("MA4",     expected_display="Digitale 2-Ebenen - Mammographie")
    test_lookup("MA6",     expected_display="Digitale 3 - Ebenen - Mammographie")
    test_lookup("MA ERG",  expected_display="Ergänzungsaufnahme Mammographie")
    test_lookup("MA CORE", expected_display="Core - Biopsie")
    test_lookup("MAS",     expected_display="Früherkennungs-Mammographie")

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