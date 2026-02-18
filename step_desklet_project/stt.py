import os
import threading
import time
import wave
from dataclasses import dataclass, field
from typing import List, Optional

API_KEY = os.getenv("ASSEMBLYAI_API_KEY") or "39bab637a3014802819715c4d965fbec"

SAMPLE_RATE = 16000
FRAMES_PER_BUFFER = 3200


def _import_streaming_sdk():
    try:
        from assemblyai.streaming.v3 import (
            StreamingClient,
            StreamingClientOptions,
            StreamingParameters,
            StreamingEvents,
        )
    except Exception as exc:
        raise RuntimeError(
            "AssemblyAI SDK not available. Install dependency: `assemblyai`."
        ) from exc

    return StreamingClient, StreamingClientOptions, StreamingParameters, StreamingEvents


@dataclass
class _TranscriptState:
    latest: str = ""
    finalized: List[str] = field(default_factory=list)
    error: Optional[str] = None
    last_update_ts: float = 0.0


def transcribe_wav_file(
    wav_path: str,
    *,
    api_key: Optional[str] = None,
    timeout_sec: float = 20.0,
) -> str:
    StreamingClient, StreamingClientOptions, StreamingParameters, StreamingEvents = _import_streaming_sdk()
    key = api_key or os.getenv("ASSEMBLYAI_API_KEY") or API_KEY
    if not key:
        raise RuntimeError("AssemblyAI API key missing. Set `ASSEMBLYAI_API_KEY`.")

    state = _TranscriptState()
    done = threading.Event()

    client = StreamingClient(StreamingClientOptions(api_key=key))
    params = StreamingParameters(sample_rate=SAMPLE_RATE, format_turns=True)

    def on_turn(_, event):
        text = (getattr(event, "transcript", "") or "").strip()
        if not text:
            return
        state.latest = text
        state.last_update_ts = time.time()

        # Guard for slightly different SDK event shapes.
        is_final = bool(
            getattr(event, "end_of_turn", False)
            or getattr(event, "is_final", False)
            or getattr(event, "turn_is_formatted", False)
        )
        if is_final:
            if not state.finalized or state.finalized[-1] != text:
                state.finalized.append(text)

    def on_terminated(_, __):
        done.set()

    def on_error(_, error):
        state.error = str(error)
        done.set()

    client.on(StreamingEvents.Turn, on_turn)
    client.on(StreamingEvents.Termination, on_terminated)
    client.on(StreamingEvents.Error, on_error)

    def safe_disconnect(wait_sec: float = 2.0):
        # Guard against SDK/network disconnect deadlocks.
        th = threading.Thread(target=client.disconnect, daemon=True)
        th.start()
        th.join(timeout=wait_sec)

    with wave.open(wav_path, "rb") as wav_file:
        if wav_file.getframerate() != SAMPLE_RATE:
            raise RuntimeError(
                f"Invalid sample rate {wav_file.getframerate()}Hz (expected {SAMPLE_RATE}Hz)."
            )
        if wav_file.getnchannels() != 1 or wav_file.getsampwidth() != 2:
            raise RuntimeError("Invalid WAV format (expected mono, 16-bit PCM).")

        client.connect(params)
        try:
            while True:
                audio_data = wav_file.readframes(FRAMES_PER_BUFFER)
                if not audio_data:
                    break
                client.stream(audio_data)
                # Smooth pacing to match real-time-ish stream behavior.
                time.sleep(0.02)
        finally:
            # Wait briefly for late STT events before disconnecting.
            wait_start = time.time()
            while time.time() - wait_start < timeout_sec and not done.is_set():
                if state.error:
                    break
                if state.last_update_ts and (time.time() - state.last_update_ts) >= 0.8:
                    break
                time.sleep(0.05)
            safe_disconnect()

    if state.error:
        raise RuntimeError(state.error)
    if state.finalized:
        return " ".join(state.finalized).strip()
    if state.latest:
        return state.latest.strip()
    raise RuntimeError("No transcript returned by STT.")


def main():
    StreamingClient, StreamingClientOptions, StreamingParameters, StreamingEvents = _import_streaming_sdk()
    try:
        import pyaudio
    except Exception as exc:
        raise RuntimeError(
            "PyAudio is required for live mic mode. Install dependency: `pyaudio`."
        ) from exc

    client = StreamingClient(StreamingClientOptions(api_key=API_KEY))
    params = StreamingParameters(sample_rate=SAMPLE_RATE, format_turns=True)

    def on_begin(_, event):
        print("Session ID:", event.id)

    def on_turn(_, event):
        if event.transcript:
            print(event.transcript, end="\r")

    def on_terminated(_, __):
        print("\nSession ended")

    def on_error(_, error):
        print("Error:", error)

    client.on(StreamingEvents.Begin, on_begin)
    client.on(StreamingEvents.Turn, on_turn)
    client.on(StreamingEvents.Termination, on_terminated)
    client.on(StreamingEvents.Error, on_error)

    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=FRAMES_PER_BUFFER,
    )

    client.connect(params)
    print("Listening...")

    try:
        while True:
            audio_data = stream.read(FRAMES_PER_BUFFER, exception_on_overflow=False)
            client.stream(audio_data)
    except KeyboardInterrupt:
        pass
    finally:
        client.disconnect()
        stream.stop_stream()
        stream.close()
        pa.terminate()


if __name__ == "__main__":
    main()
