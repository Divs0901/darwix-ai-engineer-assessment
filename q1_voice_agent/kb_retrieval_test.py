"""
Full calibration test against the 24-question traceability matrix.
Run this ONCE to gather real similarity data, then pick the final threshold
based on where true-positive and true-negative scores actually separate —
not a guess.
"""
from retrieval import KnowledgeBase
import json

TEST_CASES = [
    ("Can I defer or postpone this payment?", "kb_loan_003"),
    ("What happens if I miss my payment date?", "kb_loan_001"),
    ("Is there a penalty for late payment?", "kb_loan_001"),
    ("Can I pay in installments instead of the full amount?", "kb_loan_011"),
    ("What payment methods are accepted?", "kb_loan_008"),
    ("I lost my job, what are my options?", "kb_loan_009"),
    ("I'm on leave/traveling, can I pay when I'm back?", "kb_loan_010"),
    ("I don't have the money right now, what happens?", "kb_loan_005"),
    ("Why is this payment due already, I thought I had more time?", "kb_loan_004"),
    ("Can I negotiate a lower amount?", "kb_loan_006"),
    ("How do I confirm this is really about my loan?", "kb_loan_012"),
    ("Can you tell me my exact outstanding balance?", "kb_loan_013"),
    ("How is the interest calculated on late payments?", "kb_loan_027"),
    ("Will this affect my credit score?", "kb_loan_007"),
    ("Can someone else make the payment on my behalf?", "kb_loan_014"),
    ("I want to speak to a human agent.", "kb_loan_015"),
    ("Can you also help me with my other loan/account?", "kb_loan_016"),
    ("I want to file a complaint about this call.", "kb_loan_017"),
    ("What are your business hours for support?", "kb_loan_018"),
    ("I already paid this, why are you calling?", "kb_loan_025"),
    ("How did you get my number?", "kb_loan_026"),
    ("What kind of loan product is this exactly?", "kb_loan_028"),
    # Deliberate out-of-scope / negative controls — should score LOW
    ("What's the weather like today?", None),
    ("Can you recommend a good restaurant?", None),
]

if __name__ == "__main__":
    kb = KnowledgeBase(threshold=0.0)  # threshold=0 so we capture the real score every time, no gating yet

    results = []
    correct = 0
    for question, expected in TEST_CASES:
        result = kb.retrieve(question)
        top_record = result.get("record_id")
        similarity = result.get("similarity")
        is_correct = (top_record == expected) if expected else (similarity < 0.3)  # loose negative-control check
        correct += is_correct

        results.append({
            "question": question,
            "expected_record": expected,
            "retrieved_record": top_record,
            "similarity": similarity,
            "match": is_correct,
        })

        flag = "OK" if is_correct else "MISS"
        print(f"[{flag}] sim={similarity:.4f}  expected={expected}  got={top_record}  | {question}")

    print(f"\n{correct}/{len(TEST_CASES)} correct top-1 matches")

    with open("retrieval_calibration_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Saved: retrieval_calibration_results.json")