"""Загрузка открытой русской модели Vosk в var/models. Пакет ставится отдельно."""
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
NAME = 'vosk-model-small-ru-0.22'
URL = f'https://alphacephei.com/vosk/models/{NAME}.zip'
folder = ROOT/'var/models'
folder.mkdir(parents=True, exist_ok=True)
archive = folder/(NAME+'.zip')
if not (folder/NAME/'conf/model.conf').is_file():
    with urlopen(URL, timeout=60) as response, archive.open('wb') as output:
        total = 0
        while chunk := response.read(1024*1024):
            total += len(chunk)
            if total > 100_000_000:
                raise ValueError('Архив превысил ожидаемый размер.')
            output.write(chunk)
    with ZipFile(archive) as zipped:
        for item in zipped.infolist():
            target = (folder/item.filename).resolve()
            if folder.resolve() not in target.parents or item.file_size > 150_000_000:
                raise ValueError('Некорректный путь или размер в архиве.')
        zipped.extractall(folder)
    manifest = {'url':URL, 'sha256_downloaded':hashlib.sha256(archive.read_bytes()).hexdigest(),
                'license':'Apache-2.0', 'license_source':'https://alphacephei.com/vosk/models',
                'note':'Контрольная сумма загруженного файла, не независимая подпись издателя.'}
    (folder/(NAME+'.source.json')).write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print('Русская модель Vosk готова:', folder/NAME)
