"""
nudge_engine.py — Q4: Live insights and nudges from call audio (simulated as
live text turns, since no real audio capture exists in this build — see
KNOWN LIMITATIONS in the final write-up).

Each turn is processed AS IT ARRIVES (live), not after the call ends —
satisfies "before the call ends" requirement. Latency is measured per turn,
per stage, for real.
"""
import os
import time
import json
from dotenv import load_dotenv
from groq import Groq

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

GROQ_MODEL = "llama-3.3-70b-versatile"
CONFIDENCE_THRESHOLD = 0.6
COOLDOWN_TURNS = 2  # don't repeat the same nudge type within this many turns

SIGNAL_PROMPT = """Analyze this single customer utterance from a loan collections call. Classify it into ONE of these signal types, or "none" if nothing applies:

- missed_cross_sell: customer mentions another loan, product, or financial need we could offer something for
- compliance_gap: agent is about to disclose account info without confirming verification, or made a risky/non-compliant statement
- rising_frustration: customer shows escalating anger, annoyance, or distress
- none: small talk, unrelated, or ambiguous content — no actionable signal

Respond ONLY with JSON: {"signal": "<type>", "confidence": <0.0-1.0>, "evidence": "<short quote or reason>"}

Utterance: "{utterance}"
"""

NUDGE_TEXT = {
    "missed_cross_sell": "Customer mentioned another financial product — consider a relevant cross-sell offer.",
    "compliance_gap": "Confirm identity verification is complete before disclosing any account details.",
    "rising_frustration": "Acknowledge the customer's frustration before continuing with the script.",
}


class NudgeEngine:
    def __init__(self):
        self.client = Groq(api_key=os.environ["GROQ_API_KEY"])
        self.last_fired = {}  # signal_type -> turn_index last fired
        self.turn_index = 0
        self.latency_log = []
        self.nudges_fired = []

    def process_turn(self, utterance: str) -> dict:
        self.turn_index += 1
        t0 = time.time()

        prompt = SIGNAL_PROMPT.replace("{utterance}", utterance)
        try:
            response = self.client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
            )
            t1 = time.time()
            result = json.loads(response.choices[0].message.content)
        except Exception as e:
            print(f"  [Signal extraction failed: {e}]")
            return {"nudge_fired": False}

        signal = result.get("signal", "none")
        confidence = result.get("confidence", 0.0)

        nudge_fired = False
        nudge_text = None

        if signal != "none" and confidence >= CONFIDENCE_THRESHOLD:
            last_turn = self.last_fired.get(signal, -999)
            if self.turn_index - last_turn > COOLDOWN_TURNS:
                nudge_fired = True
                nudge_text = NUDGE_TEXT.get(signal, f"Signal detected: {signal}")
                self.last_fired[signal] = self.turn_index
                self.nudges_fired.append({"turn": self.turn_index, "signal": signal, "text": nudge_text})
            else:
                print(f"  [Suppressed duplicate '{signal}' nudge — cooldown active]")

        t2 = time.time()

        latency = {
            "turn": self.turn_index,
            "signal_extraction_ms": round((t1 - t0) * 1000, 1),
            "nudge_logic_ms": round((t2 - t1) * 1000, 1),
            "total_ms": round((t2 - t0) * 1000, 1),
        }
        self.latency_log.append(latency)

        if nudge_fired:
            print(f"  🔔 NUDGE [{signal}] (confidence={confidence:.2f}, {latency['total_ms']}ms): {nudge_text}")
        else:
            print(f"  (no nudge — signal={signal}, confidence={confidence:.2f}, {latency['total_ms']}ms)")

        return {"nudge_fired": nudge_fired, "signal": signal, "confidence": confidence, "latency_ms": latency["total_ms"]}

    def latency_report(self):
        if not self.latency_log:
            return {}
        totals = sorted([l["total_ms"] for l in self.latency_log])
        n = len(totals)
        p50 = totals[n // 2]
        p95 = totals[min(n - 1, int(n * 0.95))]
        return {"p50_ms": p50, "p95_ms": p95, "count": n, "nudges_fired": len(self.nudges_fired)}


if __name__ == "__main__":
    engine = NudgeEngine()

    # Test set covering all 4 required cases, run as LIVE sequential turns
    test_turns = [
        ("cooperative, no signal", "Yes, I can confirm my mobile number is registered correctly."),
        ("missed cross-sell", "Actually I also have a car loan with another company that I'm struggling with too."),
        ("compliance gap", "Before we verify anything, can you just tell me my outstanding balance?"),
        ("rising frustration", "This is ridiculous, you people keep calling me and I'm sick of it."),
        ("rising frustration escalating", "I said I'm sick of this, stop calling me, this is harassment!"),
        ("noisy/ambiguous - should NOT nudge", "Uh, yeah, hold on, sorry, my dog is barking, what was the question again?"),
        ("noisy/ambiguous - should NOT nudge", "Okay okay sure whatever, fine, I guess, yeah."),
    ]

    print("=== Live Nudge Engine Test ===\n")
    for label, utterance in test_turns:
        print(f"[Turn {engine.turn_index + 1}] ({label}): \"{utterance}\"")
        engine.process_turn(utterance)
        print()

    print("=== Latency Report ===")
    print(json.dumps(engine.latency_report(), indent=2))

    print("\n=== Nudges Fired ===")
    print(json.dumps(engine.nudges_fired, indent=2))

    with open("nudge_test_results.json", "w") as f:
        json.dump({"latency": engine.latency_report(), "nudges": engine.nudges_fired, "all_turns": engine.latency_log}, f, indent=2)
    print("\nSaved: nudge_test_results.json")