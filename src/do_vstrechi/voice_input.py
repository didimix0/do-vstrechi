"""Распознавание короткой реплики Vosk. Аудио не сохраняется на диск."""
from io import BytesIO
import json
from pathlib import Path
import sys
import threading
import wave
from .data import ROOT

MAX_SECONDS = 45
MAX_BYTES = 1_500_000
SAMPLE_RATE = 16_000
MODEL_NAME = 'vosk-model-small-ru-0.22'


class VoiceError(ValueError):
    pass


def read_pcm(raw):
    if not isinstance(raw, bytes) or not 44 <= len(raw) <= MAX_BYTES:
        raise VoiceError('Запишите реплику длительностью до 45 секунд.')
    try:
        with wave.open(BytesIO(raw), 'rb') as audio:
            if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (1, 2, SAMPLE_RATE, 'NONE'):
                raise VoiceError('Нужна запись WAV: один канал, 16 кГц, PCM 16 бит.')
            frames = audio.getnframes()
            if not SAMPLE_RATE//5 <= frames <= SAMPLE_RATE*MAX_SECONDS:
                raise VoiceError('Запишите реплику от 0,2 до 45 секунд.')
            pcm = audio.readframes(frames)
            if len(pcm) != frames*2:
                raise VoiceError('Запись получена не полностью. Попробуйте ещё раз.')
    except (wave.Error, EOFError) as exc:
        raise VoiceError('Не удалось прочитать запись. Попробуйте ещё раз.') from exc
    return pcm, frames/SAMPLE_RATE


class VoiceRecognizer:
    def __init__(self, root=ROOT, factory=None):
        self.runtime = Path(root)/'var/voice-runtime'
        self.model_path = Path(root)/'var/models'/MODEL_NAME
        self.factory = factory
        self.model = None
        self.lock = threading.Lock()

    def status(self):
        try:
            ready = bool(self.factory) or ((self.runtime/'vosk/__init__.py').is_file() and (self.model_path/'conf/model.conf').is_file())
        except OSError:
            ready = False
        return {'ready': ready, 'engine': 'Vosk', 'model': MODEL_NAME, 'max_seconds': MAX_SECONDS,
                'hint': 'Говорите, проверьте распознанный текст и отправьте.' if ready else 'Vosk пока не настроен. Текстовый ввод доступен.'}

    def recognize(self, raw):
        pcm, seconds = read_pcm(raw)
        if not self.status()['ready']:
            raise VoiceError('Vosk пока не настроен. Можно написать реплику вручную.')
        if not self.lock.acquire(blocking=False):
            raise VoiceError('Предыдущая запись ещё распознаётся. Подождите немного.')
        try:
            if self.factory:
                recognizer = self.factory()
            else:
                if str(self.runtime) not in sys.path:
                    sys.path.append(str(self.runtime))
                from vosk import Model, KaldiRecognizer, SetLogLevel
                SetLogLevel(-1)
                if self.model is None:
                    self.model = Model(str(self.model_path))
                recognizer = KaldiRecognizer(self.model, SAMPLE_RATE)
            segments = []
            for offset in range(0, len(pcm), 8000):
                if recognizer.AcceptWaveform(pcm[offset:offset+8000]):
                    segments.append(json.loads(recognizer.Result()).get('text', ''))
            segments.append(json.loads(recognizer.FinalResult()).get('text', ''))
            text = ' '.join(' '.join(segments).split()).strip()
            if not text:
                raise VoiceError('Речь не распознана. Попробуйте говорить ближе к микрофону или напишите реплику.')
            if len(text) > 4000:
                raise VoiceError('Реплика слишком длинная. Запишите её короче.')
            return {'text': text, 'seconds': round(seconds, 2), 'engine': 'Vosk', 'editable': True}
        except VoiceError:
            raise
        except Exception as exc:
            raise VoiceError('Не удалось распознать запись. Текстовый ввод доступен.') from exc
        finally:
            self.lock.release()
