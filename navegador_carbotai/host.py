#!/usr/bin/python3
"""Puente de mensajes nativos: socket Unix privado, sin red ni shell."""
import os, sys, json, struct, socket, stat, fcntl, select
from pathlib import Path
MAX=65536

def read_exact(stream,n):
    b=b''
    while len(b)<n:
        x=stream.read(n-len(b)) if hasattr(stream,'read') else stream.recv(n-len(b))
        if not x: raise EOFError
        b+=x
    return b

def receive(stream):
    n=struct.unpack('=I',read_exact(stream,4))[0]
    if not 0<n<=MAX: raise ValueError('longitud')
    return json.loads(read_exact(stream,n))

def encode(m):
    b=json.dumps(m,ensure_ascii=True).encode();return struct.pack('=I',len(b))+b

def main():
    manifest=json.loads((Path(__file__).parent/'extension'/'manifest.json').read_text())
    import hashlib,base64
    ident=''.join('abcdefghijklmnop'[int(x,16)] for x in hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32])
    if len(sys.argv)<2 or sys.argv[1]!=f'chrome-extension://{ident}/':return
    runtime=Path(os.environ.get('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}'))
    if runtime.stat().st_uid!=os.getuid() or runtime.stat().st_mode & 0o077: return
    os.umask(0o077)
    lock=open(runtime/'carbotai-browser.lock','a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:return
    path=runtime/'carbotai-browser.sock'
    if path.exists() or path.is_symlink():
        if not stat.S_ISSOCK(path.lstat().st_mode) or path.lstat().st_uid!=os.getuid():return
        path.unlink()
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as srv:
        srv.bind(str(path));os.chmod(path,0o600);srv.listen(4)
        try:
            while True:
                ready,_,_=select.select([srv,sys.stdin.buffer],[],[])
                if sys.stdin.buffer in ready:return
                conn,_=srv.accept()
                with conn:
                    conn.settimeout(2)
                    try:
                        _,uid,_=struct.unpack('3i',conn.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                        if uid!=os.getuid():continue
                        m=receive(conn)
                        if not isinstance(m,dict) or m.get('op') not in {'status','web','window','face_open','face_isolate','tab_close','site_search'}: continue
                    except (OSError,EOFError,ValueError):continue
                    # Una sola solicitud en vuelo. Si vence, terminar el puente para
                    # no confundir una respuesta tardía con la siguiente orden.
                    try:
                        sys.stdout.buffer.write(encode(m));sys.stdout.buffer.flush()
                        if not select.select([sys.stdin.buffer],[],[],5)[0]:return
                        reply=receive(sys.stdin.buffer)
                    except (OSError,EOFError,ValueError):return
                    try:conn.sendall(encode(reply))
                    except OSError:pass
        finally:path.unlink(missing_ok=True)

if __name__=='__main__':main()
