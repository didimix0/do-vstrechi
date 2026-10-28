from io import BytesIO
from pathlib import Path
import json, sys, tempfile, threading, unittest, wave
from urllib import request, error
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from do_vstrechi.voice_input import VoiceRecognizer, VoiceError, read_pcm, MAX_BYTES
from do_vstrechi.webapp import TrainerApp, make_server


def wav(seconds=0.5,rate=16000,channels=1,width=2):
    stream=BytesIO()
    with wave.open(stream,'wb') as output:
        output.setnchannels(channels);output.setsampwidth(width);output.setframerate(rate)
        output.writeframes(b'\x00'*(int(seconds*rate)*width*channels))
    return stream.getvalue()


class FakeRecognizer:
    def AcceptWaveform(self,chunk):return False
    def FinalResult(self):return json.dumps({'text':'здравствуйте я представитель втб'})


class VoiceTests(unittest.TestCase):
    def test_exact_audio_contract_and_limits(self):
        pcm,seconds=read_pcm(wav())
        self.assertEqual(len(pcm),16000);self.assertEqual(seconds,0.5)
        for invalid in (b'not wav',wav(channels=2),wav(width=1),wav(rate=48000),wav(seconds=.1),wav(seconds=46),wav()[:-10],b'x'*(MAX_BYTES+1)):
            with self.subTest(length=len(invalid)):
                with self.assertRaises(VoiceError):read_pcm(invalid)

    def test_no_model_needed_for_text_only_and_empty_voice_not_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            recognizer=VoiceRecognizer(folder)
            self.assertFalse(recognizer.status()['ready'])
            with self.assertRaises(VoiceError):recognizer.recognize(wav())
            class Empty(FakeRecognizer):
                def FinalResult(self):return '{"text":""}'
            recognizer=VoiceRecognizer(folder,Empty)
            with self.assertRaises(VoiceError):recognizer.recognize(wav())

    def test_audio_http_requires_token_and_returns_editable_text_without_turn(self):
        with tempfile.TemporaryDirectory() as folder:
            app=TrainerApp(Path(folder)/'test.sqlite3')
            app.voice=VoiceRecognizer(folder,FakeRecognizer)
            session=app.create({'mode':'guided','ending':'appointment'})
            server=make_server(app,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            url=f'http://127.0.0.1:{server.server_port}/api/voice/transcribe'
            def post(token,origin=None):
                headers={'Content-Type':'audio/wav','X-Trainer-Token':token}
                if origin:headers['Origin']=origin
                return request.urlopen(request.Request(url,data=wav(),headers=headers),timeout=3)
            try:
                for token,origin in (('wrong',None),(app.token,'https://example.com')):
                    with self.assertRaises(error.HTTPError) as exc:post(token,origin)
                    self.assertEqual(exc.exception.code,403)
                with post(app.token) as response:result=json.load(response)
                self.assertEqual(result['text'],'здравствуйте я представитель втб')
                self.assertTrue(result['editable'])
                self.assertEqual(app.get(session['id'])['history'],[])
                self.assertEqual(app.get(session['id'])['revision'],0)
                self.assertFalse(list(Path(folder).glob('*.wav')))
            finally:server.shutdown();server.server_close();thread.join(timeout=3)
