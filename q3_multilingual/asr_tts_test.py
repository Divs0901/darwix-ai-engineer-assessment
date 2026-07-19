"""
asr_tts_test.py — Closes a named Q3 gap: actually run TTS + ASR on Tagalog
and Bahasa Indonesia content, not just design it on paper.

Generates real audio with Edge TTS (native tl-PH / id-ID voices), then
transcribes it back with Groq Whisper, and reports real observed quality.
"""
import asyncio
import edge_tts
import os
from dotenv import load_dotenv
from groq import Groq

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

TEST_PHRASES = {
    "ph": {
        "voice": "fil-PH-AngeloNeural",
        "text": "Magandang araw po. Puwede niyo po i-pay ang premium via GCash o bank transfer bago mag-lapse ang policy niyo.",
    },
    "id": {
        "voice": "id-ID-ArdiNeural",
        "text": "Selamat pagi Bapak. Mohon segera melakukan pembayaran cicilan sebelum tanggal jatuh tempo agar tidak dikenakan denda.",
    },
}


async def generate_audio(text: str, voice: str, output_path: str):
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(output_path)


def transcribe(client: Groq, audio_path: str, language_hint: str = None):
    with open(audio_path, "rb") as f:
        kwargs = {"file": (audio_path, f.read()), "model": "whisper-large-v3"}
        if language_hint:
            kwargs["language"] = language_hint
        result = client.audio.transcriptions.create(**kwargs)
    return result.text


if __name__ == "__main__":
    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    report = {}

    for market, config in TEST_PHRASES.items():
        print(f"\n=== {market.upper()} ===")
        output_file = f"tts_output_{market}.mp3"

        print(f"Generating TTS with voice: {config['voice']}")
        asyncio.run(generate_audio(config["text"], config["voice"], output_file))
        print(f"Saved: {output_file}")

        lang_code = "tl" if market == "ph" else "id"
        print(f"Transcribing with Groq Whisper (language hint: {lang_code})...")
        transcript = transcribe(client, output_file, language_hint=lang_code)

        print(f"Original text:  {config['text']}")
        print(f"Transcribed as: {transcript}")

        report[market] = {
            "voice_used": config["voice"],
            "original_text": config["text"],
            "transcribed_text": transcript,
        }

    print("\n=== Summary ===")
    for market, data in report.items():
        print(f"\n{market.upper()}:")
        print(f"  Voice: {data['voice_used']}")
        print(f"  Original:    {data['original_text']}")
        print(f"  Round-trip:  {data['transcribed_text']}")