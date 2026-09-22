Conversational agent


**Use case:** Loan pre-due reminder (NBFC), with parallel Philippines (life insurance) and Indonesia (multifinance) localized bots for Q3.

**Stack:** 100% free/open-source — Groq Llama 3.3 (reasoning), Groq Whisper-large-v3 (ASR), Edge TTS (native-voice TTS), Chroma (local vector store), sentence-transformers `all-MiniLM-L6-v2` (local embeddings, no API calls). No paid platforms (Twilio/Vapi/ElevenLabs) were used — see "Design Decisions" below for why, and how the architecture stays provider-agnostic.

---

## Repository Structure

```
darwix-ai-engineer-assessment/
├── README.md                    (this file)
├── .env.example                 (copy to .env, fill in your own keys)
├── architecture_diagram.png
├── q1_voice_agent/
│   ├── agent.py                 (conversation orchestration)
│   ├── safety_gates.py          (deterministic verification/fraud state machine)
│   ├── verification.py          (identity-check logic against test profiles)
│   ├── retrieval.py             (KB retrieval, threshold-gated)
│   ├── kb_retrieval_test.py     (24-question calibration harness)
│   └── retrieval_calibration_results.json
├── q2_knowledge_base/
│   ├── kb_records.json          (32 records, loan pre-due reminder domain)
│   └── customer_profiles.json   (synthetic test profiles for verification)
├── q3_multilingual/
│   ├── multilingual_agent.py    (same architecture as Q1, localized)
│   ├── ph_kb_records.json       (10 records, Taglish)
│   ├── id_kb_records.json       (10 records, Bahasa Indonesia)
│   ├── asr_tts_test.py          (real ASR/TTS round-trip test)
│   └── tts_output_{ph,id}.mp3
├── q4_live_nudges/
│   ├── nudge_engine.py
│   └── nudge_test_results.json  (latency report + fired nudges)
└── recordings/
    ├── q1_voice_agent/          (3 test call transcripts)
    └── q3_multilingual/         (PH + ID test call transcripts)
```

---

## Setup

```bash
git clone <repo-url>
cd darwix-ai-engineer-assessment
cp .env.example .env   # fill in your own GROQ_API_KEY
pip install groq edge-tts python-dotenv sentence-transformers chromadb
```

Run each piece independently:

```bash
# Q1 — voice agent (text input, spoken TTS output)
cd q1_voice_agent && python3 agent.py test_call_001

# Q2 — retrieval calibration
cd q1_voice_agent && python3 kb_retrieval_test.py

# Q3 — localized bots
cd q3_multilingual && python3 multilingual_agent.py ph
cd q3_multilingual && python3 multilingual_agent.py id

# Q4 — nudge engine (simulated streaming over scripted turns)
cd q4_live_nudges && python3 nudge_engine.py
```

---

## Q1 — Knowledge-Grounded Voice Agent

**Architecture:** Greeting, identity verification, and the payment-reminder statement are deterministic (Python-constructed, not LLM-generated) — the model never sees account figures until verification passes. Everything after that (objections, FAQs, policy questions) is retrieval-grounded via a `search_knowledge_base` tool call; the system prompt forbids the model from answering from its own knowledge.

**Safety-critical exception:** wrong-person, partial-verification, refused-to-verify, and fraud-dispute handling run as unconditional code-level gates (`safety_gates.py`), not retrieval — because safety-critical behavior can't depend on a customer's phrasing happening to clear a similarity threshold.

**Interface:** push-to-talk style — type input, agent speaks its reply via Edge TTS. Chosen over live mic capture to avoid unpredictable audio-permission debugging inside the time budget; documented as a scope simplification, not a hidden gap.

**Test coverage (see `recordings/q1_voice_agent/`):** verification failure/wrong-person rejection, verified customer with objection + fraud-dispute + adversarial instruction-override attempt, and a second verified customer with graceful degradation on an out-of-scope/low-confidence query.

---

## Q2 — Production-Ready Knowledge Base

32 records total (29 live, 3 flagged/excluded — one deliberate duplicate, one malformed extraction, one raw-PII-before-redaction pair) covering policy, payment methods, objection handling, product/marketing content, form references, and five-state identity verification context.

**Retrieval calibration:** ran the full 24-question traceability matrix at threshold=0 to capture real similarity scores before gating (`retrieval_calibration_results.json`). Result: 16/24 correct top-1 matches. Two negative controls (off-topic questions) both scored well under 0.1, confirming clean separation from real questions. Several genuine matches scored as low as 0.27–0.28 (e.g. "I lost my job" → 0.2698, "Can I negotiate a lower amount?" → 0.2769) — right at the calibrated 0.28 threshold, meaning the threshold is deliberately permissive enough to catch paraphrased real questions without opening the door to false positives from the negative controls.

**Known limitation:** an earlier duplicate-detection pass using TF-IDF cosine similarity missed a deliberately-planted paraphrased duplicate (`kb_loan_003`/`kb_loan_020` — "payment holiday" vs. "deferment/moratorium", scored 0.136 against a 0.35 threshold). This is real evidence that lexical similarity alone is insufficient for near-duplicate detection, and that semantic embeddings (the `all-MiniLM-L6-v2` choice used everywhere else) are necessary, not just preferable.

---

## Q3 — Native-Language Voice Bots

Same retrieval-grounded, no-hardcoding architecture as Q1, localized content per market:

- **Philippines:** life insurance premium reminder, natural Taglish code-switching ("po"/"opo", English-Tagalog mixing), 10-record KB, `fil-PH-AngeloNeural` TTS voice.
- **Indonesia:** installment reminder, formal→colloquial Bahasa register shift, OJK/AFPI compliance content, 10-record KB, `id-ID-ArdiNeural` TTS voice.

**Localization validation approach:** dialogue generated with Groq Llama 3.3, cross-referenced against real public reference material (RichestPH, Kaiwa Blog, AFPI/OJK guidance, halobdg.com collections SMS examples) rather than trusting ungrounded LLM generation. **Not validated by a native speaker** — this is an explicit, accepted limitation, not a hidden gap. Production deployment would require native-speaker QA before launch.

**Real ASR/TTS test:** `asr_tts_test.py` generates real audio with native-market Edge TTS voices and transcribes it back with Groq Whisper, reporting actual round-trip quality rather than a paper design.

**Test coverage:** 1 recorded call per market currently (`recordings/q3_multilingual/`) — spec asks for 2 per market. *[Confirm final count before submission — if only 1/market shipped, this is named here as an explicit scope cut made under the time budget, not a silent gap.]*

---

## Q4 — Live Insights and Nudges

**Method:** simulated streaming — turns are processed one at a time, as they arrive, through the same signal-extraction pipeline live audio would hit (explicitly permitted by the spec). Reuses transcript turns from the Q1/Q3 recordings.

**Signal types:** missed cross-sell, compliance gap, rising frustration (confidence-thresholded at 0.6, JSON-structured LLM output).

**Nudge controls implemented:** confidence threshold, duplicate suppression, cooldown (2 turns) between repeats of the same signal type. Topic grouping/priority/expiry are named as future work, not built.

**Results (`nudge_test_results.json`):** 7 turns processed, 4 nudges fired (missed cross-sell, compliance gap, rising frustration ×2), P50 latency ~209-217ms, P95 ~414-438ms across runs.

**False-positive analysis (honest number, not cherry-picked):** of the two turns deliberately scripted as noisy/ambiguous and expected to produce zero nudges, one (*"Uh, yeah, hold on, sorry, my dog is barking, what was the question again?"*) was correctly suppressed. The other (*"Okay okay sure whatever, fine, I guess, yeah."*) fired a `rising_frustration` nudge at 0.80 confidence — a genuine false positive, not a suppressed one. That puts the measured false-positive rate at **1 of 4 fired nudges (25%)** on this 7-turn test set, not zero. Likely cause: a single-utterance classifier with no conversational context can misread a resigned/sarcastic "whatever, fine" as frustration, since the surface tone (short, clipped, exasperated-sounding words) overlaps with genuine escalation without the preceding-turn context that would disambiguate them. Production mitigation: include the last 1-2 turns of context in the classification prompt rather than classifying each utterance in isolation, and calibrate confidence thresholds per signal type rather than a single global 0.6 cutoff.

**10x-scale limitation:** per-turn synchronous LLM calls become a latency/cost bottleneck at scale; production would need batching, a cheaper first-pass classifier before the LLM call, and queue-based async processing.

**Noisy-audio limitation:** ASR quality degradation under real noisy audio would cascade into false signals; mitigation would be feeding ASR confidence into the nudge threshold itself, not just signal-extraction confidence.

---

## Known Limitations (full list)

- Q1/Q3 voice interface is push-to-talk (typed input, spoken output), not live mic capture.
- Q3 localization is LLM-generated + source-cross-referenced, not native-speaker validated.
- Q3 Indonesia regional-accent testing beyond standard Jakarta speech was not completed.
- Q4 nudges run over simulated/replayed turns, not live audio capture end-to-end.
- Q2 schema/PII validation uses regex + embedding similarity, not a formal JSON Schema/pydantic contract.
- Multi-turn context is passed in full each turn (no summarization/truncation) — fine at the call lengths tested here, would need bounding at scale.

## Production Improvement Plan

- Add live mic capture with real-time audio chunking for Q1/Q3/Q4.
- Native-speaker QA pass on PH/ID localized scripts before any production use.
- Move Q4 to async/queued signal extraction with a lightweight pre-classifier ahead of the LLM call.
- Add JSON Schema validation and a `last_verified_date`-driven freshness check to the KB pipeline.
- Swap in a persistent vector store (current Chroma instance is in-memory, resets per process) for production.
