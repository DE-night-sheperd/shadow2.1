"""
Push-to-talk voice input for Shadow Core.

Deliberately NOT wake-word / always-listening: the microphone stream only
opens while VoiceRecorder.start() ... stop() is bracketed by an explicit UI
action (button held down). There is no background thread that keeps the mic
open waiting for a trigger word -- that pattern is excluded on purpose, the
same way watcher.py never touches the mic.

Transcription runs locally via whisper (no audio leaves the machine).
"""
import os
import tempfile
import wave

try:
    import sounddevice as sd
    import numpy as np
    _HAS_AUDIO = True
except ImportError:
    _HAS_AUDIO = False

_whisper_model = None  # loaded lazily, once, on first use


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        import whisper
        _whisper_model = whisper.load_model("base")
    return _whisper_model


class VoiceRecorder:
    """
    Usage:
        rec = VoiceRecorder()
        rec.start()      # call on button-press
        ...
        text = rec.stop_and_transcribe()   # call on button-release
    """

    SAMPLE_RATE = 16000

    def __init__(self):
        self._stream = None
        self._frames = []
        self._recording = False

    def start(self):
        if not _HAS_AUDIO:
            return
        self._frames = []
        self._recording = True

        def _callback(indata, frames, time_info, status):
            if self._recording:
                self._frames.append(indata.copy())

        self._stream = sd.InputStream(
            samplerate=self.SAMPLE_RATE, channels=1, dtype="int16", callback=_callback
        )
        self._stream.start()

    def stop_and_transcribe(self):
        if not _HAS_AUDIO:
            return "[voice error] 'sounddevice'/'numpy' not installed. Run: pip install sounddevice numpy"

        self._recording = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        if not self._frames:
            return ""

        audio = np.concatenate(self._frames, axis=0)

        tmp_path = os.path.join(tempfile.gettempdir(), "holo_voice_capture.wav")
        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # int16
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(audio.tobytes())

        try:
            model = _get_whisper_model()
            result = model.transcribe(tmp_path, fp16=False)
            return result.get("text", "").strip()
        except ImportError:
            return "[voice error] 'openai-whisper' not installed. Run: pip install openai-whisper"
        except Exception as e:
            return f"[voice error] transcription failed: {e}"
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
