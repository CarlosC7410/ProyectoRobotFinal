"""Charsbotai: cámara, vista previa, fotos, videos y visión local.

Un único archivo y un único propietario de Picamera2. El proceso auxiliar
usa Python del sistema para no alterar el entorno virtual de voz. No se abre
la cámara al importar ni se guardan imágenes sin una orden explícita.
La observación continua se solicita explícitamente desde el arranque principal.
"""
import datetime as dt
import importlib.util
import json
import math
import os
from pathlib import Path
import queue
import re
import select
import shutil
import signal
import stat
from contextlib import contextmanager
import subprocess
import sys
import threading
import time
import unicodedata
import uuid

VERSION_CAMARA = "camara-1.5-vision-saludos"

# ================================================================
# CONFIGURACIÓN EDITABLE POR EL USUARIO: RUTAS DE ALMACENAMIENTO
# ================================================================
RUTA_FOTOS_LOCAL = Path.home() / "Imágenes" / "Fotos De Camara"
RUTA_VIDEOS_LOCAL = Path.home() / "Vídeos" / "Videos De Camara"
# Sustituye estos valores por el punto de montaje real y el UUID de lsblk -f.
# Mientras estén vacíos no se permitirá guardar en el disco externo.
RUTA_DISCO_EXTERNO = "/media/carlos7410/Nuevo vol"
UUID_DISCO_EXTERNO = "6CF1-36DA"
# Rutas RELATIVAS dentro de ese disco. No deben contener '..'.
RUTA_FOTOS_EXTERNO = "Archivos De Raspberry/Fotos De Camara"
RUTA_VIDEOS_EXTERNO = "Archivos De Raspberry/Videos De Camara"

# ================================================================
# CONFIGURACIÓN DE LA CÁMARA (valores iniciales para Raspberry Pi 5)
# ================================================================
PYTHON_CAMARA = "/usr/bin/python3"
INDICE_CAMARA = 0
RESOLUCION_VIDEO = (1920, 1080)
RESOLUCION_VISTA = (640, 360)  # ventana inicial 960x540, escalada
RESOLUCION_FOTO = (4608, 2592)
FPS_VIDEO = 20
FPS_VISTA = 8
BITRATE_VIDEO = 5_000_000
ESPACIO_MINIMO_FOTO = 32 * 1024**2
ESPACIO_MINIMO_VIDEO = 256 * 1024**2
RESERVA_VIDEO = 64 * 1024**2

# ================================================================
# VISIÓN ARTIFICIAL LOCAL (observación, consultas y visor; no guarda imágenes)
# ================================================================
# NanoDet ya fue probado por separado. Carga perezosa en el Python del
# sistema: no se instala OpenCV dentro del entorno virtual de voz.
CARPETA_NANODET = Path(__file__).resolve().parent.parent / "modelos" / "vision" / "nanodet"
ARCHIVO_MODELO_VISION = CARPETA_NANODET / "object_detection_nanodet_2022nov.onnx"
ARCHIVO_NANODET_PY = CARPETA_NANODET / "nanodet.py"
CONFIANZA_VISION = 0.45  # Valor provisional comprobado por el usuario.
CONFIANZA_PERSONAS = 0.55  # Mitigar falsos positivos (abanico -> persona).
HILOS_VISION = 2
FPS_VISION_VISIBLE = 3  # Máximo inicial; priorizar voz y grabación.
VISION_AL_INICIAR = True  # Editable; no se abre al importar el módulo.
FPS_VISION_FONDO = 1
VISION_REINTENTO_SEGUNDOS = 5
VISION_MAX_ANTIGUEDAD = 3  # No comunicar una muestra antigua como actual.

# Orden oficial de las 80 categorías COCO del modelo OpenCV Zoo.
CLASES_VISION = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush"
)
# Singular y plural para respuestas en español; no identifica personas por nombre.
NOMBRES_VISION = {
    "person": ("persona", "personas"), "bicycle": ("bicicleta", "bicicletas"),
    "car": ("automóvil", "automóviles"), "motorcycle": ("motocicleta", "motocicletas"),
    "airplane": ("avión", "aviones"), "bus": ("autobús", "autobuses"),
    "train": ("tren", "trenes"), "truck": ("camión", "camiones"),
    "boat": ("barco", "barcos"), "traffic light": ("semáforo", "semáforos"),
    "fire hydrant": ("hidrante", "hidrantes"), "stop sign": ("señal de alto", "señales de alto"),
    "parking meter": ("parquímetro", "parquímetros"), "bench": ("banca", "bancas"),
    "bird": ("ave", "aves"), "cat": ("gato", "gatos"), "dog": ("perro", "perros"),
    "horse": ("caballo", "caballos"), "sheep": ("oveja", "ovejas"),
    "cow": ("vaca", "vacas"), "elephant": ("elefante", "elefantes"),
    "bear": ("oso", "osos"), "zebra": ("cebra", "cebras"),
    "giraffe": ("jirafa", "jirafas"), "backpack": ("mochila", "mochilas"),
    "umbrella": ("paraguas", "paraguas"), "handbag": ("bolso", "bolsos"),
    "tie": ("corbata", "corbatas"), "suitcase": ("maleta", "maletas"),
    "frisbee": ("disco volador", "discos voladores"), "skis": ("esquí", "esquís"),
    "snowboard": ("tabla de nieve", "tablas de nieve"), "sports ball": ("pelota", "pelotas"),
    "kite": ("cometa", "cometas"), "baseball bat": ("bate", "bates"),
    "baseball glove": ("guante de béisbol", "guantes de béisbol"),
    "skateboard": ("patineta", "patinetas"), "surfboard": ("tabla de surf", "tablas de surf"),
    "tennis racket": ("raqueta", "raquetas"), "bottle": ("botella", "botellas"),
    "wine glass": ("copa", "copas"), "cup": ("taza", "tazas"),
    "fork": ("tenedor", "tenedores"), "knife": ("cuchillo", "cuchillos"),
    "spoon": ("cuchara", "cucharas"), "bowl": ("tazón", "tazones"),
    "banana": ("plátano", "plátanos"), "apple": ("manzana", "manzanas"),
    "sandwich": ("sándwich", "sándwiches"), "orange": ("naranja", "naranjas"),
    "broccoli": ("brócoli", "brócolis"), "carrot": ("zanahoria", "zanahorias"),
    "hot dog": ("hot dog", "hot dogs"), "pizza": ("pizza", "pizzas"),
    "donut": ("dona", "donas"), "cake": ("pastel", "pasteles"),
    "chair": ("silla", "sillas"), "couch": ("sofá", "sofás"),
    "potted plant": ("planta", "plantas"), "bed": ("cama", "camas"),
    "dining table": ("mesa", "mesas"), "toilet": ("inodoro", "inodoros"),
    "tv": ("televisión", "televisiones"), "laptop": ("computadora portátil", "computadoras portátiles"),
    "mouse": ("ratón de computadora", "ratones de computadora"),
    "remote": ("control remoto", "controles remotos"),
    "keyboard": ("teclado", "teclados"), "cell phone": ("celular", "celulares"),
    "microwave": ("microondas", "microondas"), "oven": ("horno", "hornos"),
    "toaster": ("tostadora", "tostadoras"), "sink": ("fregadero", "fregaderos"),
    "refrigerator": ("refrigerador", "refrigeradores"), "book": ("libro", "libros"),
    "clock": ("reloj", "relojes"), "vase": ("florero", "floreros"),
    "scissors": ("tijeras", "tijeras"), "teddy bear": ("oso de peluche", "osos de peluche"),
    "hair drier": ("secadora de cabello", "secadoras de cabello"),
    "toothbrush": ("cepillo de dientes", "cepillos de dientes")
}


def _preparar_imagen_vision(imagen, cv2):
    """Letterbox 416x416 compatible con la demostración oficial NanoDet."""
    import numpy as np
    rgb = np.asarray(imagen.convert("RGB"), dtype=np.uint8)
    alto, ancho = rgb.shape[:2]
    if alto <= 0 or ancho <= 0:
        raise ErrorCamara("La imagen capturada no tiene dimensiones válidas.")
    escala = min(416 / ancho, 416 / alto)
    nuevo_ancho = min(416, max(1, int(ancho * escala)))
    nuevo_alto = min(416, max(1, int(alto * escala)))
    ajustada = cv2.resize(rgb, (nuevo_ancho, nuevo_alto), interpolation=cv2.INTER_AREA)
    izquierda = (416 - nuevo_ancho) // 2
    arriba = (416 - nuevo_alto) // 2
    entrada = cv2.copyMakeBorder(
        ajustada, arriba, 416 - nuevo_alto - arriba,
        izquierda, 416 - nuevo_ancho - izquierda,
        cv2.BORDER_CONSTANT, value=0
    )
    return entrada, (alto, ancho, arriba, izquierda, nuevo_alto, nuevo_ancho)


def _interpretar_detecciones_vision(predicciones, escala):
    """Convierte coordenadas a la imagen original y filtra por confianza."""
    alto, ancho, arriba, izquierda, nuevo_alto, nuevo_ancho = escala
    resultado = []
    for fila in predicciones:
        if len(fila) < 6:
            continue
        categoria_id = int(fila[5])
        if not 0 <= categoria_id < len(CLASES_VISION):
            continue
        clase = CLASES_VISION[categoria_id]
        confianza = float(fila[4])
        umbral = CONFIANZA_PERSONAS if clase == "person" else CONFIANZA_VISION
        if confianza < umbral:
            continue
        x1 = max(0.0, min(float(ancho), (float(fila[0]) - izquierda) * ancho / nuevo_ancho))
        x2 = max(0.0, min(float(ancho), (float(fila[2]) - izquierda) * ancho / nuevo_ancho))
        y1 = max(0.0, min(float(alto), (float(fila[1]) - arriba) * alto / nuevo_alto))
        y2 = max(0.0, min(float(alto), (float(fila[3]) - arriba) * alto / nuevo_alto))
        if x2 <= x1 or y2 <= y1:
            continue
        posicion = "izquierda" if (x1 + x2) / 2 < ancho / 3 else ("derecha" if (x1 + x2) / 2 > 2 * ancho / 3 else "centro")
        resultado.append({"clase": clase, "confianza": round(confianza, 3), "posicion": posicion,
                          "recuadro": (round(x1), round(y1), round(x2), round(y2))})
    return resultado


def _dibujar_detecciones_vision(imagen, detecciones):
    """Anotar una copia de la misma imagen analizada; nunca el archivo original."""
    from PIL import ImageDraw, ImageFont
    salida = imagen.convert("RGB").copy()
    dibujo = ImageDraw.Draw(salida)
    try:
        fuente = ImageFont.truetype("DejaVuSans.ttf", 14)
    except OSError:
        fuente = ImageFont.load_default()
    for d in detecciones:
        x1, y1, x2, y2 = d["recuadro"]
        x1, x2 = max(0, min(x1, salida.width - 1)), max(0, min(x2, salida.width - 1))
        y1, y2 = max(0, min(y1, salida.height - 1)), max(0, min(y2, salida.height - 1))
        color = "#55db83" if d["clase"] == "person" else "#ffd36a"
        texto = f"{NOMBRES_VISION[d['clase']][0]} {d['confianza']:.0%}"
        dibujo.rectangle((x1, y1, x2, y2), outline=color, width=2)
        caja = dibujo.textbbox((0, 0), texto, font=fuente)
        w, h = caja[2] - caja[0] + 6, caja[3] - caja[1] + 6
        tx = max(0, min(x1, salida.width - w))
        ty = max(0, y1 - h)
        dibujo.rectangle((tx, ty, tx+w, ty+h), fill=color)
        dibujo.text((tx+3-caja[0], ty+3-caja[1]), texto, font=fuente, fill="black")
    return salida


def _respuesta_vision(detecciones, consulta="general", categoria=None):
    """Respuestas delimitadas por detecciones reales; no delega a Ollama."""
    from collections import Counter
    conteo = Counter(d["clase"] for d in detecciones)
    if consulta == "personas":
        n = conteo["person"]
        return f"Según mi cámara, detecto {n} {'persona' if n == 1 else 'personas'} en este momento."
    if consulta == "posicion_persona":
        personas = [d for d in detecciones if d["clase"] == "person"]
        if not personas:
            return "No detecto ninguna persona con suficiente confianza en este momento."
        if len(personas) != 1:
            return f"Detecto {len(personas)} personas; están en distintas posiciones de la imagen."
        return f"Detecto una persona en la {personas[0]['posicion'] if personas[0]['posicion'] != 'centro' else 'parte central'} de mi imagen."
    if consulta == "objeto":
        if categoria not in CLASES_VISION:
            return "Ese objeto no está entre las categorías que puedo reconocer con este modelo."
        n = conteo[categoria]
        singular, plural = NOMBRES_VISION[categoria]
        if not n:
            return f"No detecto {singular} con suficiente confianza en este momento."
        return f"Según mi cámara, detecto {n} {singular if n == 1 else plural}."
    if not conteo:
        return "En esta imagen no pude identificar objetos con suficiente confianza."
    partes = []
    for clase, cantidad in sorted(conteo.items(), key=lambda elemento: (-elemento[1], elemento[0]))[:8]:
        singular, plural = NOMBRES_VISION.get(clase, (clase, clase))
        partes.append(f"{cantidad} {singular if cantidad == 1 else plural}")
    mas = " También podría haber otros objetos." if len(conteo) > 8 else ""
    return "Según mi cámara, detecto: " + ", ".join(partes) + "." + mas



class ErrorCamara(Exception):
    pass


class DiscoNoDisponible(ErrorCamara):
    pass


def _montaje_externo():
    if not RUTA_DISCO_EXTERNO or not UUID_DISCO_EXTERNO:
        raise DiscoNoDisponible("No encontré el almacenamiento externo configurado. Falta configurar su ruta o UUID.")
    raiz = Path(RUTA_DISCO_EXTERNO).expanduser().resolve()
    try:
        r = subprocess.run(["findmnt", "-n", "-r", "-o", "UUID,MAJ:MIN", "--mountpoint", str(raiz)],
                           capture_output=True, text=True, timeout=3, check=True)
        datos = r.stdout.strip().split()
        if len(datos) != 2 or datos[0].lower() != UUID_DISCO_EXTERNO.strip().lower():
            raise ValueError("UUID distinto")
        info = raiz.stat()
        if datos[1] != f"{os.major(info.st_dev)}:{os.minor(info.st_dev)}":
            raise ValueError("montaje distinto")
        if raiz == Path("/") or info.st_dev == Path("/").stat().st_dev:
            raise ValueError("no es una unidad separada")
        return raiz, info.st_dev
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        raise DiscoNoDisponible("No encontré el almacenamiento externo que me solicitaste. Comprueba que esté conectado y montado.") from e



def consultar_estado_disco_externo():
    """Verifica el volumen configurado sin montar, escribir ni activar cámara."""
    try:
        _montaje_externo()
        return "El disco externo configurado está montado y reconocido."
    except DiscoNoDisponible:
        return "El disco externo configurado no está montado o no pude verificarlo."


class Destino:
    """Ancla las escrituras a un descriptor abierto de la carpeta real.

    Si desaparece el montaje, nunca vuelve a abrir el nombre de ruta sobre
    la microSD. No sigue enlaces simbólicos dentro del disco externo.
    """
    def __init__(self, tipo, externo):
        self.externo = externo
        self.fd = None
        try:
            if externo:
                raiz, dispositivo = _montaje_externo()
                relativo = Path(RUTA_FOTOS_EXTERNO if tipo == "foto" else RUTA_VIDEOS_EXTERNO)
                if relativo.is_absolute() or ".." in relativo.parts:
                    raise ErrorCamara("La ruta externa debe ser relativa al disco, sin puntos dobles.")
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                self.fd = os.open(raiz, flags)
                if os.fstat(self.fd).st_dev != dispositivo:
                    raise DiscoNoDisponible("El disco externo cambió durante la comprobación.")
                for parte in relativo.parts:
                    try:
                        os.mkdir(parte, dir_fd=self.fd)
                    except FileExistsError:
                        pass
                    nuevo = os.open(parte, flags, dir_fd=self.fd)
                    os.close(self.fd)
                    self.fd = nuevo
                    if os.fstat(self.fd).st_dev != dispositivo:
                        raise DiscoNoDisponible("La carpeta no pertenece al disco externo solicitado.")
                self.ruta = raiz / relativo
                self.comprobar()
            else:
                self.ruta = Path(RUTA_FOTOS_LOCAL if tipo == "foto" else RUTA_VIDEOS_LOCAL).expanduser().resolve()
                self.ruta.mkdir(parents=True, exist_ok=True)
                self.fd = os.open(self.ruta, os.O_RDONLY | os.O_DIRECTORY)
            prueba = ".charsbotai-prueba-" + uuid.uuid4().hex
            f = os.open(prueba, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=self.fd)
            os.close(f)
            os.unlink(prueba, dir_fd=self.fd)
            if self.libre() < (ESPACIO_MINIMO_FOTO if tipo == "foto" else ESPACIO_MINIMO_VIDEO):
                raise ErrorCamara("No hay suficiente espacio libre en el almacenamiento solicitado.")
        except Exception:
            self.cerrar()
            raise

    def comprobar(self):
        if self.externo:
            _, dispositivo = _montaje_externo()
            if os.fstat(self.fd).st_dev != dispositivo or self.ruta.stat().st_dev != dispositivo:
                raise DiscoNoDisponible("El almacenamiento externo dejó de estar disponible.")
            # También detecta si el directorio fue sustituido dentro del montaje.
            if self.ruta.stat().st_ino != os.fstat(self.fd).st_ino:
                raise DiscoNoDisponible("La carpeta externa cambió durante la operación.")

    def libre(self):
        info = os.fstatvfs(self.fd)
        return info.f_bavail * info.f_frsize

    def anclada(self, nombre):
        return f"/proc/{os.getpid()}/fd/{self.fd}/{nombre}"

    def cerrar(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cerrar()


def _nombre(prefijo, extension):
    return f"{prefijo}_{dt.datetime.now():%Y-%m-%d_%H-%M-%S_%f}_{uuid.uuid4().hex[:6]}.{extension}"



# ================================================================
# VISOR DE ARCHIVOS CAPTURADOS: independiente de la cámara física.
# ================================================================
class VisorArchivo:
    """Ventana propia; el lector de video no ejecuta operaciones de Tk."""
    TAM_VIDEO = (640, 360)

    def __init__(self, tipo, ruta, externo, identidad, fd, mostrar=True):
        from PIL import Image
        self.tipo, self.ruta, self.externo, self.identidad = tipo, ruta, externo, identidad
        self.ventana = self.etiqueta = self.img_tk = None
        self.imagen = None
        self.minimizada = False
        self.geometria = "960x570"
        self.proceso = self.lector = None
        self.parar = threading.Event()
        self.fotogramas = queue.Queue(maxsize=1)
        self.primer_frame = threading.Event()
        self.fin = threading.Event()
        self.estado = ""
        self._ultimo_tamano = None
        try:
            if tipo == "foto":
                with os.fdopen(os.dup(fd), "rb") as archivo:
                    with Image.open(archivo) as imagen:
                        self.imagen = imagen.convert("RGB")
                        self.imagen.thumbnail((1920, 1080))
            else:
                if not shutil.which("ffmpeg"):
                    raise ErrorCamara("No encontré FFmpeg para reproducir el video.")
                # -re conserva la velocidad temporal del video. Solo un
                # fotograma pendiente; no se acumula el video en memoria.
                ancho, alto = self.TAM_VIDEO
                filtro = (f"scale={ancho}:{alto}:force_original_aspect_ratio=decrease,"
                          f"pad={ancho}:{alto}:(ow-iw)/2:(oh-ih)/2,setsar=1")
                self.proceso = subprocess.Popen(
                    ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                     "-re", "-threads", "2", "-i", f"/proc/self/fd/{fd}",
                     "-map", "0:v:0", "-an", "-sn", "-dn", "-filter_threads", "1",
                     "-vf", filtro, "-pix_fmt", "rgb24", "-threads", "1",
                     "-f", "rawvideo", "pipe:1"],
                    pass_fds=(fd,), stdout=subprocess.PIPE, stderr=None)
                self.lector = threading.Thread(target=self._leer_video, daemon=True)
                self.lector.start()
                if not self.primer_frame.wait(6) or self.fotogramas.empty():
                    raise ErrorCamara("No pude decodificar imágenes del video con FFmpeg.")
                self.imagen = self.fotogramas.get_nowait()
            if mostrar:
                self._crear_ventana()
        except Exception:
            self.cerrar()
            raise

    def _leer_video(self):
        from PIL import Image
        ancho, alto = self.TAM_VIDEO
        cantidad = ancho * alto * 3
        p = self.proceso
        try:
            while not self.parar.is_set():
                datos = p.stdout.read(cantidad)
                if len(datos) != cantidad:
                    break
                imagen = Image.frombytes("RGB", (ancho, alto), datos)
                try:
                    self.fotogramas.get_nowait()
                except queue.Empty:
                    pass
                self.fotogramas.put_nowait(imagen)
                self.primer_frame.set()
            codigo = p.wait()
            if not self.parar.is_set():
                self.estado = "Video terminado" if codigo == 0 else "Error durante la reproducción"
        except Exception as e:
            if not self.parar.is_set():
                self.estado = "Error durante la reproducción"
                print(f"[Visor] {e}", file=sys.stderr, flush=True)
        finally:
            self.fin.set()
            self.primer_frame.set()

    def _crear_ventana(self):
        import tkinter as tk
        ventana = tk.Tk()
        self.ventana = ventana
        try:
            ventana.title("Charsbotai - " + ("Fotografía" if self.tipo == "foto" else "Video"))
            ventana.geometry(self.geometria)
            ventana.configure(bg="black")
            self.etiqueta = tk.Label(ventana, bg="black")
            self.etiqueta.pack(fill="both", expand=True)
            self.barra = tk.Label(ventana, text=self.ruta.name, bg="#222", fg="white")
            self.barra.pack(fill="x")
            ventana.protocol("WM_DELETE_WINDOW", self.cerrar)
            self.minimizada = False
            self._ultimo_tamano = None
            self.dibujar()
        except Exception:
            self._destruir_ventana()
            raise

    def _destruir_ventana(self):
        ventana, self.ventana = self.ventana, None
        self.img_tk = self.etiqueta = None
        if ventana is not None:
            ventana.destroy()

    def dibujar(self):
        from PIL import ImageTk
        cambio = False
        try:
            imagen = self.fotogramas.get_nowait()
            self.imagen = imagen
            cambio = True
        except queue.Empty:
            pass
        if self.ventana is None:
            return
        self.ventana.update_idletasks()
        self.ventana.update()
        if self.ventana is None or self.ventana.state() in ("iconic", "withdrawn"):
            return
        if self.estado:
            self.barra.configure(text=self.estado + " · " + self.ruta.name)
        ancho = max(1, self.etiqueta.winfo_width())
        alto = max(1, self.etiqueta.winfo_height())
        if self.imagen is not None and (cambio or self._ultimo_tamano != (ancho, alto)):
            escala = min(ancho / self.imagen.width, alto / self.imagen.height)
            imagen = self.imagen.resize((max(1, int(self.imagen.width * escala)),
                                         max(1, int(self.imagen.height * escala))))
            self.img_tk = ImageTk.PhotoImage(imagen, master=self.ventana)
            self.etiqueta.configure(image=self.img_tk)
            self._ultimo_tamano = (ancho, alto)

    def minimizar(self):
        if not self.minimizada:
            self.geometria = self.ventana.geometry()
        self.ventana.iconify()
        self.minimizada = True

    def restaurar(self):
        # Mismo procedimiento comprobado en la Raspberry con la cámara:
        # reconstruir la ventana, sin reiniciar el lector ni la grabación.
        if self.minimizada or self.ventana.state() in ("iconic", "withdrawn"):
            if not self.minimizada:
                self.geometria = self.ventana.geometry()
            self._destruir_ventana()
            self._crear_ventana()
        else:
            self.ventana.lift()
        self.minimizada = False

    def cerrar(self):
        self.parar.set()
        self._destruir_ventana()
        p = self.proceso
        if p is not None and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2)
        if self.lector is not None:
            self.lector.join(timeout=2)
        if p is not None and p.stdout is not None:
            p.stdout.close()
        self.proceso = self.lector = None
        self.imagen = None
        self.minimizada = False


def _identidad(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _archivo_registrado(tipo, ruta, externo, identidad):
    """Abre solo un archivo de captura conocido; no crea carpetas ni archivos."""
    carpeta = fd = None
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        if externo:
            raiz, dispositivo = _montaje_externo()
            relativo = Path(RUTA_FOTOS_EXTERNO if tipo == "foto" else RUTA_VIDEOS_EXTERNO)
            if relativo.is_absolute() or ".." in relativo.parts or ruta.parent != raiz / relativo:
                raise ErrorCamara("La ruta del archivo no pertenece al almacenamiento configurado.")
            carpeta = os.open(raiz, flags)
            if os.fstat(carpeta).st_dev != dispositivo:
                raise DiscoNoDisponible("El almacenamiento externo cambió.")
            for parte in relativo.parts:
                nuevo = os.open(parte, flags, dir_fd=carpeta)
                os.close(carpeta)
                carpeta = nuevo
                if os.fstat(carpeta).st_dev != dispositivo:
                    raise DiscoNoDisponible("La carpeta no pertenece al disco externo solicitado.")
        else:
            esperada = Path(RUTA_FOTOS_LOCAL if tipo == "foto" else RUTA_VIDEOS_LOCAL).expanduser().resolve()
            if ruta.parent != esperada:
                raise ErrorCamara("La ruta del archivo no pertenece a la carpeta local configurada.")
            carpeta = os.open(esperada, flags)
        fd = os.open(ruta.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=carpeta)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or _identidad(info) != identidad:
            raise ErrorCamara("El archivo cambió desde que lo guardé. No realizaré la operación.")
        if externo:
            _, dispositivo = _montaje_externo()
            if info.st_dev != dispositivo:
                raise DiscoNoDisponible("El almacenamiento externo cambió.")
        yield carpeta, fd
    except FileNotFoundError as e:
        raise ErrorCamara("No encontré el archivo que guardé.") from e
    finally:
        if fd is not None:
            os.close(fd)
        if carpeta is not None:
            os.close(carpeta)


class AdministradorCamara:
    """Vive exclusivamente en el proceso auxiliar; las órdenes son seriales."""
    def __init__(self):
        self.cam = None
        self.ventana = None
        self.etiqueta = None
        self.img_tk = None
        self.fullscreen = False
        self.minimizada_por_voz = False
        self.geometria_minimizada = None
        self.mantener_activa = False
        self.grabacion = None
        self.ultima_foto = None
        self.ultimo_video = None
        self.visores = {}
        self.identidades = {}
        self.borrado_pendiente = None
        self.ultimo_error_grabacion = None
        self.proxima_vista = 0
        self.proxima_revision = 0
        self.modelo_vision = None  # Se carga al activar la visión, no al importar.
        self.vista_vision = False
        self.estado_vista = None
        self.vision_continua = False
        self.privacidad = "--privacidad" in sys.argv
        self.proxima_vision = 0
        self.ultimas_detecciones = None  # Solo metadatos en RAM.
        self.ultima_vision = None
        self.error_vision = None

    def _cargar_modelo_vision(self):
        if self.modelo_vision is not None:
            return self.modelo_vision
        if not ARCHIVO_MODELO_VISION.is_file() or not ARCHIVO_NANODET_PY.is_file():
            raise ErrorCamara("No encuentro el modelo NanoDet o su archivo nanodet.py en modelos/vision/nanodet.")
        import cv2
        cv2.setNumThreads(HILOS_VISION)
        spec = importlib.util.spec_from_file_location("carbotai_nanodet_local", str(ARCHIVO_NANODET_PY))
        if spec is None or spec.loader is None:
            raise ErrorCamara("No pude cargar el programa NanoDet.")
        modulo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(modulo)
        modelo = modulo.NanoDet(
            modelPath=str(ARCHIVO_MODELO_VISION),
            prob_threshold=CONFIANZA_VISION,
            backend_id=cv2.dnn.DNN_BACKEND_OPENCV,
            target_id=cv2.dnn.DNN_TARGET_CPU,
        )
        self.modelo_vision = modelo
        print("[Visión] NanoDet cargado localmente en la CPU.", file=sys.stderr, flush=True)
        return modelo

    def _inferir_vision(self, imagen):
        modelo = self._cargar_modelo_vision()
        import cv2
        entrada, escala = _preparar_imagen_vision(imagen, cv2)
        inicio = time.monotonic()
        detecciones = _interpretar_detecciones_vision(modelo.infer(entrada), escala)
        return detecciones, (time.monotonic() - inicio) * 1000

    def _analizar_frame(self, imagen):
        try:
            detecciones, ms = self._inferir_vision(imagen)
        except Exception:
            self.error_vision = "No pude completar el análisis visual."
            self.ultimas_detecciones = self.ultima_vision = None
            raise
        self.ultimas_detecciones = detecciones
        self.ultima_vision = time.monotonic()
        self.error_vision = None
        return detecciones, ms

    def estado_vision(self):
        if self.privacidad:
            return "Mi visión está desactivada por privacidad. La cámara está liberada."
        if not self.vision_continua:
            if self.vista_vision and self.ventana is not None and self.cam is not None:
                return "Mi visión está disponible en la ventana; la observación continua en segundo plano está desactivada."
            return "Mi visión continua está desactivada. Puedo analizar bajo demanda."
        if self.cam is None or self.error_vision or self.ultima_vision is None or time.monotonic() - self.ultima_vision > VISION_MAX_ANTIGUEDAD:
            return "La observación continua está solicitada, pero no tengo un análisis reciente válido."
        return "Sí, mi visión está activa. Estoy observando mi entorno, aunque no muestre la ventana."

    def muestra_saludo(self):
        """Metadatos recientes, sin inferencia nueva ni apertura de cámara."""
        ahora = time.monotonic()
        valida = (self.vision_continua and not self.privacidad and self.cam is not None
                  and not self.error_vision and self.ultima_vision is not None
                  and self.ultimas_detecciones is not None
                  and 0 <= ahora - self.ultima_vision <= VISION_MAX_ANTIGUEDAD)
        return {"texto": "Muestra visual para saludo.", "valida": bool(valida),
                "instante": self.ultima_vision if valida else None,
                "persona_central": bool(valida and any(
                    d["clase"] == "person" and d["posicion"] == "centro"
                    and d["confianza"] >= CONFIANZA_PERSONAS
                    for d in self.ultimas_detecciones)),
                "grabando": bool(self.grabacion)}

    def activar_vision_continua(self):
        if (self.vision_continua and self.cam is not None and not self.error_vision
                and self.ultima_vision is not None
                and time.monotonic() - self.ultima_vision <= VISION_MAX_ANTIGUEDAD):
            return self.estado_vision()
        estaba_privada = self.privacidad
        self.privacidad = False
        try:
            self._cargar_modelo_vision()
            self.iniciar()
            self._analizar_frame(self.imagen_vision())
            self.vision_continua = True
            self.proxima_vision = time.monotonic() + 1 / FPS_VISION_FONDO
            print("[Visión] Observación continua activa, sin guardar imágenes.", file=sys.stderr, flush=True)
            return self.estado_vision()
        except Exception:
            self.vision_continua = False
            self.privacidad = estaba_privada
            self.ultimas_detecciones = self.ultima_vision = None
            self.apagar_si_libre()
            raise

    def desactivar_vision_privacidad(self):
        if self.grabacion:
            return "Estoy grabando. Dime que deje de grabar antes de desactivar la visión por privacidad."
        self.vision_continua = False
        self.privacidad = True
        self.mantener_activa = False
        self.ultimas_detecciones = self.ultima_vision = None
        self.error_vision = None
        self.ocultar()
        self.apagar_si_libre()
        return {"texto": "Desactivé mi visión por privacidad y liberé la cámara.", "privacidad": True}

    def revisar_vision_fondo(self):
        if not self.vision_continua or self.privacidad or self.cam is None:
            return
        if self.vista_vision and self.ventana is not None and self.ventana.state() not in ("iconic", "withdrawn"):
            return  # dibujar() analiza la misma imagen que muestra.
        ahora = time.monotonic()
        if ahora < self.proxima_vision:
            return
        self.proxima_vision = ahora + 1 / FPS_VISION_FONDO
        try:
            self._analizar_frame(self.imagen_vision())
        except Exception as e:
            self.error_vision = "No pude completar el análisis visual."
            self.ultimas_detecciones = self.ultima_vision = None
            self.proxima_vision = time.monotonic() + VISION_REINTENTO_SEGUNDOS
            print(f"[Visión] Error de observación: {e}", file=sys.stderr, flush=True)

    def analizar_vision(self, consulta="general", categoria=None):
        # No crea fotografías, archivos temporales ni ventanas nuevas.
        # Si la cámara ya está grabando, utiliza la imagen auxiliar (lores).
        if self.privacidad:
            return "Mi visión está desactivada por privacidad. Dime que active mi visión si quieres que vuelva a observar."
        self._cargar_modelo_vision()
        antes_activa = self.cam is not None
        try:
            if not antes_activa:
                self.iniciar()
            imagen = self.imagen_vision()
            detecciones, ms = self._analizar_frame(imagen)
            print(f"[Visión] {len(detecciones)} detecciones, {ms:.1f} ms de inferencia.", file=sys.stderr, flush=True)
            return _respuesta_vision(detecciones, consulta, categoria)
        finally:
            if not antes_activa:
                # No deja la cámara encendida cuando solo se pidió analizar.
                self.apagar_si_libre()

    def iniciar(self):
        if self.privacidad:
            raise ErrorCamara("La cámara está desactivada por privacidad. Activa mi visión primero.")
        if self.cam is not None:
            return
        from picamera2 import Picamera2
        from libcamera import controls
        cam = Picamera2(INDICE_CAMARA)
        try:
            config = cam.create_video_configuration(
                main={"size": RESOLUCION_VIDEO, "format": "YUV420"},
                lores={"size": RESOLUCION_VISTA, "format": "BGR888"},
                controls={"FrameRate": FPS_VIDEO}, buffer_count=6,
                display=None)
            cam.configure(config)
            if "AfMode" in cam.camera_controls:
                cam.set_controls({"AfMode": controls.AfModeEnum.Continuous})
            cam.start(show_preview=False)
            self.cam = cam
            print("[Cámara] Activa. No se guardan imágenes automáticamente.", file=sys.stderr, flush=True)
        except Exception:
            try:
                cam.close()
            except Exception:
                pass
            raise

    def apagar_si_libre(self):
        if self.cam is not None and not (self.vision_continua or self.mantener_activa or self.ventana or self.grabacion):
            cam, self.cam = self.cam, None
            try:
                cam.stop()
            finally:
                cam.close()
            print("[Cámara] Desactivada.", file=sys.stderr, flush=True)

    def mostrar_vision(self):
        if self.privacidad:
            return "Mi visión está desactivada por privacidad. Activa mi visión primero."
        self._cargar_modelo_vision()
        anterior = self.vista_vision
        self.vista_vision = True
        self.proxima_vista = 0
        try:
            if self.minimizada_por_voz or (self.ventana is not None and self.ventana.state() in ("iconic", "withdrawn")):
                self.restaurar()
            else:
                ya_abierta = self.ventana is not None
                self.mostrar()
                if ya_abierta:
                    self.dibujar()
            if not self.vista_vision or self.ventana is None:
                raise ErrorCamara("No pude iniciar la visualización de detecciones.")
        except Exception:
            self.vista_vision = anterior if self.ventana is not None else False
            raise
        return "Listo, te muestro mi visión en directo con las detecciones."

    def cerrar_vision(self):
        if not self.vista_vision:
            return "No hay una visualización de visión artificial abierta."
        self.ocultar()
        return "Cerré la visualización de visión artificial." + (" Sigo observando en segundo plano." if self.vision_continua else "")

    def _titulo_vista(self):
        if self.ventana is not None:
            self.ventana.title("Charsbotai - Visión artificial" if self.vista_vision else "Charsbotai - Cámara activa")
        if self.estado_vista is not None:
            self.estado_vista.configure(text="VISIÓN ARTIFICIAL · Esc: salir de pantalla completa" if self.vista_vision else "CÁMARA ACTIVA · Esc: salir de pantalla completa")

    def mostrar(self, completa=False, geometria=None):
        if self.ventana is None:
            import tkinter as tk
            # Crear la ventana antes de abrir el dispositivo: si no hay escritorio,
            # no dejamos la cámara activa por una solicitud de vista fallida.
            ventana = tk.Tk()
            try:
                ventana.title("Charsbotai - Cámara activa")
                # La visión arranca a 640x390; limitarla a la pantalla.
                if self.vista_vision and geometria is None:
                    w = min(640, max(1, ventana.winfo_screenwidth() - 40))
                    h = min(390, max(1, ventana.winfo_screenheight() - 90))
                    geometria = f"{w}x{h}"
                ventana.geometry(geometria or "960x570")
                ventana.configure(bg="black")
                etiqueta = tk.Label(ventana, bg="black")
                etiqueta.pack(fill="both", expand=True)
                self.estado_vista = tk.Label(ventana, text="CÁMARA ACTIVA · Esc: salir de pantalla completa", bg="#222", fg="white")
                self.estado_vista.pack(fill="x")
                ventana.protocol("WM_DELETE_WINDOW", self.ocultar_manual)
                ventana.bind("<F11>", lambda e: self.pantalla_completa(not self.fullscreen))
                ventana.bind("<Escape>", lambda e: self.pantalla_completa(False))
                self.iniciar()
                self.ventana, self.etiqueta = ventana, etiqueta
                self._titulo_vista()
                self.minimizada_por_voz = False
                self.geometria_minimizada = None
                self.proxima_vista = 0
                self.dibujar()
                if self.ventana is None:
                    raise ErrorCamara("No pude mostrar la imagen de la cámara.")
            except Exception:
                self.ventana = self.etiqueta = self.estado_vista = None
                try:
                    ventana.destroy()
                except Exception:
                    pass
                self.apagar_si_libre()
                raise
        else:
            self.ventana.deiconify()
            self.ventana.lift()
            self._titulo_vista()
        if completa:
            self.pantalla_completa(True)
        return "Listo, abrí la visualización de la cámara."

    def pantalla_completa(self, estado):
        if self.ventana is None:
            return self.mostrar(estado)
        # Una orden de pantalla completa también debe mostrar una ventana
        # que estaba minimizada. Al salir de pantalla completa no se fuerza
        # la aparición: minimizar utiliza este mismo método antes de iconify.
        if estado and self.ventana.state() in ("iconic", "withdrawn"):
            self.ventana.deiconify()
            self.ventana.lift()
        self.ventana.attributes("-fullscreen", estado)
        self.fullscreen = estado
        return "Listo, cambié el tamaño de la visualización."

    def restaurar(self):
        """Recupera la ventana minimizada y sale de pantalla completa."""
        if self.ventana is None:
            return self.mostrar()
        # En el escritorio del robot, deiconify() y lift() no recuperaron
        # la ventana minimizada. Reconstruir solo la vista usa el mismo
        # camino que ocultar y volver a abrir, ya comprobado en la Raspberry.
        # La marca propia evita depender del estado devuelto por el gestor
        # de ventanas después de una minimización solicitada por voz.
        if self.minimizada_por_voz or self.ventana.state() in ("iconic", "withdrawn"):
            geometria = self.geometria_minimizada
            if geometria is None and not self.fullscreen:
                geometria = self.ventana.geometry()
            mantener = self.mantener_activa
            modo_vision = self.vista_vision
            self.mantener_activa = True  # No cerrar Picamera2 ni su grabación.
            try:
                self.ocultar()
                self.vista_vision = modo_vision
                self.mostrar(geometria=geometria)
            finally:
                self.mantener_activa = mantener
                if self.ventana is None:
                    self.vista_vision = False
                    self.apagar_si_libre()
            return "Listo, restauré la visualización de la cámara."
        # Para una ventana visible, conservar la restauración anterior.
        self.ventana.deiconify()
        self.ventana.attributes("-fullscreen", False)
        self.fullscreen = False
        self.ventana.lift()
        self.ventana.update_idletasks()
        return "Listo, restauré la visualización de la cámara."

    def ocultar_manual(self):
        # Cerrar la ventana no equivale a apagar el dispositivo.
        if self.cam is not None and not self.vista_vision:
            self.mantener_activa = True
        self.ocultar()

    def ocultar(self):
        ventana, self.ventana = self.ventana, None
        self.etiqueta = self.img_tk = self.estado_vista = None
        self.vista_vision = False
        self.fullscreen = False
        self.minimizada_por_voz = False
        self.geometria_minimizada = None
        if ventana is not None:
            ventana.destroy()
        self.apagar_si_libre()
        return "Oculté la visualización." + (" La cámara sigue activa en segundo plano." if self.cam else " La cámara quedó desactivada.")

    def imagen_vision(self):
        """Imagen PIL en RAM compartida por vista y visión, sin escribir a disco."""
        if self.cam is None:
            raise ErrorCamara("La cámara no está activa.")
        return self.cam.capture_image("lores")

    def dibujar(self):
        if self.ventana is None:
            return
        from PIL import ImageTk
        self.ventana.update_idletasks()
        self.ventana.update()
        if self.ventana is None or self.ventana.state() in ("iconic", "withdrawn"):
            return
        if time.monotonic() < self.proxima_vista:
            return
        self.proxima_vista = time.monotonic() + 1 / (FPS_VISION_VISIBLE if self.vista_vision else FPS_VISTA)
        imagen = self.imagen_vision()
        if self.vista_vision:
            try:
                detecciones, ms = self._analizar_frame(imagen)
                imagen = _dibujar_detecciones_vision(imagen, detecciones)
                self.estado_vista.configure(text=f"VISIÓN · {len(detecciones)} detecciones · {ms:.0f} ms · Esc: salir de pantalla completa")
            except Exception as e:
                # No detener una grabación por fallo del detector.
                self.vista_vision = False
                self._titulo_vista()
                self.estado_vista.configure(text="Error de visión · Cámara sin detecciones")
                print(f"[Visión] Error: {e}", file=sys.stderr, flush=True)
        # Mantener relación de aspecto al cambiar el tamaño de ventana.
        ancho = max(1, self.ventana.winfo_width())
        alto = max(1, self.ventana.winfo_height() - 28)
        escala = min(ancho / imagen.width, alto / imagen.height)
        imagen = imagen.resize((max(1, int(imagen.width * escala)), max(1, int(imagen.height * escala))))
        self.img_tk = ImageTk.PhotoImage(imagen, master=self.ventana)
        self.etiqueta.configure(image=self.img_tk)

    def foto(self, externo):
        with Destino("foto", externo) as destino:
            from libcamera import controls
            nombre = _nombre("Foto", "jpg")
            temporal = nombre + ".parcial.jpg"
            try:
                self.iniciar()
                if self.grabacion:
                    # No cambiar modo ni interrumpir el codificador en marcha.
                    # Main es YUV420: Picamera2 guarda JPEG nativo de main.
                    self.cam.capture_file(destino.anclada(temporal), name="main", format="jpeg")
                    aviso = " a resolución de video, sin detener la grabación"
                else:
                    config = self.cam.create_still_configuration(main={"size": RESOLUCION_FOTO, "format": "BGR888"}, display=None)
                    if "AfMode" in self.cam.camera_controls:
                        config["controls"]["AfMode"] = controls.AfModeEnum.Continuous
                    self.cam.switch_mode_and_capture_file(config, destino.anclada(temporal), delay=5)
                    aviso = ""
                destino.comprobar()
                from PIL import Image
                with Image.open(destino.anclada(temporal)) as img:
                    img.verify()
                with open(destino.anclada(temporal), "rb") as archivo:
                    os.fsync(archivo.fileno())
                destino.comprobar()
                os.rename(temporal, nombre, src_dir_fd=destino.fd, dst_dir_fd=destino.fd)
                self.ultima_foto = (destino.ruta / nombre, externo)
                self.identidades[self.ultima_foto[0]] = _identidad(os.stat(nombre, dir_fd=destino.fd, follow_symlinks=False))
                print(f"[Cámara] Foto: {self.ultima_foto[0]}", file=sys.stderr, flush=True)
                return f"Listo, guardé la fotografía{aviso} en el almacenamiento {'externo' if externo else 'local'}."
            finally:
                try:
                    os.unlink(temporal, dir_fd=destino.fd)
                except OSError:
                    pass
                self.apagar_si_libre()

    def iniciar_video(self, externo):
        if self.grabacion:
            return "Ya estoy grabando un video. Puedes pedirme que deje de grabar."
        destino = Destino("video", externo)
        nombre = _nombre("Video", "mp4")
        temporal = nombre + ".parcial.mp4"
        encoder = None
        try:
            if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
                raise ErrorCamara("No encontré FFmpeg y FFprobe. Son necesarios para la grabación MP4.")
            from picamera2.encoders import H264Encoder
            from picamera2.outputs import FfmpegOutput
            self.iniciar()
            # El nombre anclado no contiene espacios y no depende de la ruta del
            # montaje. FFmpeg escribe directamente en el almacenamiento elegido.
            salida = FfmpegOutput(destino.anclada(temporal), audio=False)
            encoder = H264Encoder(bitrate=BITRATE_VIDEO, framerate=FPS_VIDEO)
            # En Pi 5 se codifica por software: limitar hilos para dejar CPU a la voz.
            if hasattr(encoder, "threads"):
                encoder.threads = 2
            self.cam.start_encoder(encoder, salida, name="main")
            time.sleep(0.35)
            if getattr(salida, "output_broken", False) or (salida.ffmpeg is not None and salida.ffmpeg.poll() is not None):
                raise ErrorCamara("No pude iniciar la escritura del video.")
            self.grabacion = (destino, nombre, temporal, encoder, salida)
            self.ultimo_error_grabacion = None
            self.proxima_revision = 0
            return f"Listo, inicié la grabación en el almacenamiento {'externo' if externo else 'local'}."
        except Exception:
            if encoder is not None and getattr(encoder, "running", False):
                try:
                    self.cam.stop_encoder(encoder)
                except Exception:
                    pass
            destino.cerrar()
            self.apagar_si_libre()
            raise

    def detener_video(self, error=None):
        if not self.grabacion:
            if self.ultimo_error_grabacion:
                aviso, self.ultimo_error_grabacion = self.ultimo_error_grabacion, None
                return aviso
            return "No hay una grabación en curso."
        destino, nombre, temporal, encoder, salida = self.grabacion
        self.grabacion = None
        try:
            self.cam.stop_encoder(encoder)
            if error:
                raise ErrorCamara(error)
            if getattr(salida, "output_broken", False):
                raise ErrorCamara("Falló la escritura del video.")
            destino.comprobar()
            resultado = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name,duration", "-of", "json", destino.anclada(temporal)], capture_output=True, text=True, timeout=10, check=True)
            streams = json.loads(resultado.stdout).get("streams", [])
            if not streams or float(streams[0].get("duration", 0)) <= 0:
                raise ErrorCamara("El archivo no contiene un video válido.")
            # No se presenta un archivo parcial como un video terminado.
            with open(destino.anclada(temporal), "rb") as archivo:
                os.fsync(archivo.fileno())
            destino.comprobar()
            os.rename(temporal, nombre, src_dir_fd=destino.fd, dst_dir_fd=destino.fd)
            self.ultimo_video = (destino.ruta / nombre, destino.externo)
            self.identidades[self.ultimo_video[0]] = _identidad(os.stat(nombre, dir_fd=destino.fd, follow_symlinks=False))
            print(f"[Cámara] Video: {self.ultimo_video[0]}", file=sys.stderr, flush=True)
            return f"Listo, terminé la grabación y guardé el video en el almacenamiento {'externo' if destino.externo else 'local'}."
        except Exception as e:
            mensaje = f"La grabación terminó con un problema: {e}. No puedo confirmar que el video se guardó correctamente."
            self.ultimo_error_grabacion = mensaje
            print(f"[Cámara] {mensaje} Archivo parcial: {destino.ruta / temporal}", file=sys.stderr, flush=True)
            return mensaje
        finally:
            destino.cerrar()
            self.apagar_si_libre()

    def revisar_grabacion(self):
        if not self.grabacion or time.monotonic() < self.proxima_revision:
            return
        self.proxima_revision = time.monotonic() + 2
        destino, _, _, _, salida = self.grabacion
        try:
            destino.comprobar()
            if destino.libre() < RESERVA_VIDEO:
                raise ErrorCamara("Queda poco espacio libre.")
            if getattr(salida, "output_broken", False) or (salida.ffmpeg is not None and salida.ffmpeg.poll() is not None):
                raise ErrorCamara("Falló el proceso de escritura del video.")
        except Exception as e:
            self.detener_video(str(e))

    def _seleccionar_archivo(self, tipo, mostrado=False):
        visor = self.visores.get(tipo)
        if mostrado and visor is not None and visor.ventana is not None:
            return visor.ruta, visor.externo, visor.identidad
        ultimo = self.ultima_foto if tipo == "foto" else self.ultimo_video
        if ultimo is None:
            raise ErrorCamara("Todavía no tengo registrado un archivo de ese tipo en esta sesión.")
        ruta, externo = ultimo
        identidad = self.identidades.get(ruta)
        if identidad is None:
            raise ErrorCamara("No pude verificar la identidad del archivo guardado.")
        return ruta, externo, identidad

    def abrir_archivo(self, tipo):
        ruta, externo, identidad = self._seleccionar_archivo(tipo)
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            raise ErrorCamara("No tengo acceso a una sesión gráfica para mostrar el archivo.")
        # El visor anterior del mismo tipo se cierra solo si el nuevo abre bien.
        with _archivo_registrado(tipo, ruta, externo, identidad) as (_, fd):
            nuevo = VisorArchivo(tipo, ruta, externo, identidad, fd)
        anterior = self.visores.pop(tipo, None)
        if anterior is not None:
            anterior.cerrar()
        self.visores[tipo] = nuevo
        return "Listo, mostré la fotografía." if tipo == "foto" else "Listo, estoy reproduciendo el video."

    def controlar_visor(self, tipo, accion):
        visor = self.visores.get(tipo)
        objeto = "la fotografía" if tipo == "foto" else "el video"
        if visor is None or visor.ventana is None:
            return f"No tengo {objeto} abierto." if tipo == "video" else "No tengo la fotografía abierta."
        if accion == "cerrar":
            visor.cerrar()
            self.visores.pop(tipo, None)
            return f"Cerré {objeto}. El archivo sigue guardado."
        if accion == "minimizar":
            visor.minimizar()
            return f"Minimicé la ventana de {'la fotografía' if tipo == 'foto' else 'reproducción del video'}."
        visor.restaurar()
        return f"Restauré la ventana de {'la fotografía' if tipo == 'foto' else 'reproducción del video'}."

    def actualizar_visores(self):
        for tipo, visor in tuple(self.visores.items()):
            try:
                if visor.ventana is not None:
                    visor.dibujar()
            except Exception as e:
                print(f"[Visor] Error de {tipo}: {e}", file=sys.stderr, flush=True)
                visor.cerrar()
            if visor.ventana is None:
                self.visores.pop(tipo, None)

    def preparar_borrado(self, tipo):
        self.borrado_pendiente = None
        if tipo == "video" and self.grabacion:
            return {"texto": "Estoy grabando un video. Detén la grabación antes de solicitar una eliminación."}
        ruta, externo, identidad = self._seleccionar_archivo(tipo, mostrado=True)
        with _archivo_registrado(tipo, ruta, externo, identidad):
            pass
        token = uuid.uuid4().hex
        self.borrado_pendiente = (token, tipo, ruta, externo, identidad, time.monotonic() + 75)
        objeto = "la fotografía" if tipo == "foto" else "el video"
        print(f"[Cámara] Pendiente de confirmar eliminación: {ruta}", file=sys.stderr, flush=True)
        visor = self.visores.get(tipo)
        referencia = "que tienes abierto" if tipo == "video" else "que tienes abierta"
        if visor is None or visor.ventana is None:
            referencia = "que guardé por última vez"
        return {"texto": f"Voy a eliminar {objeto} {referencia} del almacenamiento {'externo' if externo else 'local'}. ¿Estás seguro de que quieres eliminarlo permanentemente?" if tipo == "video" else f"Voy a eliminar {objeto} {referencia} del almacenamiento {'externo' if externo else 'local'}. ¿Estás seguro de que quieres eliminarla permanentemente?", "token_borrado": token}

    def confirmar_borrado(self, token):
        pendiente, self.borrado_pendiente = self.borrado_pendiente, None
        if not pendiente or token != pendiente[0] or time.monotonic() >= pendiente[-1]:
            return "La solicitud de eliminación ya no está vigente. No eliminé ningún archivo."
        _, tipo, ruta, externo, identidad, _ = pendiente
        if tipo == "video" and self.grabacion:
            return "Estoy grabando. No eliminé ningún video."
        with _archivo_registrado(tipo, ruta, externo, identidad) as (carpeta, fd):
            # Confirmar el mismo archivo, sin seguir enlaces, justo antes de borrar.
            if _identidad(os.stat(ruta.name, dir_fd=carpeta, follow_symlinks=False)) != identidad:
                raise ErrorCamara("El archivo cambió. Cancelé la eliminación.")
            if externo:
                _, dispositivo = _montaje_externo()
                if os.fstat(carpeta).st_dev != dispositivo or ruta.parent.stat().st_ino != os.fstat(carpeta).st_ino:
                    raise DiscoNoDisponible("El almacenamiento externo cambió. Cancelé la eliminación.")
            os.unlink(ruta.name, dir_fd=carpeta)
        self.identidades.pop(ruta, None)
        atributo = "ultima_foto" if tipo == "foto" else "ultimo_video"
        if getattr(self, atributo) == (ruta, externo):
            setattr(self, atributo, None)
        visor = self.visores.get(tipo)
        if visor is not None and visor.ruta == ruta:
            self.visores.pop(tipo, None)
            try:
                visor.cerrar()
            except Exception as e:
                print(f"[Visor] Error al cerrar después de eliminar: {e}", file=sys.stderr, flush=True)
        print(f"[Cámara] Eliminado por confirmación: {ruta}", file=sys.stderr, flush=True)
        return "Eliminé la fotografía." if tipo == "foto" else "Eliminé el video."

    def ejecutar(self, accion, externo=False, token=None):
        if accion == "confirmar_borrado":
            return self.confirmar_borrado(token)
        if accion in ("borrar_foto", "borrar_video"):
            return self.preparar_borrado(accion.split("_", 1)[1])
        if accion in {f"{a}_{t}" for a in ("minimizar", "restaurar", "cerrar") for t in ("foto", "video")}:
            operacion, tipo = accion.split("_", 1)
            return self.controlar_visor(tipo, operacion)
        if accion == "vision_muestra_saludo":
            return self.muestra_saludo()
        if accion == "vision_estado":
            return self.estado_vision()
        if accion == "vision_activar":
            texto = self.activar_vision_continua()
            return {"texto": texto, "privacidad": False}
        if accion == "vision_privacidad":
            return self.desactivar_vision_privacidad()
        if self.privacidad and (accion in {"mostrar", "pantalla_completa", "ventana_normal", "activar", "foto", "video", "vision_mostrar"}):
            return "La cámara está desactivada por privacidad. Activa mi visión primero."
        if accion == "vision_mostrar":
            return self.mostrar_vision()
        if accion == "vision_cerrar":
            return self.cerrar_vision()
        if accion in ("vision_minimizar", "vision_restaurar", "vision_completa"):
            if not self.vista_vision or self.ventana is None:
                return "No hay una visualización de visión artificial abierta."
            return self.ejecutar({"vision_minimizar": "minimizar", "vision_restaurar": "ventana_normal", "vision_completa": "pantalla_completa"}[accion])
        if accion.startswith("vision_"):
            if accion == "vision_personas":
                return self.analizar_vision("personas")
            if accion == "vision_posicion_persona":
                return self.analizar_vision("posicion_persona")
            if accion.startswith("vision_objeto_"):
                return self.analizar_vision("objeto", accion.removeprefix("vision_objeto_"))
            if accion == "vision_general":
                return self.analizar_vision()
        if accion == "mostrar":
            self.vista_vision = False
            self.proxima_vista = 0
            if self.minimizada_por_voz:
                return self.restaurar()
            return self.mostrar()
        if accion == "pantalla_completa":
            return self.pantalla_completa(True)
        if accion == "ventana_normal":
            return self.restaurar()
        if accion == "minimizar":
            if self.ventana is None:
                return "No hay una visualización abierta para minimizar."
            # Guardar las dimensiones de la ventana normal antes de ocultarla.
            if not self.minimizada_por_voz:
                self.geometria_minimizada = None if self.fullscreen else self.ventana.geometry()
            self.pantalla_completa(False)
            self.ventana.iconify()
            self.minimizada_por_voz = True
            return "Minimicé la visualización de la cámara."
        if accion == "ocultar":
            # La orden explícita de ocultar conserva la cámara activa si ya lo está.
            if self.cam is not None:
                self.mantener_activa = True
            return self.ocultar()
        if accion == "activar":
            self.iniciar()
            self.mantener_activa = True
            return "La cámara está activa en segundo plano."
        if accion == "cerrar_camara":
            return self.ocultar()
        if accion == "desactivar":
            return self.desactivar_vision_privacidad()
        if accion == "foto":
            return self.foto(externo)
        if accion == "video":
            return self.iniciar_video(externo)
        if accion == "detener_video":
            return self.detener_video()
        if accion in ("ver_foto", "ver_video"):
            return self.abrir_archivo(accion[4:])
        if accion == "estado":
            base = "La cámara está activa." if self.cam else "La cámara está desactivada."
            if self.grabacion:
                base += " Estoy grabando un video."
            if self.ultimo_error_grabacion:
                base += " " + self.ultimo_error_grabacion
            return base
        raise ErrorCamara("Acción de cámara desconocida.")

    def cerrar(self):
        self.borrado_pendiente = None
        for visor in tuple(self.visores.values()):
            try:
                visor.cerrar()
            except Exception as e:
                print(f"[Visor] {e}", file=sys.stderr, flush=True)
        self.visores.clear()
        self.vision_continua = False
        self.ultimas_detecciones = self.ultima_vision = None
        self.mantener_activa = False
        try:
            if self.grabacion:
                self.detener_video()
        finally:
            self.ocultar()
            self.apagar_si_libre()


def _servidor_camara():
    # El protocolo tiene un descriptor separado de la salida de diagnósticos de
    # libcamera, FFmpeg y las bibliotecas nativas.
    canal = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    solicitudes = queue.Queue()
    def leer():
        try:
            for linea in sys.stdin:
                solicitudes.put(linea)
        finally:
            solicitudes.put(None)
    threading.Thread(target=leer, daemon=True).start()
    admin = AdministradorCamara()
    try:
        while True:
            try:
                linea = solicitudes.get(timeout=0.025 if admin.ventana or admin.visores else 0.15)
            except queue.Empty:
                linea = ""
            if linea is None:
                break
            if linea:
                peticion = json.loads(linea)
                if peticion["accion"] == "salir":
                    admin.cerrar()
                    canal.write(json.dumps({"texto": "Cámara cerrada."}) + "\n")
                    break
                try:
                    respuesta = admin.ejecutar(peticion["accion"], peticion.get("externo", False), peticion.get("token"))
                    resultado = respuesta if isinstance(respuesta, dict) else {"texto": respuesta}
                except DiscoNoDisponible as e:
                    resultado = {"texto": str(e), "disco_ausente": True}
                except Exception as e:
                    print(f"[Cámara] {type(e).__name__}: {e}", file=sys.stderr, flush=True)
                    resultado = {"texto": f"No pude completar la operación de cámara. {e}"}
                canal.write(json.dumps(resultado, ensure_ascii=False) + "\n")
            try:
                admin.revisar_grabacion()
                admin.dibujar()
                admin.revisar_vision_fondo()
            except Exception as e:
                print(f"[Cámara] Error de visualización: {e}", file=sys.stderr, flush=True)
                try:
                    admin.ocultar()
                except Exception:
                    pass
            admin.actualizar_visores()
    finally:
        try:
            admin.cerrar()
        finally:
            canal.close()


# ================================================================
# INTEGRACIÓN CON CHARSBOTAITEXTO: no importa Picamera2 en el venv.
# ================================================================
_proceso = None
_privacidad_solicitada = False  # Se conserva si se reinicia el auxiliar durante esta sesión.
_pendiente = None
_pregunta_pendiente = None
_vencimiento = 0


def _terminar_proceso():
    global _proceso
    p, _proceso = _proceso, None
    if p is None:
        return
    if p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
    for canal in (p.stdin, p.stdout):
        if canal:
            canal.close()


def _rpc(accion, externo=False, timeout=25, token=None):
    global _proceso, _privacidad_solicitada
    try:
        if _proceso is None or _proceso.poll() is not None:
            _terminar_proceso()
            if accion in {"vision_privacidad", "desactivar"}:
                _privacidad_solicitada = True
                return {"texto": "Mi visión está desactivada por privacidad. La cámara no está abierta."}
            if accion == "vision_muestra_saludo":
                return {"texto": "No hay observación disponible.", "valida": False}
            if accion == "vision_estado":
                return {"texto": "Mi visión está desactivada por privacidad." if _privacidad_solicitada else "Mi visión no está activa en este momento."}
            if accion in {"salir", "cerrar_camara", "ocultar", "detener_video", "estado", "minimizar"}:
                return {"texto": "No hay una grabación en curso." if accion == "detener_video" else "La cámara está desactivada."}
            if accion in {"vision_cerrar", "vision_minimizar", "vision_restaurar", "vision_completa"}:
                return {"texto": "No hay una visualización de visión artificial abierta."}
            if accion == "confirmar_borrado":
                return {"texto": "La solicitud de eliminación ya no está vigente."}
            if accion in {f"{a}_{t}" for a in ("minimizar", "restaurar", "cerrar") for t in ("foto", "video")}:
                return {"texto": "No hay una ventana de ese tipo abierta."}
            entorno = os.environ.copy()
            # No heredar paquetes del venv a libcamera/Picamera2 del sistema.
            for clave in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
                entorno.pop(clave, None)
            argumentos = [PYTHON_CAMARA, str(Path(__file__).resolve()), "--servidor-camara"]
            if _privacidad_solicitada:
                argumentos.append("--privacidad")
            _proceso = subprocess.Popen(argumentos,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None,
                bufsize=0, start_new_session=True, env=entorno)
        _proceso.stdin.write((json.dumps({"accion": accion, "externo": externo, "token": token}) + "\n").encode())
        if not select.select([_proceso.stdout], [], [], timeout)[0]:
            raise TimeoutError("La cámara no respondió a tiempo.")
        linea = _proceso.stdout.readline()
        if not linea:
            raise ErrorCamara("El proceso de cámara se cerró inesperadamente.")
        resultado = json.loads(linea)
        if "privacidad" in resultado:
            _privacidad_solicitada = bool(resultado["privacidad"])
        return resultado
    except Exception as e:
        _terminar_proceso()
        return {"texto": f"No pude comunicarme con la cámara. {e}"}


# ================================================================
# SALUDO SOCIAL: sin historial, sin modelos nuevos, sin órdenes de motor.
# El proveedor de distancia se conectará solamente al integrar Arduino.
# ================================================================
SALUDOS_AL_INICIAR = True
SALUDO_DISTANCIA_MINIMA = 2.0  # Mínimo nominal HC-SR04; cero no es un eco válido.
SALUDO_DISTANCIA_MAXIMA = 30.0
SALUDO_DISTANCIA_SALIDA = 50.0
SALUDO_PRESENCIA_S = 3.0
SALUDO_AUSENCIA_S = 10.0
SALUDO_INTERVALO_S = 300.0
SALUDO_EDAD_MAX_S = 2.0
SALUDO_GAP_MAX_S = 2.0


def consultar_muestra_saludo():
    return _rpc("vision_muestra_saludo", timeout=2)


class ControlSaludos:
    """Estado acotado de una visita; tiempos monotónicos, sin identidad.

    distancia: dict con cm, instante (monotonic LOCAL al recibir telemetría
    fresca), valida=True y detenido=True, comprobados por integración real.
    No se acepta un valor estimado por imagen, eco ausente o dato simulado
    en producción. El controlador no tiene ninguna vía de accionar motores.
    """
    def __init__(self):
        self.habilitado = SALUDOS_AL_INICIAR
        self.ultimo_saludo = None
        self.visita_consumida = False
        self.reiniciar_muestras()

    def reiniciar_muestras(self):
        self.presencia_desde = self.ausencia_desde = None
        self.ultimo_par = self.ultimo_muestreo = None

    def configurar(self, habilitado):
        self.habilitado = bool(habilitado)
        self.reiniciar_muestras()  # No borra visita ni enfriamiento.

    @staticmethod
    def _numero(v):
        return type(v) in (int, float) and math.isfinite(v)

    def evaluar(self, visual, distancia, ahora, permitido):
        if not self.habilitado or not permitido or not isinstance(visual, dict) or not isinstance(distancia, dict):
            self.reiniciar_muestras()
            return False
        tv, td, cm = visual.get("instante"), distancia.get("instante"), distancia.get("cm")
        if (visual.get("valida") is not True or visual.get("grabando") is not False
                or distancia.get("valida") is not True or distancia.get("detenido") is not True
                or not all(self._numero(x) for x in (tv, td, cm, ahora))
                or not (0 <= ahora-tv <= SALUDO_EDAD_MAX_S and 0 <= ahora-td <= SALUDO_EDAD_MAX_S)
                or abs(tv-td) > SALUDO_EDAD_MAX_S or not SALUDO_DISTANCIA_MINIMA <= cm <= 400):
            self.reiniciar_muestras()
            return False
        if self.ultimo_muestreo is not None and ahora-self.ultimo_muestreo > SALUDO_GAP_MAX_S:
            self.reiniciar_muestras()
        if self.ultimo_par is not None and (tv <= self.ultimo_par[0] or td <= self.ultimo_par[1]):
            return False  # No contar repetidamente la misma muestra.
        self.ultimo_par = (tv, td)
        self.ultimo_muestreo = ahora
        if cm > SALUDO_DISTANCIA_SALIDA:
            self.presencia_desde = None
            if self.ausencia_desde is None:
                self.ausencia_desde = ahora
            if ahora-self.ausencia_desde >= SALUDO_AUSENCIA_S:
                self.visita_consumida = False
            return False
        self.ausencia_desde = None
        if cm > SALUDO_DISTANCIA_MAXIMA or visual.get("persona_central") is not True:
            self.presencia_desde = None
            return False
        if self.visita_consumida:
            return False
        if self.presencia_desde is None:
            self.presencia_desde = ahora
        if ahora-self.presencia_desde < SALUDO_PRESENCIA_S:
            return False
        # Una visita iniciada dentro del enfriamiento no genera un saludo tardío.
        self.visita_consumida = True
        if self.ultimo_saludo is not None and ahora-self.ultimo_saludo < SALUDO_INTERVALO_S:
            return False
        self.ultimo_saludo = ahora  # Reserva antes de hablar, incluso si se interrumpe.
        return True

    def estado(self, proveedor_disponible=False):
        if not self.habilitado:
            return "Mis saludos automáticos están deshabilitados."
        if not proveedor_disponible:
            return "Mis saludos automáticos están habilitados en configuración, pero bloqueados hasta integrar la medición frontal del Arduino y confirmar que estoy detenido."
        return "Mis saludos automáticos están habilitados, sujetos a proximidad válida, presencia estable y demás restricciones."

    def responder(self, texto, proveedor_disponible=False):
        t = _normalizar(texto)
        if re.fullmatch(r"(?:activa|habilita|enciende) (?:tus |los )?saludos automaticos", t):
            self.configurar(True)
            return self.estado(proveedor_disponible)
        if re.fullmatch(r"(?:desactiva|deshabilita|apaga) (?:tus |los )?saludos automaticos", t):
            self.configurar(False)
            return self.estado(proveedor_disponible)
        if re.fullmatch(r"(?:tienes (?:activos|activados) (?:tus |los )?saludos automaticos|(?:tus |los )?saludos automaticos (?:estan activos|estan activados)|(?:dime )?(?:el )?estado de (?:tus |los )?saludos automaticos)", t):
            return self.estado(proveedor_disponible)
        return None


def consultar_estado_vision():
    """Consulta real, sin abrir la cámara ni cambiar la privacidad."""
    return _rpc("vision_estado")["texto"]


def iniciar_vision_al_arrancar():
    """Únicamente desde main, no desde import ni modo de prueba de voz."""
    if not VISION_AL_INICIAR:
        return "Inicio automático de visión deshabilitado en la configuración."
    return _rpc("vision_activar")["texto"]


def cerrar_camara():
    cancelar_dialogo_camara()
    if _proceso is not None:
        try:
            _rpc("salir", timeout=15)
            if _proceso is not None:
                _proceso.wait(timeout=3)
        except Exception:
            pass
        finally:
            _terminar_proceso()


def cancelar_dialogo_camara():
    global _pendiente, _pregunta_pendiente, _vencimiento
    _pendiente = _pregunta_pendiente = None
    _vencimiento = 0


def actualizar_dialogo_camara(respuesta, voz_completa, segundos=18):
    """El plazo empieza al terminar la voz; no se cambia el ASR ni el VAD."""
    global _vencimiento
    if _pendiente:
        if voz_completa and respuesta == _pregunta_pendiente:
            _vencimiento = time.monotonic() + segundos
        else:
            cancelar_dialogo_camara()


def _normalizar(texto):
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    t = " ".join(t.split())
    t = re.sub(r"^(?:oye robot|robot|charsbotai|asistente)\s+", "", t)
    t = re.sub(r"^por favor\s+|\s+por favor$", "", t)
    return t


_LOCAL = r"(?:local(?:mente)?|(?:el )?almacenamiento local|(?:la )?memoria(?: local| de la raspberry(?: pi)?)?|(?:la )?raspberry(?: pi)?|(?:la )?micro ?sd)"
_EXTERNO = r"(?:(?:el )?(?:disco(?: duro)?|almacenamiento) externo|(?:el )?disco(?: duro)? (?:usb|de (?:un|1) (?:tera(?:byte)?|tb))|(?:el )?usb|externo|(?:el )?disco)"
_DESTINO = re.compile(r"(?:en |al |a )?(?:el |la )?(?P<dest>" + _LOCAL + "|" + _EXTERNO + r")$")


def _destino(texto):
    texto = re.sub(r"^(?:guardala|guardalo|guarda(?: la foto(?:grafia)?| el video)?|quiero que lo guardes|quiero que la guardes)\s+", "", texto)
    m = _DESTINO.fullmatch(texto)
    if not m:
        return None
    return bool(re.fullmatch(_EXTERNO, m.group("dest")))


def _separar_destino(t):
    # Solo complementos de destino al final de una orden completa de captura.
    patron = r"\s+(?:(?:y )?(?:guarda(?:la|lo)?|guardala|guardalo|guardar)(?: (?:la foto(?:grafia)?|el video))?\s+)?(?:en |al |a )?(?:el |la )?(?P<dest>" + _LOCAL + "|" + _EXTERNO + r")$"
    m = re.search(patron, t)
    if m:
        return t[:m.start()].strip(), bool(re.fullmatch(_EXTERNO, m.group("dest")))
    return t, None


def detectar_orden_camara(texto):
    """Patrones completos: no intercepta la domótica o consultas generales."""
    t = _normalizar(texto)
    base, destino = _separar_destino(t)
    if re.fullmatch(r"(?:toma(?:me)?|saca(?:me)?|captura|haz(?:me)?|quiero que tomes) (?:una |la )?foto(?:grafia)?(?: en maxima resolucion)?", base):
        return "foto", destino
    if re.fullmatch(r"(?:graba(?:me)? (?:un |el )?video|(?:empieza|comienza|inicia) (?:a )?grabar(?: (?:un |el )?video)?|inicia (?:la )?grabacion)", base):
        return "video", destino
    # Consultas visuales bajo demanda. Se capturan imágenes únicamente en RAM.
    # No ampliar innecesariamente las coincidencias de cámara: órdenes completas.
    if re.fullmatch(r"(?:que ves|que estas viendo|que objetos ves|dime que ves|describe lo que ves|que tienes enfrente)", t):
        return "vision_general", None
    if re.fullmatch(r"(?:cuantas personas ves|cuantas personas hay (?:frente a ti|enfrente de ti)|hay alguien frente a ti|ves alguna persona|ves personas|hay una persona frente a ti)", t):
        return "vision_personas", None
    if re.fullmatch(r"(?:donde (?:esta|ves) (?:a la |la |una )?persona|en que parte ves (?:a la |la |una )?persona)", t):
        return "vision_posicion_persona", None
    # Pregunta por categoría conocida; reconocer sinónimos cotidianos.
    clase_preguntada = {
        "persona": "person", "personas": "person", "botella": "bottle", "botellas": "bottle",
        "celular": "cell phone", "telefono": "cell phone", "telefono celular": "cell phone",
        "mouse": "mouse", "raton": "mouse", "raton de computadora": "mouse",
        "laptop": "laptop", "computadora portatil": "laptop", "silla": "chair",
        "taza": "cup", "vaso": "cup", "teclado": "keyboard", "mesa": "dining table",
        "perro": "dog", "gato": "cat", "libro": "book", "television": "tv",
    }
    m = re.fullmatch(r"(?:ves|puedes ver|detectas|hay) (?:alguna |algun |una |un |la |el )?(?P<objeto>[a-z ]+?)(?: frente a ti| enfrente de ti)?", t)
    if m:
        objeto = m.group("objeto").strip()
        if objeto in clase_preguntada:
            return "vision_objeto_" + clase_preguntada[objeto], None
        if objeto in {"ventilador", "abanico"}:
            return "vision_objeto_no_soportado", None
    # Visores de archivos: órdenes completas y separadas de la cámara y la grabación.
    for tipo, objeto in (("foto", r"(?:la |esta |esa |mi )?(?:ultima )?foto(?:grafia)?(?: que tomaste)?"),
                         ("video", r"(?:el |este |ese |mi )?(?:ultimo )?video(?: que grabaste)?")):
        for accion, verbo in (("minimizar", r"minimiza|minimice"),
                              ("restaurar", r"restaura|restaure|restauro"),
                              ("cerrar", r"cierra|cierre|cierrame|quita|quite|quitame"),
                              ("borrar", r"elimina|elimine|borra|borre|suprime")):
            consulta = re.sub(r"\bventana del video\b", "ventana de el video", t)
            if re.fullmatch(r"(?:" + verbo + r") (?:la ventana de )?" + objeto, consulta):
                return accion + "_" + tipo, None
    patrones = {
        # Solo órdenes completas de vista previa: no se altera la
        # normalización global ni se reinicia una cámara que ya está activa.
        "mostrar": (r"(?:vuelve a (?:abrir|mostrar(?:me)?|ensenar(?:me)?|poner)|"
                    r"(?:abre(?:me)?|muestra(?:me)?|ensena(?:me)?|pon(?:me)?)(?: (?:otra vez|de nuevo|nuevamente))?|"
                    r"quiero que (?:abras|muestres|pongas)(?: (?:otra vez|de nuevo|nuevamente))?) (?:la |tu |las )?camaras?"
                    r"(?: (?:otra vez|de nuevo|nuevamente))?|"
                    r"(?:abre|muestra) (?:la )?(?:vista previa|visualizacion)(?: de la camara)?|"
                    r"muestra(?:me)? lo que ves"),
        "activar": r"(?:activa|enciende) (?:la |tu )?camara",
        "ocultar": r"oculta (?:la |tu )?(?:camara|visualizacion)|vuelve a tu cara",
        "minimizar": r"(?:minimiza|minimice) (?:la |tu |las )?(?:camaras?|visualizacion)",
        "cerrar_camara": r"(?:cierra|cierre|quita) (?:la |tu )?camara",
        "desactivar": r"(?:desactiva|apaga) (?:la |tu )?camara",
        "pantalla_completa": r"(?:pon|muestra) (?:la |tu |las )?camaras? en pantalla completa",
        "ventana_normal": r"(?:(?:restaura|restaure|restauro) (?:la |tu |las )?camaras?|sal de pantalla completa de la camara)",
        "detener_video": r"(?:deja de grabar|para de grabar|deten(?: la)? grabacion|termina(?: la)? grabacion|finaliza(?: la)? grabacion|deten el video)",
        "ver_foto": r"(?:muestra|ensena|abre)(?:me)? (?:la |tu |mi )?(?:ultima )?foto(?:grafia)?(?: que tomaste| que capturaste)?",
        "ver_video": r"(?:muestra|ensena|abre|reproduce)(?:me)? (?:el |tu |mi )?(?:ultimo )?video(?: que grabaste| que acabas de grabar)?",
        "estado": r"(?:esta (?:activa|encendida) (?:la |tu )?camara|estas grabando|estado de (?:la |tu )?camara)",
        # Observación independiente de la ventana de detecciones.
        "vision_estado": r"(?:tienes (?:activada|activoada|activa|encendida) (?:tu |la )?vision|(?:tu |la )?vision esta (?:activa|activada|encendida)|esta (?:activa|activada|encendida) (?:tu |la )?vision|(?:(?:dime|dame|indica(?:me)?|muestra(?:me)?|cual es|quiero saber) (?:el )?)?(?:el )?estado de (?:tu |la )?vision|estas observando)(?: artificial)?",
        "vision_activar": r"(?:activa|ativa|enciende|reactiva) (?:tu |la )?vision(?: artificial)?|(?:sal|salir) del modo privacidad",
        "vision_privacidad": r"(?:desactiva|apaga) (?:tu |la )?vision(?: artificial)?(?: por privacidad)?|(?:activa|entra en) (?:el )?modo (?:de )?privacidad",
        "vision_mostrar": r"(?:(?:muestra(?:me)?|ensena(?:me)?|abre(?:me)?|vuelve a (?:mostrar(?:me)?|abrir)) (?:tu |la )?vision(?: artificial| en directo)?|(?:muestra(?:me)?|ensena(?:me)?) lo que detectas)",
        "vision_cerrar": r"(?:cierra|cierre|quita|oculta) (?:tu |la )?vision(?: artificial)?",
        "vision_minimizar": r"(?:minimiza|minimice) (?:tu |la )?vision(?: artificial)?",
        "vision_restaurar": r"(?:restaura|restaure|restauro) (?:tu |la )?vision(?: artificial)?",
        "vision_completa": r"(?:pon|muestra) (?:tu |la )?vision(?: artificial)? en pantalla completa",
    }
    for accion, patron in patrones.items():
        if re.fullmatch(patron, t):
            return accion, None
    return None


def _preguntar(accion, alternativa=False, motivo=""):
    global _pendiente, _pregunta_pendiente, _vencimiento
    objeto = "la fotografía" if accion == "foto" else "el video"
    _pendiente = (accion, alternativa)
    if alternativa:
        _pregunta_pendiente = f"{motivo} ¿Quieres que guarde {objeto} en el almacenamiento local?"
    else:
        _pregunta_pendiente = f"¿Dónde quieres guardar {objeto}, en el almacenamiento local o en el externo?"
    _vencimiento = time.monotonic() + 45  # se sustituye al terminar de hablar
    return _pregunta_pendiente


def _realizar(accion, externo=False):
    global _pendiente, _pregunta_pendiente, _vencimiento
    r = _rpc(accion, externo)
    if r.get("token_borrado") and accion in {"borrar_foto", "borrar_video"}:
        _pendiente = ("confirmar_borrado", r["token_borrado"])
        _pregunta_pendiente = r["texto"]
        _vencimiento = time.monotonic() + 45
        return _pregunta_pendiente
    if r.get("disco_ausente") and accion in {"foto", "video"} and externo:
        return _preguntar(accion, True, r["texto"])
    return r["texto"]


def responder_camara(texto):
    """Devuelve una respuesta local o None. No se consulta Ollama para órdenes reconocidas."""
    t = _normalizar(texto)
    if _pendiente and time.monotonic() >= _vencimiento:
        cancelar_dialogo_camara()
    if _pendiente:
        accion, alternativa = _pendiente
        if t in {"no", "cancelar", "cancela", "olvidalo", "mejor no", "no gracias"}:
            cancelar_dialogo_camara()
            return "De acuerdo, cancelé la solicitud."
        if accion == "confirmar_borrado":
            # Una respuesta afirmativa explícita y vigente, nunca una
            # elección de destino ni una frase que solo contenga «sí».
            cancelar_dialogo_camara()
            if t in {"si", "si estoy seguro", "si estoy segura", "si eliminala", "si eliminalo", "si borralo", "si borrala", "si por favor", "confirmo la eliminacion"}:
                return _rpc("confirmar_borrado", token=alternativa)["texto"]
            # Otra instrucción cancela el borrado y sigue su ruta normal.
            orden = detectar_orden_camara(texto)
            if orden is None:
                return "Cancelé la eliminación. No eliminé ningún archivo."
            accion, externo = orden
            if accion == "vision_objeto_no_soportado":
                return "Este modelo aún no reconoce abanicos ni ventiladores como una categoría independiente."
            if accion in {"foto", "video"} and externo is None:
                return _preguntar(accion)
            return _realizar(accion, bool(externo))
        elegido = _destino(t)
        if alternativa and t in {"si", "si gracias", "si por favor", "de acuerdo", "esta bien", "claro"}:
            elegido = False
        if elegido is not None:
            cancelar_dialogo_camara()
            return _realizar(accion, elegido)
        # Cualquier otra instrucción cancela la captura pendiente; no se guarda.
        cancelar_dialogo_camara()
    orden = detectar_orden_camara(texto)
    
    if orden is None:
        return None
    accion, externo = orden
    if accion == "vision_objeto_no_soportado":
        return "Este modelo aún no reconoce abanicos ni ventiladores como una categoría independiente."
    if accion in {"foto", "video"} and externo is None:
        return _preguntar(accion)
    return _realizar(accion, bool(externo))


if __name__ == "__main__" and "--servidor-camara" in sys.argv:
    _servidor_camara()
