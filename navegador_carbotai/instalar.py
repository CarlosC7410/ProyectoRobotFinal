#!/usr/bin/python3
"""Instalación local del puente y manifiesto; no instala la extensión sin permiso."""
import os, json, base64, hashlib, shutil
from pathlib import Path
if os.getuid()==0:raise SystemExit('Ejecuta como tu usuario normal, sin sudo.')
source=Path(__file__).resolve().parent
base=Path.home()/'.local/share/carbotai-browser'
base.mkdir(parents=True,exist_ok=True)
shutil.copytree(source/'extension',base/'extension',dirs_exist_ok=True)
shutil.copy2(source/'host.py',base/'host.py');(base/'host.py').chmod(0o700)
m=json.loads((base/'extension/manifest.json').read_text())
id=''.join('abcdefghijklmnop'[int(x,16)] for x in hashlib.sha256(base64.b64decode(m['key'])).hexdigest()[:32])
config=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))
for browser in ('chromium','google-chrome'):
    d=config/browser/'NativeMessagingHosts';d.mkdir(parents=True,exist_ok=True)
    (d/'org.carbotai.browser.json').write_text(json.dumps({'name':'org.carbotai.browser','description':'Carbotai local','path':str(base/'host.py'),'type':'stdio','allowed_origins':[f'chrome-extension://{id}/']},indent=2)+'\n')
print('Puente instalado. En Chromium abre chrome://extensions, activa Modo desarrollador y Cargar descomprimida:')
print(base/'extension');print('ID esperado:',id)
