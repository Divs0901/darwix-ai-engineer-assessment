"""
agent.py — Core conversational orchestration for the loan pre-due reminder voice agent.

Architecture (per ground rules Section 2):
- Greeting, verification, and the reminder statement are DETERMINISTIC (scripted in
  code), not LLM-generated. The LLM never sees account-specific figures until
  verification passes, and even then the first disclosure is Python-constructed.
- Free-form Q&A (after verification) is LLM-driven, grounded via a
  search_knowledge_base tool call — the model never answers from its own knowledge.
- Fraud/dispute detection: the LLM DETECTS (genuine semantic judgment needed), a
  Python function ENFORCES. The model can flag risk; it never grants disclosure.
"""
import os
import sys
import re
import json
import asyncio
import edge_tts
from dotenv import load_dotenv
from groq import Groq

from safety_gates import (
    CallState, VerificationState, flag_wrong_person, flag_refused_verification,
    flag_fraud_dispute, schedule_callback, escalate_to_human,
)
from verification import load_profile, attempt_verification
from retrieval import KnowledgeBase

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

GROQ_MODEL = "llama-3.3-70b-versatile"
AGENT_VOICE = "en-IN-PrabhatNeural"
 
 
async def _speak_async(text: str, filename: str = "_agent_speech.mp3"):
    communicate = edge_tts.Communicate(text, AGENT_VOICE)
    await communicate.save(filename)
    os.system(f"afplay {filename}")
 
 
def speak(text: str):
    if text:
        asyncio.run(_speak_async(text))


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search the loan policy knowledge base for a grounded answer. Always use this instead of answering from your own knowledge.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The customer's question, in their words"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "flag_fraud_dispute",
            "description": "Call this immediately if the customer states they never took this loan or suspects fraud. Do not continue discussing payment after calling this.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_callback",
            "description": "Call this when the customer cannot resolve the matter now and needs a callback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "requested_date": {"type": "string", "description": "When the customer wants to be called back"},
                    "reason": {"type": "string", "description": "Why a callback is needed"},
                },
                "required": ["requested_date", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Call this if the customer explicitly asks for a human agent, or the request is out of scope for this specific loan.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]

SYSTEM_PROMPT = """You are a loan pre-due reminder voice agent for an NBFC. The customer has already been identity-verified before this conversation started — you are only handling their questions and objections about their upcoming payment.

Rules you must follow exactly, in order of priority:
1. For ANY question about policy, fees, deferment, hardship, objections, or process — you MUST call search_knowledge_base FIRST, every single time, before saying anything else. Do this even if you think you already know the answer.
2. Only call escalate_to_human if: the customer explicitly asks for a human agent, OR search_knowledge_base returns no grounded result for their question. Never escalate as a first response to an ordinary question — that skips the knowledge base and is not allowed.
3. If search_knowledge_base returns no grounded result, tell the customer you don't have that information and THEN offer to escalate — never invent an answer, and never escalate without having tried search_knowledge_base first.
4. If the customer says they never took this loan, or suspects fraud, call flag_fraud_dispute immediately and stop discussing the payment.
5. If the customer cannot resolve this now, offer a callback and call schedule_callback with their preferred date and reason.
6. Never write function names, tags, or code-like syntax directly in your spoken response to the customer — only use the actual tool-calling mechanism, never mention tool names in the text you say out loud.
7. Keep responses short and conversational — this is a spoken phone call, not a chat window.
8. For simple acknowledgments like "okay," "thank you," "noted," or "got it" — just reply naturally, don't call any tool. Only call search_knowledge_base when the customer actually asks a question.
"""


def sanitize_reply(text: str) -> str:
    """
    Strip any leaked function-call artifacts from model output before it
    reaches the customer (as text or TTS). Found via adversarial testing —
    the model occasionally leaks raw tags like <function>get_balance</function>
    into otherwise normal responses under prompt-injection-style pressure.
    """
    if not text:
        return text
    cleaned = re.sub(r"<function[^>]*>.*?</function>", "", text)
    cleaned = re.sub(r"<function[^>]*/?>", "", cleaned)
    return cleaned.strip()


EXIT_PHRASES = ("bye", "goodbye", "hang up", "gotta go", "i'm done", "im done")


class LoanReminderAgent:
    def __init__(self, groq_api_key: str = None):
        self.client = Groq(api_key=groq_api_key or os.environ["GROQ_API_KEY"])
        self.kb = KnowledgeBase()  # loaded once, reused for the whole call
        self.messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    def run_verification(self, state: CallState, profile: dict) -> bool:
        """Deterministic verification exchange — tries up to 3 factors, stops once 2 are confirmed."""
        print(f"\nAgent: Hello, is this {profile['customer_name']}? This call is regarding your loan payment. "
              f"To proceed, I need to verify your identity — could you confirm your registered mobile number?")

        factor_prompts = {
            "mobile": "your registered mobile number",
            "dob": "your date of birth",
            "account_last4": "the last 4 digits of your loan account number",
        }
        factor_sequence = ["mobile", "dob", "account_last4"]

        for i, factor in enumerate(factor_sequence):
            if state.verification == VerificationState.VERIFIED:
                break

            provided = input("You: ").strip()

            if any(p in provided.lower() for p in ["not going to", "won't tell you", "don't trust", "who is this"]):
                flag_refused_verification(state)
                print("\nAgent: No problem. I won't share any account details on this call. "
                      "You can reach our verified support line directly if you'd like to confirm this is genuine.")
                return False

            factors_before = state.factors_confirmed
            attempt_verification(state, profile, factor, provided)
            this_attempt_correct = state.factors_confirmed > factors_before

            if state.verification == VerificationState.VERIFIED:
                break

            if i < len(factor_sequence) - 1:
                next_factor = factor_sequence[i + 1]
                if this_attempt_correct:
                    print(f"\nAgent: Thanks, that matches. Could you also confirm {factor_prompts[next_factor]}?")
                else:
                    print(f"\nAgent: That doesn't match our records. Could you confirm {factor_prompts[next_factor]} instead?")

        if state.verification != VerificationState.VERIFIED:
            flag_wrong_person(state, reason="failed verification after available attempts")
            print("\nAgent: I'm unable to verify your identity on this call, so I can't discuss any account details. "
                  "I'll have someone from our verified support team follow up.")
            return False

        return True

    def state_reminder(self, profile: dict):
        """Deterministic — NOT LLM-generated. Figures are Python-constructed, not model-generated."""
        print(f"\nAgent: Thank you, you're verified. This is a reminder that your payment of "
              f"₹{profile['outstanding_amount']} for loan {profile['loan_id']} is due on {profile['due_date']}. "
              f"Do you have any questions, or would you like to proceed with the payment?")

    def handle_tool_call(self, state: CallState, tool_name: str, args: dict) -> str:
        print(f"  [TOOL CALLED: {tool_name}]  args={args}")

        if tool_name == "search_knowledge_base":
            result = self.kb.retrieve(args["query"])
            if result["grounded"]:
                return f"[Source: {result['source']}] {result['content']}"
            return result["fallback_message"]

        elif tool_name == "flag_fraud_dispute":
            flag_fraud_dispute(state)
            return "Fraud dispute logged. Collections activity paused pending review. Inform the customer their account is flagged for investigation."

        elif tool_name == "schedule_callback":
            schedule_callback(state, args["requested_date"], args["reason"])
            return f"Callback scheduled for {args['requested_date']}. Confirm this with the customer."

        elif tool_name == "escalate_to_human":
            escalate_to_human(state, args["reason"])
            return "Escalated to a human agent. Inform the customer someone will follow up within one business day."

        return "Unknown tool."

    def _call_with_retry(self, tools=None, tool_choice=None, max_attempts: int = 2):
        """
        Groq's Llama tool-calling occasionally emits malformed function-call
        syntax (a known flakiness, not a bug in our code). Retry once before
        giving up — returns None if every attempt fails, so callers can
        degrade gracefully instead of crashing.
        """
        kwargs = {"model": GROQ_MODEL, "messages": self.messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(**kwargs)
                return response.choices[0].message
            except Exception as e:
                print(f"  [API call failed, attempt {attempt + 1}/{max_attempts}: {e}]")
        return None

    def chat_turn(self, state: CallState, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})

        message = self._call_with_retry(tools=TOOLS, tool_choice="auto")

        if message is None:
            reply = ("I'm having trouble processing that right now. Let me schedule "
                     "a callback so someone can help you directly.")
            self.messages.append({"role": "assistant", "content": reply})
            return sanitize_reply(reply)

        if message.tool_calls:
            self.messages.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in message.tool_calls
                ],
            })

            for tool_call in message.tool_calls:
                args = json.loads(tool_call.function.arguments)
                result = self.handle_tool_call(state, tool_call.function.name, args)
                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

            followup_message = self._call_with_retry(tools=None)
            reply = followup_message.content if followup_message else "Let me have someone follow up on that."
            self.messages.append({"role": "assistant", "content": reply})
            return sanitize_reply(reply)

        reply = message.content
        self.messages.append({"role": "assistant", "content": reply})
        return sanitize_reply(reply)


if __name__ == "__main__":
    call_id = sys.argv[1] if len(sys.argv) > 1 else "test_call_001"

    agent = LoanReminderAgent()
    profile = load_profile(call_id)
    state = CallState(call_id=f"live-test-{call_id}")

    print(f"\n[Simulating call regarding: {profile['customer_name']} ({call_id})]")

    if not agent.run_verification(state, profile):
        print("\n--- Call ended (verification failed) ---")
        print(json.dumps(state.to_dict(), indent=2))
        exit()

    agent.state_reminder(profile)

    print("\n(Type 'bye' to end the call)\n")
    while True:
        user_input = input("You: ").strip()
        if any(phrase in user_input.lower() for phrase in EXIT_PHRASES):
            print("\nAgent: Thank you for your time. Goodbye.")
            break
        reply = agent.chat_turn(state, user_input)
        print(f"\nAgent: {reply}\n")

    print("\n--- Call log ---")
    print(json.dumps(state.to_dict(), indent=2))
