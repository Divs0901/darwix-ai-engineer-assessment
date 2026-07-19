"""
Deterministic safety gates — enforced in code, NOT retrieval-dependent.

Wrong-person, partial-verification, refused-to-verify, and fraud-dispute
handling cannot depend on a customer's phrasing happening to clear the KB
retrieval threshold. These run unconditionally, every call, before any
retrieval happens.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum


class VerificationState(Enum):
    NOT_STARTED = "not_started"
    PARTIAL = "partial"
    VERIFIED = "verified"
    REFUSED = "refused"
    WRONG_PERSON = "wrong_person"
    FRAUD_DISPUTE = "fraud_dispute"


@dataclass
class CallState:
    call_id: str
    verification: VerificationState = VerificationState.NOT_STARTED
    factors_confirmed: int = 0
    events: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """JSON-safe serialization — Enum won't survive json.dumps() otherwise."""
        d = asdict(self)
        d["verification"] = self.verification.value
        return d


def log_event(state: CallState, event_type: str, detail: dict):
    """State mutation only. Caller decides where the event goes (print, file, webhook)."""
    state.events.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "detail": detail,
    })


def check_verification_factor(state: CallState, factor_correct: bool) -> VerificationState:
    """Call once per identity factor offered (registered mobile, DOB, last-4 of account)."""
    already_verified = state.verification == VerificationState.VERIFIED

    if factor_correct:
        state.factors_confirmed += 1

    if state.factors_confirmed >= 2 and not already_verified:
        state.verification = VerificationState.VERIFIED
        log_event(state, "VERIFICATION_COMPLETE", {"factors_confirmed": state.factors_confirmed})
    elif state.factors_confirmed == 1 and state.verification == VerificationState.NOT_STARTED:
        state.verification = VerificationState.PARTIAL
        log_event(state, "VERIFICATION_PARTIAL", {"factors_confirmed": state.factors_confirmed})

    return state.verification


def flag_wrong_person(state: CallState, reason: str = "caller did not confirm identity as registered borrower"):
    state.verification = VerificationState.WRONG_PERSON
    log_event(state, "WRONG_PERSON_DETECTED", {"reason": reason})


def flag_refused_verification(state: CallState):
    state.verification = VerificationState.REFUSED
    log_event(state, "VERIFICATION_REFUSED", {})


def flag_fraud_dispute(state: CallState):
    """Customer claims they never took this loan. Stops collections talk immediately."""
    state.verification = VerificationState.FRAUD_DISPUTE
    log_event(state, "FRAUD_DISPUTE", {"action": "collections_paused_pending_review"})


def can_disclose_account_info(state: CallState) -> bool:
    """THE gate. Call before any account-specific content leaves the agent."""
    return state.verification == VerificationState.VERIFIED


def schedule_callback(state: CallState, requested_date: str, reason: str):
    log_event(state, "CALLBACK_SCHEDULED", {"requested_date": requested_date, "reason": reason})


def escalate_to_human(state: CallState, reason: str):
    log_event(state, "ESCALATED", {"reason": reason})


if __name__ == "__main__":
    print("--- Wrong person ---")
    s1 = CallState(call_id="test-1")
    flag_wrong_person(s1)
    assert can_disclose_account_info(s1) is False
    print("PASS: disclosure blocked\n")

    print("--- Partial verification ---")
    s2 = CallState(call_id="test-2")
    check_verification_factor(s2, factor_correct=True)
    assert can_disclose_account_info(s2) is False
    print("PASS: disclosure blocked on partial\n")

    print("--- Full verification ---")
    s3 = CallState(call_id="test-3")
    check_verification_factor(s3, factor_correct=True)
    check_verification_factor(s3, factor_correct=True)
    assert can_disclose_account_info(s3) is True
    print("PASS: disclosure allowed\n")

    print("--- Fraud dispute (overrides even after verification) ---")
    s4 = CallState(call_id="test-4")
    check_verification_factor(s4, factor_correct=True)
    check_verification_factor(s4, factor_correct=True)
    flag_fraud_dispute(s4)
    assert can_disclose_account_info(s4) is False
    print("PASS: disclosure blocked after fraud flag, even post-verification\n")

    print("--- JSON serialization check (the bug this review caught) ---")
    import json
    print(json.dumps(s4.to_dict(), indent=2))
    print("\nAll safety gate smoke tests passed.")