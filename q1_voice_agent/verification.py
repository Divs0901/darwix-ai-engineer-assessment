"""
Verification layer — connects safety_gates.py's state machine to actual
identity checks against customer profile data.

Handles messy real input: phone numbers with spaces/dashes, dates in
different formats. A naive exact-string comparison would falsely reject
a genuinely correct customer just because of formatting differences.
"""
import json
import os
import re
from datetime import datetime

from safety_gates import CallState, check_verification_factor, log_event

PROFILES_PATH = os.path.join(os.path.dirname(__file__), "..", "q2_knowledge_base", "customer_profiles.json")

# Common date formats a customer might say/type — extend as needed.
DATE_FORMATS = ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d %B %Y", "%B %d %Y", "%d %b %Y"]


def load_profile(call_id: str) -> dict:
    with open(PROFILES_PATH) as f:
        profiles = json.load(f)
    for p in profiles:
        if p["call_id"] == call_id:
            return p
    raise ValueError(f"No test profile found for call_id: {call_id}")


def _normalize_digits(value: str) -> str:
    """Strip everything except digits — handles '987 650 0001', '987-650-0001', etc."""
    return re.sub(r"\D", "", value)


def _normalize_date(value: str):
    """Try each known format until one parses. Returns a date object or None."""
    value = value.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def verify_mobile(profile: dict, provided: str) -> bool:
    return _normalize_digits(provided) == _normalize_digits(profile["registered_mobile"])


def verify_dob(profile: dict, provided: str) -> bool:
    provided_date = _normalize_date(provided)
    if provided_date is None:
        return False
    expected_date = datetime.strptime(profile["date_of_birth"], "%Y-%m-%d").date()
    return provided_date == expected_date


def verify_account_last4(profile: dict, provided: str) -> bool:
    digits = _normalize_digits(provided)
    return digits == profile["loan_account_last4"]


VERIFIERS = {
    "mobile": verify_mobile,
    "dob": verify_dob,
    "account_last4": verify_account_last4,
}


def attempt_verification(state: CallState, profile: dict, factor_type: str, provided_value: str):
    """
    Run one verification attempt. Updates state via check_verification_factor
    and logs the attempt (not the actual correct answer, to avoid leaking it
    into logs — only whether the attempt matched).
    """
    verifier = VERIFIERS.get(factor_type)
    if verifier is None:
        raise ValueError(f"Unknown factor_type: {factor_type}. Expected one of {list(VERIFIERS)}")

    is_correct = verifier(profile, provided_value)
    log_event(state, "VERIFICATION_ATTEMPT", {"factor_type": factor_type, "matched": is_correct})
    return check_verification_factor(state, factor_correct=is_correct)


if __name__ == "__main__":
    profile = load_profile("test_call_001")
    print(f"Testing against profile: {profile['customer_name']}\n")

    print("--- Correct mobile, formatted with spaces/dashes ---")
    state = CallState(call_id="sim-1")
    result = attempt_verification(state, profile, "mobile", "987-650-0001")
    print(f"Verification state: {result}\n")

    print("--- Correct DOB, different format than stored ---")
    result = attempt_verification(state, profile, "dob", "14 May 1990")
    print(f"Verification state: {result}\n")

    print("--- Wrong account digits ---")
    state2 = CallState(call_id="sim-2")
    attempt_verification(state2, profile, "mobile", "9876500001")
    result = attempt_verification(state2, profile, "account_last4", "0000")
    print(f"Verification state: {result} (should still be PARTIAL, not VERIFIED)\n")

    print("All verification smoke tests ran.")