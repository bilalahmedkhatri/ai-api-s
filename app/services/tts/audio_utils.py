"""app/services/tts/audio_utils.py — WAV helpers + duration/size metadata."""

import io
import wave

from app.services.tts.constants import DEFAULT_SAMPLE_RATE


def pcm_to_wav(
    pcm_bytes: bytes,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    num_channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Wrap raw PCM bytes in a RIFF WAV container if not already WAV formatted."""
    if pcm_bytes.startswith(b"RIFF"):
        return pcm_bytes
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def merge_wav_bytes(wav_list: list[bytes], silence_ms: int = 0) -> bytes:
    """Merge multiple WAV byte strings into one WAV (optional silence between parts)."""
    if not wav_list:
        return b""
    if len(wav_list) == 1:
        return wav_list[0]

    frames: list[bytes] = []
    params = None
    for wav in wav_list:
        with wave.open(io.BytesIO(wav), "rb") as w:
            if params is None:
                params = w.getparams()
            elif (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (
                params.nchannels, params.sampwidth, params.framerate,
            ):
                raise ValueError("Cannot merge WAV chunks with different audio formats.")
            frames.append(w.readframes(w.getnframes()))

    gap = b""
    if silence_ms > 0:
        n = int(params.framerate * silence_ms / 1000)
        gap = b"\x00" * (n * params.nchannels * params.sampwidth)

    out = io.BytesIO()
    with wave.open(out, "wb") as w_out:
        w_out.setparams(params)
        for i, f in enumerate(frames):
            if i and gap:
                w_out.writeframes(gap)
            w_out.writeframes(f)
    return out.getvalue()


def get_wav_stats(wav_bytes: bytes) -> dict:
    """
    Metadata for the frontend server:
      duration_sec — real audio length
      size_bytes   — file size (the "memory" the file takes in storage)
    """
    size_bytes = len(wav_bytes)
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as w:
            rate = w.getframerate() or DEFAULT_SAMPLE_RATE
            duration = w.getnframes() / rate
            return {
                "duration_sec": round(duration, 2),
                "size_bytes": size_bytes,
                "sample_rate": rate,
                "channels": w.getnchannels(),
                "frame_count": w.getnframes(),
            }
    except wave.Error:
        # fallback: assume 24 kHz / mono / 16-bit, minus 44-byte header
        duration = max(size_bytes - 44, 0) / (DEFAULT_SAMPLE_RATE * 2)
        return {
            "duration_sec": round(duration, 2),
            "size_bytes": size_bytes,
            "sample_rate": DEFAULT_SAMPLE_RATE,
            "channels": 1,
        }
