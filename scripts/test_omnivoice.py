"""
scripts/test_omnivoice.py — CLI Test Script for k2-fsa/OmniVoice Hugging Face Space API.

Usage Examples:

1. Voice Design Mode (Generate speech from text with age/gender/pitch/accent controls):
   python scripts/test_omnivoice.py --mode design --text "Hello! This is a test of zero-cost voice design using OmniVoice." --gender "Female / 女" --age "Young Adult / 青年" --out output_design.wav

2. Voice Cloning Mode (Clone voice from a reference audio file):
   python scripts/test_omnivoice.py --mode clone --text "Synthesizing new text in cloned voice!" --ref-aud "path/to/reference.wav" --ref-text "Original text spoken in reference audio" --out output_clone.wav
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from dotenv import load_dotenv
from gradio_client import Client, handle_file

# Load environment variables from .env (e.g. HF_TOKEN)
load_dotenv()

# Force UTF-8 encoding for Windows CMD / PowerShell standard output
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

SPACE_ID = "k2-fsa/OmniVoice"


def normalize_gender(val: str) -> str:
    v = val.strip().lower()
    if "female" in v or "女" in v:
        return "Female / 女"
    if "male" in v or "男" in v:
        return "Male / 男"
    return "Auto"


def normalize_age(val: str) -> str:
    v = val.strip().lower()
    if "child" in v or "儿童" in v:
        return "Child / 儿童"
    if "teen" in v or "少年" in v:
        return "Teenager / 少年"
    if "young" in v or "青年" in v:
        return "Young Adult / 青年"
    if "middle" in v or "中年" in v:
        return "Middle-aged / 中年"
    if "elder" in v or "老年" in v:
        return "Elderly / 老年"
    return "Auto"


def normalize_pitch(val: str) -> str:
    v = val.strip().lower()
    if "very low" in v or "极低" in v:
        return "Very Low Pitch / 极低音调"
    if "low" in v or "低" in v:
        return "Low Pitch / 低音调"
    if "moderate" in v or "medium" in v or "中" in v:
        return "Moderate Pitch / 中音调"
    if "very high" in v or "极高" in v:
        return "Very High Pitch / 极高音调"
    if "high" in v or "高" in v:
        return "High Pitch / 高音调"
    return "Auto"


def normalize_accent(val: str) -> str:
    v = val.strip().lower()
    if "american" in v or "美" in v:
        return "American Accent / 美式口音"
    if "australian" in v or "澳大利亚" in v:
        return "Australian Accent / 澳大利亚口音"
    if "british" in v or "英" in v:
        return "British Accent / 英国口音"
    if "chinese" in v or "中" in v:
        return "Chinese Accent / 中国口音"
    if "canadian" in v or "加拿大" in v:
        return "Canadian Accent / 加拿大口音"
    if "indian" in v or "印度" in v:
        return "Indian Accent / 印度口音"
    if "korean" in v or "韩" in v:
        return "Korean Accent / 韩国口音"
    if "portuguese" in v or "葡萄牙" in v:
        return "Portuguese Accent / 葡萄牙口音"
    if "russian" in v or "俄罗斯" in v:
        return "Russian Accent / 俄罗斯口音"
    if "japanese" in v or "日" in v:
        return "Japanese Accent / 日本口音"
    return "Auto"


def get_client() -> Client:
    hf_token = os.getenv("HF_TOKEN")
    if hf_token:
        print("[+] Authenticating Gradio Client with HF_TOKEN from environment.")
        return Client(SPACE_ID, token=hf_token)
    else:
        print("[!] Note: HF_TOKEN not set in .env. Connecting anonymously.")
        return Client(SPACE_ID)


def split_text_into_chunks(text: str, max_chars: int = 350) -> list[str]:
    cleaned_text = text.strip()
    if not cleaned_text:
        return []

    paragraphs = [p.strip() for p in cleaned_text.split("\n") if p.strip()]
    chunks = []

    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            chunks.append(paragraph)
        else:
            sentences = re.split(r"(?<=[.!?])\s+", paragraph)
            current_chunk = ""
            for sentence in sentences:
                if len(current_chunk) + len(sentence) + 1 <= max_chars:
                    current_chunk = f"{current_chunk} {sentence}".strip()
                else:
                    if current_chunk:
                        chunks.append(current_chunk)
                    current_chunk = sentence
            if current_chunk:
                chunks.append(current_chunk)

    return chunks if chunks else [cleaned_text]


def combine_audio_files(file_paths: list[str], output_path: str) -> str:
    audio_data = []
    sr = 24000
    for p in file_paths:
        if p and Path(p).exists():
            data, sample_rate = sf.read(p)
            sr = sample_rate
            audio_data.append(data)
    if not audio_data:
        raise RuntimeError("No valid audio files generated to combine.")
    combined = np.concatenate(audio_data)
    sf.write(output_path, combined, sr)
    return output_path


def test_voice_design(
    text: str,
    lang: str = "Auto",
    gender: str = "Auto",
    age: str = "Auto",
    pitch: str = "Auto",
    style: str = "Auto",
    accent: str = "Auto",
    dialect: str = "Auto",
    duration: float = 5.0,
    steps: int = 32,
    guidance_scale: float = 2.0,
    denoise: bool = True,
    speed: float = 1.0,
    output_path: str = "omnivoice_design_output.wav",
):
    print(f"\n[+] Initializing Gradio Client for HF Space: {SPACE_ID}...")
    start_time = time.time()

    client = get_client()
    print(f"[+] Client connected in {time.time() - start_time:.2f}s")

    gender_str = normalize_gender(gender)
    age_str = normalize_age(age)
    pitch_str = normalize_pitch(pitch)
    accent_str = normalize_accent(accent)

    chunks = split_text_into_chunks(text, max_chars=180)
    print(f"[+] Total text length: {len(text)} chars split across {len(chunks)} chunk(s)...")
    print(f"    - Gender: {gender_str}")
    print(f"    - Age: {age_str}")
    print(f"    - Pitch: {pitch_str}")
    print(f"    - Accent: {accent_str}")

    temp_audio_files = []
    for idx, chunk in enumerate(chunks, 1):
        chunk_dur = min(8.0, max(2.0, len(chunk) / 25.0))
        print(f"    [Chunk {idx}/{len(chunks)}] ({len(chunk)} chars, ~{chunk_dur:.1f}s dur): '{chunk[:40]}...'")

        result = client.predict(
            text=chunk,
            lang=lang,
            ns=float(steps),
            gs=float(guidance_scale),
            dn=denoise,
            sp=float(speed),
            du=chunk_dur,
            pp=True,
            po=True,
            param_9=gender_str,
            param_10=age_str,
            param_11=pitch_str,
            param_12=style,
            param_13=accent_str,
            param_14=dialect,
            api_name="/_design_fn",
        )

        audio_temp_path, status_text = result
        if audio_temp_path and Path(audio_temp_path).exists():
            temp_audio_files.append(audio_temp_path)
            print(f"        -> Generated chunk {idx} audio: {status_text}")
        else:
            print(f"        [!] Warning: Failed chunk {idx}: {status_text}")

    elapsed = time.time() - start_time

    if temp_audio_files:
        if len(temp_audio_files) == 1:
            target = Path(output_path)
            target.write_bytes(Path(temp_audio_files[0]).read_bytes())
        else:
            combine_audio_files(temp_audio_files, output_path)

        target = Path(output_path)
        print(f"\n[SUCCESS] Voice Design completed in {elapsed:.2f} seconds!")
        print(f"    [Saved Output Audio] --> {target.resolve()}\n")
        return str(target.resolve())
    else:
        print("    [!] Error: No audio generated for any chunks.")
        return None


def test_voice_clone(
    text: str,
    ref_aud: str,
    ref_text: str,
    instruct: str = "Synthesize natural speech",
    lang: str = "Auto",
    duration: float = 5.0,
    steps: int = 32,
    guidance_scale: float = 2.0,
    denoise: bool = True,
    speed: float = 1.0,
    output_path: str = "omnivoice_clone_output.wav",
):
    print(f"\n[+] Initializing Gradio Client for HF Space: {SPACE_ID}...")
    start_time = time.time()

    client = get_client()
    print(f"[+] Client connected in {time.time() - start_time:.2f}s")

    print(f"[+] Sending Voice Clone request for text: '{text}'...")
    print(f"    - Reference Audio: {ref_aud}")
    print(f"    - Reference Text: '{ref_text}'")

    # Format reference audio as handle_file if URL or local file path
    ref_aud_input = handle_file(ref_aud)

    result = client.predict(
        text=text,
        lang=lang,
        ref_aud=ref_aud_input,
        ref_text=ref_text,
        instruct=instruct,
        ns=float(steps),
        gs=float(guidance_scale),
        dn=denoise,
        sp=float(speed),
        du=float(duration),
        pp=True,
        po=True,
        api_name="/_clone_fn",
    )

    elapsed = time.time() - start_time
    audio_temp_path, status_text = result

    print(f"\n[SUCCESS] Voice Cloning completed in {elapsed:.2f} seconds!")
    print(f"    Status Message: {status_text}")
    print(f"    Temporary Audio: {audio_temp_path}")

    if audio_temp_path and Path(audio_temp_path).exists():
        target = Path(output_path)
        target.write_bytes(Path(audio_temp_path).read_bytes())
        print(f"    [Saved Output Audio] --> {target.resolve()}\n")
        return str(target.resolve())
    else:
        print("    [!] Error: Temporary audio file not found or empty.")
        return None


def main():
    parser = argparse.ArgumentParser(description="Test OmniVoice Gradio Client API")
    parser.add_argument(
        "--mode",
        choices=["design", "clone"],
        default="design",
        help="Mode: 'design' (voice properties) or 'clone' (reference audio voice cloning)",
    )
    parser.add_argument("--text", type=str, default="Hello! Welcome to OmniVoice Zero-Cost Speech Synthesis.", help="Text to synthesize")
    parser.add_argument("--lang", type=str, default="Auto", help="Target language (e.g. 'Auto', 'English', 'Chinese')")
    parser.add_argument("--duration", type=float, default=4.0, help="Expected audio duration in seconds")
    parser.add_argument("--out", type=str, default="omnivoice_test_output.wav", help="Output file path for generated audio")

    # Voice Design Parameters
    parser.add_argument("--gender", type=str, default="Auto", help="Gender: 'Female / 女', 'Male / 男', or 'Auto'")
    parser.add_argument("--age", type=str, default="Auto", help="Age group e.g. 'Young Adult / 青年'")
    parser.add_argument("--pitch", type=str, default="Auto", help="Pitch level e.g. 'Moderate Pitch / 中音调'")
    parser.add_argument("--accent", type=str, default="Auto", help="English accent e.g. 'American Accent / 美式口音'")

    # Voice Clone Parameters
    parser.add_argument("--ref-aud", type=str, help="Path or URL to reference WAV/MP3 file for voice cloning")
    parser.add_argument("--ref-text", type=str, help="Transcript of reference audio file")
    parser.add_argument("--instruct", type=str, default="Synthesize natural voice", help="Instruction prompt for cloning")

    args = parser.parse_args()

    if args.mode == "design":
        test_voice_design(
            text=args.text,
            lang=args.lang,
            gender=args.gender,
            age=args.age,
            pitch=args.pitch,
            accent=args.accent,
            duration=args.duration,
            output_path=args.out,
        )
    elif args.mode == "clone":
        if not args.ref_aud or not args.ref_text:
            print("[!] Error: --ref-aud and --ref-text are required when using --mode clone!")
            sys.exit(1)
        test_voice_clone(
            text=args.text,
            ref_aud=args.ref_aud,
            ref_text=args.ref_text,
            instruct=args.instruct,
            lang=args.lang,
            duration=args.duration,
            output_path=args.out,
        )


if __name__ == "__main__":
    main()




# uv run python scripts/test_omnivoice.py --mode "design" --gender "Female / 女" --age "Young Adult / 青年" --pitch "Auto" --accent "american" --out "test_human_flow.wav" --text "You know, when I first started thinking about how AI voices were evolving... I never really expected them to sound quite like this. [laughter] It's actually a bit surreal, if you think about it. I mean –– just a few years ago, we were entirely used to those robotic, stilted voices that sounded like they were reading from a rigid script. But now? Now it feels like there is an actual human breathing, thinking, and speaking on the other side of the screen. Let me take a step back –– I remember the first time I heard a truly expressive synthesizer. It was late at night, I was working on a project, and I just typed in a random sentence. When it played back, the voice didn't just read the words... it hesitated. It took a breath. [sigh] It felt the weight of the punctuation. That was the moment I realized everything was about to change. Sometimes, when you're speaking naturally, you speed up because you get excited about an idea –– like how we can now map specific emotions and acoustic properties into a latent space to generate zero-shot voice cloning in real time! And then... you slow down. You take a moment to let the listener absorb what you just said. You drop your pitch slightly. You let the silence do the heavy lifting. It's not just about the words, is it? It's about the spaces between them. A comma is a breath. An ellipsis is a thought forming in real-time. A dash is a sudden realization –– oh, wait, I forgot to mention how it handles accents! You can literally design a voice from scratch, dialing in the age, the pitch, the exact gender, and even the regional dialect, just by adjusting a few parameters. [laughter] Sorry, I get a little carried away when I talk about this stuff. But think about the applications! We are no longer just rendering audio; we are directing a performance. We are telling the system not just what to say, but how to feel while saying it. The rhythm, the cadence, the micro-pauses that make human speech so beautifully imperfect. So, what happens next? What happens when the line between a synthesized voice and a human recording becomes so blurred that you literally cannot tell the difference? I suppose... we're about to find out. We are stepping into an era where our digital creations can finally speak with the same warmth, hesitation, and passion that we do. And honestly –– that is both terrifying and absolutely incredible."

# uv run python scripts/test_omnivoice.py --mode design --text "You know, when I first started thinking about how AI voices were evolving... I never really expected them to sound quite like this. [laughter] It's actually a bit surreal, if you think about it. I mean –– just a few years ago, we were entirely used to those robotic, stilted voices that sounded like they were reading from a rigid script. But now? Now it feels like there is an actual human breathing, thinking, and speaking on the other side of the screen. Let me take a step back –– I remember the first time I heard a truly expressive synthesizer. It was late at night, I was working on a project, and I just typed in a random sentence. When it played back, the voice didn't just read the words... it hesitated. It took a breath. [sigh] It felt the weight of the punctuation. That was the moment I realized everything was about to change. Sometimes, when you're speaking naturally, you speed up because you get excited about an idea –– like how we can now map specific emotions and acoustic properties into a latent space to generate zero-shot voice cloning in real time! And then... you slow down. You take a moment to let the listener absorb what you just said. You drop your pitch slightly. You let the silence do the heavy lifting. It's not just about the words, is it? It's about the spaces between them. A comma is a breath. An ellipsis is a thought forming in real-time. A dash is a sudden realization –– oh, wait, I forgot to mention how it handles accents! You can literally design a voice from scratch, dialing in the age, the pitch, the exact gender, and even the regional dialect, just by adjusting a few parameters. [laughter] Sorry, I get a little carried away when I talk about this stuff. But think about the applications! We are no longer just rendering audio; we are directing a performance. We are telling the system not just what to say, but how to feel while saying it. The rhythm, the cadence, the micro-pauses that make human speech so beautifully imperfect. So, what happens next? What happens when the line between a synthesized voice and a human recording becomes so blurred that you literally cannot tell the difference? I suppose... we're about to find out. We are stepping into an era where our digital creations can finally speak with the same warmth, hesitation, and passion that we do. And honestly –– that is both terrifying and absolutely incredible." --gender "Female / 女" --age "Young Adult / 青年" --accent "American Accent / 美式口音" --out scripts/omnivoice_design.wav
