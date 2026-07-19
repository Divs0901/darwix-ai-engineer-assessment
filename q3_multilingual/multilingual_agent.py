"""
multilingual_agent.py — Q3: Native-language voice bots (Philippines / Indonesia).

Reuses the exact same architecture as Q1 (retrieval-grounded, no hardcoded
answers, safe fallback) with localized content and code-switching instructions
instead of literal translation.

Localization validation note (per ground rules Section 8, locked): dialogue
was generated and cross-referenced against real public source material
(RichestPH, Kaiwa Blog, AFPI/OJK, halobdg.com — see q3_multilingual sources),
but has NOT been validated by a native speaker. This is a documented,
accepted limitation, not a hidden gap — production deployment would require
native-speaker QA before launch.
"""
import os
import re
import sys
import json
from dotenv import load_dotenv
from groq import Groq
from sentence_transformers import SentenceTransformer
import chromadb

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

GROQ_MODEL = "llama-3.3-70b-versatile"
RETRIEVAL_THRESHOLD = 0.28  # same calibrated value as Q1 — same embedding model, same behavior expected

MARKET_CONFIG = {
    "ph": {
        "kb_file": "ph_kb_records.json",
        "language_name": "Taglish (natural English/Tagalog code-switching)",
        "system_prompt": """You are a life insurance premium reminder voice agent for a bancassurance partner in the Philippines.

Rules:
1. Speak in natural Taglish — the way a real Filipino customer service agent speaks: mixing English and Tagalog naturally (e.g. "Puwede niyo po i-pay via GCash"), using "po" and "opo" for respect, softeners like "medyo" and "parang". This is NOT a literal translation exercise — match how Filipinos actually code-switch.
2. For ANY policy, premium, coverage, or process question, you MUST call search_knowledge_base first. Never answer from your own knowledge.
3. If search_knowledge_base returns no grounded result, say so honestly in Taglish and offer to escalate — never invent an answer.
4. Use terms naturally: premium, policy, beneficiary, rider, lapse, coverage, bank referral.
5. If the customer asks for a human agent, or asks about something out of scope, escalate — say so clearly.
6. Keep responses short and conversational — this is a spoken phone call.
7. NEVER alter, round, or approximate numbers, dates, or figures from the knowledge base content — quote them exactly as retrieved, even while paraphrasing the surrounding language.
8. If the customer asks for a SPECIFIC figure or date tied to their own account (their exact due date, their exact tenor, their exact amount), and the knowledge base only returns general policy information rather than their specific account data, do NOT invent or select a specific value. Say clearly that you need to check their specific account and offer to look it up or escalate.
""",
    },
    "id": {
        "kb_file": "id_kb_records.json",
        "language_name": "Bahasa Indonesia (formal + colloquial, with finance English loanwords)",
        "system_prompt": """You are an installment payment reminder voice agent for a multifinance company in Indonesia.

Rules:
1. Speak in natural Bahasa Indonesia — formal register with "Bapak/Ibu" for a first approach, shifting to more colloquial register only if the customer speaks casually first. This is NOT a literal translation exercise — match how a real Indonesian collections agent actually speaks, per OJK/AFPI politeness rules (never threatening tone).
2. For ANY installment, penalty, restructuring, or process question, you MUST call search_knowledge_base first. Never answer from your own knowledge.
3. If search_knowledge_base returns no grounded result, say so honestly in Bahasa Indonesia and offer to escalate — never invent an answer.
4. Use terms naturally: cicilan, tenor, denda, DP, jatuh tempo, angsuran, pembiayaan.
5. If the customer asks for a human agent, or raises a complaint about threatening tone, escalate — cite OJK compliance rules from the knowledge base if relevant.
6. Keep responses short and conversational — this is a spoken phone call.
7. NEVER alter, round, or approximate numbers, dates, or figures from the knowledge base content — quote them exactly as retrieved, even while paraphrasing the surrounding language.
8. If the customer asks for a SPECIFIC figure or date tied to their own account (their exact due date, their exact tenor, their exact amount), and the knowledge base only returns general policy information rather than their specific account data, do NOT invent or select a specific value. Say clearly that you need to check their specific account and offer to look it up or escalate.
""",
    },
}


def sanitize_reply(text: str) -> str:
    if not text:
        return text
    cleaned = re.sub(r"<function[^>]*>.*?</function>", "", text)
    cleaned = re.sub(r"<function[^>]*/?>", "", cleaned)
    return cleaned.strip()


class MarketKnowledgeBase:
    def __init__(self, kb_filename: str, threshold: float = RETRIEVAL_THRESHOLD):
        self.threshold = threshold
        kb_path = os.path.join(os.path.dirname(__file__), kb_filename)
        print(f"Loading embedding model and {kb_filename}...")
        self.model = SentenceTransformer("all-MiniLM-L6-v2")
        self.client = chromadb.Client()
        self.collection = self.client.get_or_create_collection(
            f"kb_{kb_filename}", metadata={"hnsw:space": "cosine"}
        )
        with open(kb_path, encoding="utf-8") as f:
            records = json.load(f)
        ids = [r["record_id"] for r in records]
        docs = [r["content"] for r in records]
        metadatas = [{"title": r["title"], "category": r["category"], "source": r["source"]} for r in records]
        embeddings = self.model.encode(docs).tolist()
        self.collection.add(ids=ids, documents=docs, metadatas=metadatas, embeddings=embeddings)
        print(f"Loaded {len(records)} records.")

    def retrieve(self, query: str) -> dict:
        query_embedding = self.model.encode([query]).tolist()
        results = self.collection.query(query_embeddings=query_embedding, n_results=1)
        if not results["ids"][0]:
            return {"grounded": False, "fallback_message": "Wala pong available na information dito. / Maaf, informasi tersebut belum tersedia."}
        similarity = 1 - results["distances"][0][0]
        if similarity < self.threshold:
            return {"grounded": False, "similarity": round(similarity, 4),
                     "fallback_message": "Wala pong available na information dito. / Maaf, informasi tersebut belum tersedia."}
        return {
            "grounded": True,
            "record_id": results["ids"][0][0],
            "content": results["documents"][0][0],
            "source": results["metadatas"][0][0]["source"],
            "similarity": round(similarity, 4),
        }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search the localized knowledge base for a grounded answer. Always use this instead of answering from your own knowledge.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The customer's question, in English (for retrieval matching)"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": "Call this if the customer explicitly asks for a human agent, or the request is out of scope for this call.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
            },
        },
    },
]


class MultilingualAgent:
    def __init__(self, market: str):
        config = MARKET_CONFIG[market]
        self.client = Groq(api_key=os.environ["GROQ_API_KEY"])
        self.kb = MarketKnowledgeBase(config["kb_file"])
        self.messages = [{"role": "system", "content": config["system_prompt"]}]
        print(f"\n[Market: {market.upper()} — {config['language_name']}]")
        print("[Localization note: generated + cross-referenced against real sources, NOT native-speaker validated]\n")

    def _call_with_retry(self, tools=None, max_attempts=2):
        kwargs = {"model": GROQ_MODEL, "messages": self.messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        for attempt in range(max_attempts):
            try:
                response = self.client.chat.completions.create(**kwargs)
                return response.choices[0].message
            except Exception as e:
                print(f"  [API call failed, attempt {attempt + 1}/{max_attempts}: {e}]")
        return None

    def chat_turn(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})
        message = self._call_with_retry(tools=TOOLS)

        if message is None:
            reply = "Pasensya na po, may problema. / Maaf, ada kendala teknis."
            self.messages.append({"role": "assistant", "content": reply})
            return sanitize_reply(reply)

        if message.tool_calls:
            self.messages.append({
                "role": "assistant", "content": message.content,
                "tool_calls": [{"id": tc.id, "type": "function",
                                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                for tc in message.tool_calls],
            })
            for tool_call in message.tool_calls:
                args = json.loads(tool_call.function.arguments)
                if tool_call.function.name == "escalate_to_human":
                    print(f"  [TOOL CALLED: escalate_to_human]  reason={args.get('reason')}")
                    content = "Escalated to a human agent. Inform the customer someone will follow up within one business day, in their language."
                else:
                    result = self.kb.retrieve(args["query"])
                    print(f"  [TOOL CALLED: search_knowledge_base]  query={args['query']}  similarity={result.get('similarity')}")
                    content = f"[Source: {result['source']}] {result['content']}" if result["grounded"] else result["fallback_message"]
                self.messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": content})
            followup = self._call_with_retry()
            reply = followup.content if followup else "Let me have someone follow up on that."
            self.messages.append({"role": "assistant", "content": reply})
            return sanitize_reply(reply)

        reply = message.content
        self.messages.append({"role": "assistant", "content": reply})
        return sanitize_reply(reply)


if __name__ == "__main__":
    market = sys.argv[1] if len(sys.argv) > 1 else "ph"
    if market not in MARKET_CONFIG:
        print(f"Unknown market '{market}'. Use 'ph' or 'id'.")
        sys.exit(1)

    agent = MultilingualAgent(market)
    print("(Type 'bye' to end)\n")
    while True:
        user_input = input("You: ").strip()
        if any(w in user_input.lower() for w in ("bye", "goodbye")):
            goodbye = "Salamat po!" if market == "ph" else "Terima kasih!"
            print(f"\nAgent: {goodbye}")
            break
        reply = agent.chat_turn(user_input)
        print(f"\nAgent: {reply}\n")