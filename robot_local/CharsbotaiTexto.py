import subprocess
import requests
import re
import time
import socketio
import unicodedata
import json
import os
import random
import sys
import wave
import struct
import shutil
import tempfile
import signal
import select
import threading
from collections import deque

# Control del sistema: consultas de fecha, hora y estado general.
if __package__:
    from .modulos.control_sistema import responder_fecha_hora, responder_estado_general, responder_control_software, responder_dialogo_archivos, actualizar_dialogo_archivos, responder_consulta_internet, responder_dialogo_investigacion, actualizar_dialogo_investigacion, iniciar_control_escritorio, cerrar_control_escritorio
    from .modulos.camara import responder_camara, actualizar_dialogo_camara, cerrar_camara, iniciar_vision_al_arrancar, consultar_estado_vision, consultar_estado_disco_externo, ControlSaludos, consultar_muestra_saludo
else:
    from modulos.control_sistema import responder_fecha_hora, responder_estado_general, responder_control_software, responder_dialogo_archivos, actualizar_dialogo_archivos, responder_consulta_internet, responder_dialogo_investigacion, actualizar_dialogo_investigacion, iniciar_control_escritorio, cerrar_control_escritorio
    from modulos.camara import responder_camara, actualizar_dialogo_camara, cerrar_camara, iniciar_vision_al_arrancar, consultar_estado_vision, consultar_estado_disco_externo, ControlSaludos, consultar_muestra_saludo

NUMPY_IMPORT_ERROR = None
SHERPA_ONNX_IMPORT_ERROR = None

try:
    import numpy as np
except Exception as e:
    np = None
    NUMPY_IMPORT_ERROR = str(e)

try:
    import sherpa_onnx
except Exception as e:
    sherpa_onnx = None
    SHERPA_ONNX_IMPORT_ERROR = str(e)

try:
    from vosk import Model as VoskModel
    from vosk import KaldiRecognizer as VoskRecognizer
    from vosk import SetLogLevel as VoskSetLogLevel
except ImportError:
    VoskModel = None
    VoskRecognizer = None
    VoskSetLogLevel = None

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_LOAD_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:1.5b-instruct"
WAKE_WORD = "charsbotai"

BACKEND_URL = "http://localhost:3000"
BACKEND_HEALTH_URL = "http://localhost:3000/health"
BACKEND_COMMAND_URL = "http://localhost:3000/robot/command"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SHERPA_MODEL_DIR = os.path.abspath(
    os.path.join(
        BASE_DIR,
        "..",
        "modelos",
        "sherpa",
        "sherpa-onnx-nemo-canary-180m-flash-en-es-de-fr-int8",
    )
)

SHERPA_ENCODER = os.path.join(SHERPA_MODEL_DIR, "encoder.int8.onnx")
SHERPA_DECODER = os.path.join(SHERPA_MODEL_DIR, "decoder.int8.onnx")
SHERPA_TOKENS = os.path.join(SHERPA_MODEL_DIR, "tokens.txt")

SAMPLE_RATE = 16000

# Ajustes de detección de voz para el micrófono USB actual.
# Se eligieron a partir de las pruebas realizadas en la Raspberry:
# ruido/silencio ~150-300 RMS y voz clara normalmente > 350 RMS.
SHERPA_RMS_THRESHOLD = 260
SHERPA_START_FRAMES = 1
SHERPA_END_SILENCE_FRAMES = 6
SHERPA_PRE_ROLL_FRAMES = 4
SHERPA_CHUNK_BYTES = 3200   # 100 ms: 16000 Hz * 2 bytes * 0.1 s
SHERPA_MAX_UTTERANCE_SECONDS = 10
SHERPA_WAIT_FOR_VOICE_SECONDS = 5
SHERPA_NUM_THREADS = 2

# VAD real: Silero VAD mediante sherpa-onnx.
# Coloca el archivo en:
# ~/Domitica-main/modelos/sherpa/silero_vad.onnx
SILERO_VAD_MODEL = os.path.abspath(
    os.path.join(BASE_DIR, "..", "modelos", "sherpa", "silero_vad.onnx")
)
SILERO_VAD_THRESHOLD = 0.35
SILERO_VAD_MIN_SILENCE = 0.70
SILERO_VAD_MIN_SPEECH = 0.15
SILERO_VAD_MAX_SPEECH = 10.0
SILERO_VAD_WINDOW_SIZE = 512
SILERO_VAD_BUFFER_SECONDS = 20

# Conservamos audio crudo alrededor de cada segmento de voz.
# Así Sherpa recibe también el inicio y final de la frase aunque Silero
# marque la voz unas décimas tarde.
VAD_PRE_ROLL_SECONDS = 0.50
VAD_POST_ROLL_SECONDS = 0.22

# Menos contexto enviado a Ollama = menos tiempo de evaluación en la Pi.
# Se conserva el mensaje system y las últimas interacciones útiles.
# Conversación LLM optimizada para Raspberry Pi.
# Domótica, sensores, expresiones y estados se resuelven ANTES del LLM.
LLM_CONTEXT_MESSAGES = 3
LLM_NUM_CTX = 1024

# Mismo prompt para calentamiento y conversación real.
LLM_SYSTEM_FAST = (
    "Eres Charsbotai. Responde en español, directo y natural. "
    "No saludes salvo que el usuario te salude. "
    "No inventes datos. Si piden brevedad, responde muy breve. "
    "Continúa el diálogo si necesitas una respuesta del usuario. "
    "Haz preguntas de seguimiento solo cuando sean naturales o necesarias; "
    "no termines todas las respuestas con una pregunta."
)

# Voz en streaming:
# - primer fragmento corto para conservar la respuesta rápida;
# - fragmentos posteriores más largos para hablar de forma fluida.
LLM_PRIMER_FRAGMENTO_PALABRAS = 8
LLM_FRAGMENTO_PALABRAS = 26

# Conversación continua selectiva: tiempo para EMPEZAR a contestar.
# Se cuenta a partir de que termina la voz del robot, no de que Ollama
# empieza a generar. No modifica el tiempo de captura de una frase.
CONVERSACION_CONTINUA = True
CONVERSACION_ESPERA_SEGUNDOS = 18.0

# Animación sincronizada con la señal de salida, nunca con el micrófono.
# Valores iniciales ajustables según el volumen y la latencia del audio.
ANIMACION_VOZ_SINCRONIZADA = True
ANIMACION_VOZ_RMS_INICIO = 180
ANIMACION_VOZ_RMS_FIN = 100
ANIMACION_VOZ_BLOQUE_MS = 20
ANIMACION_VOZ_SILENCIO_MS = 120

# Filtro adicional para el modo de respaldo RMS.
SHERPA_MIN_VOICED_FRAMES = 2
SHERPA_MIN_VOICED_RATIO = 0.08
SHERPA_MIN_PEAK_RMS = 300

# Detector auxiliar de interrupción.
# IMPORTANTE: Sherpa sigue siendo el reconocedor principal.
# Vosk se usa únicamente mientras Charsbotai está HABLANDO para detectar
# unas pocas frases como "robot para" o "cállate".
VOSK_STOP_MODEL_PATH = os.path.abspath(
    os.path.join(BASE_DIR, "..", "modelos", "vosk-model-small-es-0.42")
)

WAKE_GRAMMAR = [
    "robot",
    "oye robot",
    "asistente",
    "robots",
    "[unk]"
]

STOP_GRAMMAR = [
    "robot para",
    "robot pare",
    "robot callate",
    "robot cállate",
    "oye robot para",
    "oye robot callate",
    "para de hablar",
    "pare de hablar",
    "deja de hablar",
    "callate",
    "cállate",
    "silencio",
    "detente",
    "no hables",
    "[unk]"
]

WAKE_WORDS = [
    "charsbotai",
    "char botai",
    "char bot",
    "robot",
    "oye robot",
    "asistente",
    "roboam",
    "robam",
    "robo",
    "robó",
    "robots",
    # Variantes que Sherpa ha producido en las pruebas:
    "sharbot tai",
    "sharbotai",
    "charlottei",
    "charlotte i",
    "charbotay",
    "char botay",
    "charles botai"
]

sio = socketio.Client()
socket_conectado = False
emocion_final_pendiente = None
emocion_final_modo_pendiente = "state"
intentos_no_entendidos = 0
last_interaction_time = time.time()
sleeping_sent = False
sleep_started_at = None
sleep_scheduled_at = None
NATURAL_EMOTION_DURATION_MS = 10000
INACTIVITY_MIN_SLEEP_SECONDS = 10 * 60
INACTIVITY_MAX_SLEEP_SECONDS = 60 * 60
SLEEP_DURATION_SECONDS = 50 * 60

# Sherpa se mantiene cargado en memoria para evitar recrear el modelo
# cada vez que el usuario habla.
SHERPA_RECOGNIZER = None
MICROFONO_ACTUAL = None

# Fuente/salida opcionales con cancelación de eco para permitir
# interrumpir la voz del robot mientras está hablando.
MICROFONO_INTERRUPCION = None
SALIDA_TTS = None
AEC_MODULE_ID = None

# Modelo pequeño auxiliar para detectar únicamente órdenes de interrupción.
VOSK_STOP_MODEL = None

# Silero VAD se mantiene cargado en memoria.
SILERO_VAD = None
SILERO_VAD_ACTIVO = False

# Evita mensajes de Socket.IO y voz de despedida mientras Ctrl+C
# está cerrando el programa.
APAGANDO = False

# Solo se abre una continuación si la respuesta realmente terminó de sonar.
# hablar() mantiene su retorno original (True = orden de interrupción).
ULTIMA_VOZ_COMPLETA = False
MONITOR_TTS_AVISADO = False

# El proveedor real se añadirá con la telemetría del Arduino Mega.
# None bloquea los saludos sin sondear la cámara ni cambiar la escucha.
PROVEEDOR_PROXIMIDAD_SALUDOS = None
CONTROL_SALUDOS = ControlSaludos()
SILENCIO_VERIFICADO_SALUDO = False
ULTIMA_EMOCION_SALUDO = ("neutral", "state")


def conectar_socket():
    global socket_conectado

    if socket_conectado:
        return

    try:
        sio.connect(BACKEND_URL)
        socket_conectado = True
        print("[Socket.IO] Charsbotai conectado al backend.")
        enviar_estado("Charsbotai conectado.")
        enviar_emocion("happy")
    except Exception as e:
        socket_conectado = False
        print(f"[Socket.IO] No se pudo conectar al backend: {e}")

def programar_proximo_sueno():
    """
    Programa un momento aleatorio para dormir entre 10 y 60 minutos
    después de la última interacción.
    """
    global sleep_scheduled_at
    sleep_scheduled_at = last_interaction_time + random.uniform(
        INACTIVITY_MIN_SLEEP_SECONDS,
        INACTIVITY_MAX_SLEEP_SECONDS
    )


def marcar_interaccion():
    """
    Registra actividad del usuario. Si estaba dormido, despierta de inmediato.
    """
    global last_interaction_time, sleeping_sent, sleep_started_at

    estaba_dormido = sleeping_sent
    last_interaction_time = time.time()
    sleeping_sent = False
    sleep_started_at = None
    programar_proximo_sueno()

    if estaba_dormido:
        enviar_emocion("neutral", modo="state")


def revisar_inactividad(activo=False):
    """
    Charsbotai no se duerme de inmediato: después de 10 minutos de inactividad
    puede dormirse en cualquier momento hasta cumplir 1 hora. Si se duerme,
    permanece dormido máximo 50 minutos y luego despierta solo.
    """
    global sleeping_sent, sleep_started_at

    if activo:
        return

    ahora = time.time()

    if sleep_scheduled_at is None:
        programar_proximo_sueno()

    if sleeping_sent:
        if sleep_started_at and ahora - sleep_started_at >= SLEEP_DURATION_SECONDS:
            enviar_emocion("neutral", modo="state")
            marcar_interaccion()
        return

    if sleep_scheduled_at and ahora >= sleep_scheduled_at:
        enviar_emocion("sleeping", modo="state")
        sleeping_sent = True
        sleep_started_at = ahora


def resetear_confusiones():
    global intentos_no_entendidos
    intentos_no_entendidos = 0


def registrar_no_entendido():
    """
    Sube la intensidad de la expresión si Charsbotai no entiende varias veces.
    """
    global intentos_no_entendidos
    intentos_no_entendidos += 1

    if intentos_no_entendidos >= 4:
        return "angry"

    if intentos_no_entendidos >= 2:
        return "annoyed"

    return "confused"


def detectar_solicitud_peligrosa(texto):
    """
    Detecta instrucciones peligrosas o que no debería ejecutar.
    """
    t = corregir_texto_vosk(texto)

    palabras_peligrosas = [
        "rompe", "romper", "destruye", "destruir",
        "quema", "quemar", "incendia", "incendiar",
        "electrocuta", "electrocutar", "lastima", "lastimar",
        "golpea", "golpear", "explota", "explotar"
    ]

    return any(p in t for p in palabras_peligrosas)


def inferir_emocion_natural(mensaje, respuesta=""):
    """
    Decide la emoción final de forma natural según el mensaje y la respuesta.
    """
    t = corregir_texto_vosk(f"{mensaje} {respuesta}")

    if any(p in t for p in [
        "jajaja", "ja ja", "jeje", "chiste", "gracioso", "chistoso",
        "risa", "divertido", "me dio risa", "excelente", "genial",
        "increible", "muy bien", "perfecto"
    ]):
        return "excited"

    # Enojado: molestia fuerte, enojo o rabia.
    if (
        any(p in t for p in [
            "estoy enojado", "estoy enojada",
            "me hiciste enojar", "me haces enojar",
            "estoy furioso", "estoy furiosa",
            "tengo rabia", "odio esto", "ya basta"
        ])
        or any(raiz in t for raiz in [
            "enojad", "furios", "rabia"
        ])
    ):
        return "angry"

    # Serio/molesto: molestia leve, fastidio o cansancio.
    if (
        any(p in t for p in [
            "esto es molesto", "me estas molestando", "me estás molestando",
            "me molesta", "que fastidio", "qué fastidio",
            "es un fastidio", "otra vez", "otra vez lo mismo",
            "ya te dije", "ya te lo dije", "no me hagas repetir",
            "deja de molestar", "estoy harto", "estoy harta",
            "me estoy cansando", "esto cansa", "que molesto", "qué molesto"
        ])
        or any(raiz in t for raiz in [
            "molest", "fastidi", "hart"
        ])
    ):
        return "annoyed"

    if any(p in t for p in [
        "no pude", "no puedo", "error", "fallo", "falla", "perdi conexion",
        "sin conexion", "no tengo conexion"
    ]):
        return "sad"

    if any(p in t for p in [
        "no entiendo", "no entendi", "repite", "repetir", "no lo entendi",
        "no escuché", "no escuche"
    ]):
        return "confused"

    if any(p in t for p in [
        "agua", "presencia", "alguien cerca", "temperatura alta", "alerta",
        "inesperado", "cuidado"
    ]):
        return "surprised"

    if any(p in t for p in [
        "listo", "claro", "con gusto", "hecho", "correcto", "encendi",
        "apague", "abri", "cerre"
    ]):
        return "happy"

    return "neutral"


def detectar_comando_descanso(texto):
    """
    Permite dormir o despertar la cara con frases naturales.
    """
    t = corregir_texto_vosk(texto)

    if any(p in t for p in ["duerme", "duermete", "descansa", "modo descanso", "vete a dormir"]):
        return "sleeping"

    if any(p in t for p in ["despierta", "levantate", "modo activo"]):
        return "neutral"

    return None


def obtener_modo_robot():
    """
    Revisa si el backend está disponible y si el ESP32D está conectado.

    Retorna:
    - "local": no hay backend disponible
    - "backend": backend disponible, pero ESP32D no conectado
    - "iot": backend disponible y ESP32D conectado
    """
    try:
        respuesta = requests.get(BACKEND_HEALTH_URL, timeout=5)
        respuesta.raise_for_status()

        data = respuesta.json()

        if data.get("ok") and data.get("esp32Connected"):
            return "iot"

        if data.get("ok"):
            return "backend"

        return "local"

    except Exception:
        return "local"

def normalizar_texto(texto):
    """
    Convierte texto a minúsculas y quita acentos.
    Ejemplo: 'baño' -> 'bano'
    """
    texto = texto.lower()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto

def corregir_texto_vosk(texto):
    """
    Corrige errores comunes del reconocimiento de voz.

    El nombre se conserva por compatibilidad con el resto del proyecto,
    pero esta versión también corrige resultados típicos de Sherpa.
    """
    t = normalizar_texto(texto)

    reemplazos = {
        "roboam": "robot",
        "robam": "robot",
        "robó": "robot",
        "robo": "robot",
        "robots": "robot",

        # Variantes de "Charsbotai" observadas con Sherpa.
        "sharbot tai": "charsbotai",
        "sharbotai": "charsbotai",
        "charlottei": "charsbotai",
        "charlotte i": "charsbotai",
        "charbotay": "charsbotai",
        "char botay": "charsbotai",
        "charles botai": "charsbotai",
        "charles botay": "charsbotai",

        # Variantes de órdenes observadas en pruebas.
        "encien": "enciende",

        "apagaban": "apaga",
        "apagaba": "apaga",
        "apaguen": "apaga",
        "apague": "apaga",

        "prendan": "prende",
        "prender": "prende",
        "enciendan": "enciende",
        "encender": "enciende",

        "luse": "luces",
        "luses": "luces",
        "toda luces": "todas las luces",
        "toda la luces": "todas las luces",
        "todos luces": "todas las luces",
        "toda novio sen": "todas las luces",
        "novio sen": "luces",

        "muestrame": "muestra",
        "muéstrame": "muestra",
        "enseñame": "ensena",
        "enséñame": "ensena",
        "ensename": "ensena",

        "alegre": "feliz",
        "contento": "feliz",
        "contenta": "feliz",
        "dormido": "durmiendo",
        "dormida": "durmiendo",
        "pensativo": "pensando",
        "pensativa": "pensando",
        "confundida": "confundido",
        "sorprendida": "sorprendido",
        "enfadado": "enojado",
        "enfadada": "enojado"
    }

    for mal, bien in reemplazos.items():
        t = re.sub(rf"\b{re.escape(mal)}\b", bien, t)

    # Correcciones contextuales: evitamos convertir "fósforo" globalmente,
    # porque puede ser una palabra válida en una conversación normal.
    t = re.sub(
        r"\b(enciende|prende)\s+(?:un|el)?\s*fosforo\b",
        r"\1 el foco",
        t,
    )
    t = re.sub(
        r"\bapaga\s+(?:un|el)?\s*fosforo\b",
        "apaga el foco",
        t,
    )

    return t

def obtener_modo_robot():
    """
    Revisa si el backend está disponible y si el ESP32D está conectado.

    Retorna:
    - local: no hay backend
    - backend: hay backend, pero no ESP32D
    - iot: hay backend y ESP32D
    """
    try:
        respuesta = requests.get(BACKEND_HEALTH_URL, timeout=5)
        respuesta.raise_for_status()
        data = respuesta.json()

        if data.get("ok") and data.get("esp32Connected"):
            return "iot"

        if data.get("ok"):
            return "backend"

        return "local"

    except Exception:
        return "local"


def obtener_estado_domotico():
    """
    Obtiene el estado actual del backend y sensores.
    """
    try:
        respuesta = requests.get(BACKEND_HEALTH_URL, timeout=5)
        respuesta.raise_for_status()
        return respuesta.json()
    except Exception:
        return None


def detectar_consulta_sensor(texto):
    """
    Detecta preguntas sobre sensores.
    """
    t = normalizar_texto(texto)

    if any(p in t for p in ["temperatura", "grados", "calor", "frio"]):
        return "temperatura"

    if any(p in t for p in ["humedad", "humedo"]):
        return "humedad"

    if any(p in t for p in ["agua", "sensor de agua", "hay agua"]):
        return "agua"

    if any(p in t for p in ["distancia", "presencia", "alguien cerca", "persona cerca", "hay alguien"]):
        return "presencia"

    if any(p in t for p in ["estado", "sensores", "lecturas"]):
        return "estado"

    return None


def formatear_numero(valor, unidad=""):
    if valor is None:
        return "sin lectura"

    try:
        return f"{float(valor):.1f}{unidad}"
    except Exception:
        return f"{valor}{unidad}"


def responder_consulta_sensor(tipo):
    """
    Responde usando los datos reales del ESP32D.
    """
    data = obtener_estado_domotico()

    if not data:
        enviar_emocion("sad")
        fijar_emocion_final("sad")
        return (
            "No tengo conexión con el sistema domótico en este momento. "
            "Puedo conversar contigo, pero no puedo consultar los sensores."
        )

    if not data.get("esp32Connected"):
        enviar_emocion("confused")
        fijar_emocion_final("confused")
        return (
            "No hay ningún dispositivo ESP32D conectado en este momento. "
            "Cuando el ESP32D esté conectado, podré consultar los sensores."
        )

    estado = data.get("estado", {})

    temp_dht = estado.get("tempDHT")
    temp_lm35 = estado.get("tempLM35")
    humedad = estado.get("humedad")
    agua = estado.get("agua")
    distancia = estado.get("distancia")
    presencia = estado.get("hayPersonaCerca")

    sensor_emotion = "happy"

    if tipo == "temperatura":
        try:
            if (temp_dht is not None and float(temp_dht) >= 35) or (temp_lm35 is not None and float(temp_lm35) >= 35):
                sensor_emotion = "surprised"
        except Exception:
            pass
        enviar_emocion(sensor_emotion)
        fijar_emocion_final(sensor_emotion)
        return (
            "La temperatura actual es: "
            f"{formatear_numero(temp_dht, ' grados')} en el sensor DHT11 "
            f"y {formatear_numero(temp_lm35, ' grados')} en el sensor LM35. "
            "Por ahora no tengo un sensor exclusivo del cuarto, son lecturas generales del entorno."
        )

    if tipo == "humedad":
        enviar_emocion("happy")
        fijar_emocion_final("happy")
        return f"La humedad actual es de {formatear_numero(humedad, ' por ciento')}."

    if tipo == "agua":
        if agua:
            enviar_emocion("surprised")
            fijar_emocion_final("surprised")
            return "El sensor de agua indica que sí hay presencia de agua."
        enviar_emocion("happy")
        fijar_emocion_final("happy")
        return "El sensor de agua indica que no hay presencia de agua."

    if tipo == "presencia":
        if presencia:
            enviar_emocion("surprised")
            fijar_emocion_final("surprised")
            return f"Sí detecto presencia cerca. La distancia es de {distancia} centímetros."
        enviar_emocion("happy")
        fijar_emocion_final("happy")
        return f"No detecto presencia cerca. La distancia registrada es de {distancia} centímetros."

    if tipo == "estado":
        enviar_emocion("happy")
        fijar_emocion_final("happy")
        return (
            f"Temperatura DHT11: {formatear_numero(temp_dht, ' grados')}. "
            f"Temperatura LM35: {formatear_numero(temp_lm35, ' grados')}. "
            f"Humedad: {formatear_numero(humedad, ' por ciento')}. "
            f"Distancia: {distancia} centímetros. "
            f"Presencia: {'sí' if presencia else 'no'}. "
            f"Agua: {'sí' if agua else 'no'}."
        )

    return "No encontré esa lectura del sensor."

def ejecutar_comando_domotico(comando):
    """
    Ejecuta uno o varios comandos domóticos solo si el ESP32D está conectado.
    """
    comandos = comando if isinstance(comando, list) else [comando]

    modo_robot = obtener_modo_robot()

    if modo_robot == "local":
        enviar_emocion("sad")
        fijar_emocion_final("sad")
        return (
            "Entendí el comando, pero no tengo conexión con el sistema domótico. "
            "Puedo seguir conversando contigo, pero no puedo controlar la casita."
        )

    if modo_robot == "backend":
        enviar_emocion("confused")
        fijar_emocion_final("confused")
        return (
            "Entendí el comando, pero no hay ningún dispositivo ESP32D conectado. "
            "Cuando el ESP32D esté conectado, podré ejecutarlo físicamente."
        )

    try:
        enviar_emocion("thinking")
        comandos_ok = []

        for cmd in comandos:
            enviar_estado(f"Ejecutando comando domótico: {cmd}")

            respuesta = requests.post(
                BACKEND_COMMAND_URL,
                json={"command": cmd},
                timeout=10
            )

            data = respuesta.json()

            if respuesta.status_code == 200 and data.get("ok"):
                comandos_ok.append(cmd)
            else:
                enviar_emocion("sad")
                fijar_emocion_final("sad")
                return data.get("reply", f"No pude ejecutar el comando {cmd}.")

        enviar_emocion("happy")
        fijar_emocion_final("happy")

        if len(comandos_ok) > 1:
            if comandos_ok[0].endswith(":on"):
                return "Listo, encendí las luces."
            if comandos_ok[0].endswith(":off"):
                return "Listo, apagué las luces."
            return "Listo, ejecuté los comandos."

        return "Listo, ejecuté el comando."

    except requests.exceptions.ConnectionError:
        enviar_emocion("sad")
        fijar_emocion_final("sad")
        return "Perdí conexión con el sistema domótico antes de poder ejecutar el comando."

    except Exception as e:
        enviar_emocion("sad")
        fijar_emocion_final("sad")
        return f"Ocurrió un error al ejecutar el comando domótico: {e}"

def enviar_emocion(emotion, modo="state", duracion_ms=None):
    global socket_conectado, ULTIMA_EMOCION_SALUDO
    ULTIMA_EMOCION_SALUDO = (emotion, modo)

    if APAGANDO:
        return

    if not socket_conectado:
        return

    if not getattr(sio, "connected", False):
        socket_conectado = False
        return

    payload = {
        "emotion": emotion,
        "mode": modo
    }

    if modo == "natural":
        payload["autoReset"] = True
        payload["durationMs"] = duracion_ms or NATURAL_EMOTION_DURATION_MS
        payload["resetTo"] = "neutral"

    try:
        sio.emit("robot:emotion", payload)
    except Exception:
        # Si el backend desapareció durante la ejecución, simplemente
        # dejamos de intentar emitir hasta una futura reconexión.
        socket_conectado = False


def enviar_estado(texto):
    global socket_conectado

    if APAGANDO:
        return

    if not socket_conectado:
        return

    if not getattr(sio, "connected", False):
        socket_conectado = False
        return

    try:
        sio.emit("robot:status", {
            "text": texto
        })
    except Exception:
        socket_conectado = False


def desconectar_socket_silenciosamente():
    """
    Cierra Socket.IO sin generar errores durante Ctrl+C.
    """
    global socket_conectado

    try:
        if getattr(sio, "connected", False):
            sio.disconnect()
    except Exception:
        pass

    socket_conectado = False


# Memoria temporal de la conversación.
# Se mantiene mientras el programa esté abierto.
historial = [
    {
        "role": "system",
        "content": (
            "Eres Charsbotai, un agente robótico doméstico creado para interactuar "
            "con usuarios y controlar un entorno inteligente mediante IoT. "
            "Responde siempre en español, de forma clara y amable. "
            "No saludes ni agregues introducciones innecesarias salvo que el usuario te salude. "
            "Por defecto da solo la información necesaria y evita explicaciones demasiado largas; "
            "normalmente responde en 2 a 4 oraciones completas y cortas. "
            "Si el usuario pide que respondas de manera breve, resumida, corta, en pocas palabras "
            "o algo equivalente, responde realmente breve: máximo 1 o 2 oraciones completas, "
            "sin introducciones, listas innecesarias ni detalles adicionales. "
            "Nunca dejes una palabra u oración incompleta al final de la respuesta. "
            "Mantén el hilo de la conversación usando el historial anterior. "
            "Si el usuario se refiere a algo que dijiste antes, usa el contexto. "
            "Si no sabes algo, dilo con honestidad y no inventes datos. "
            "El sistema puede controlar dispositivos domóticos cuando el backend "
            "y el ESP32D están conectados. Si el usuario pide prender, apagar, "
            "abrir o cerrar algo, no digas que no puedes; si no entiendes bien "
            "el comando, pide que lo repita más claro."
        )
    }
]

def usuario_pide_respuesta_breve(texto):
    """
    Detecta cuándo el usuario pide explícitamente una respuesta muy corta.
    """
    t = normalizar_texto(texto)

    expresiones = [
        "de manera breve",
        "de forma breve",
        "brevemente",
        "respuesta breve",
        "responde breve",
        "explicalo breve",
        "explica breve",
        "de manera corta",
        "de forma corta",
        "respuesta corta",
        "en pocas palabras",
        "resumelo",
        "resume esto",
        "de manera resumida",
        "de forma resumida",
        "solo lo necesario"
    ]

    return any(expresion in t for expresion in expresiones)


def limitar_respuesta_llm(texto, breve=False):
    """
    Mantiene respuestas cortas sin cortar palabras ni oraciones a la mitad.
    """
    if not texto:
        return ""

    texto = re.sub(r"\s+", " ", texto).strip()

    max_oraciones = 2 if breve else 4

    # Separa conservando oraciones completas terminadas en . ! ?
    coincidencias = re.findall(r"[^.!?]+[.!?]+", texto)

    if coincidencias:
        respuesta = " ".join(
            oracion.strip()
            for oracion in coincidencias[:max_oraciones]
        ).strip()

        if respuesta:
            return respuesta

    # Si el modelo devolvió una sola frase sin punto final, no la cortamos.
    return texto


def precargar_ollama():
    """
    Carga y calienta Qwen usando /api/chat y el mismo system prompt
    de las preguntas reales.
    """
    try:
        print("Preparando y calentando el modelo local de Ollama...")

        respuesta = requests.post(
            OLLAMA_URL,
            json={
                "model": MODEL,
                "stream": False,
                "keep_alive": "30m",
                "messages": [
                    {
                        "role": "system",
                        "content": LLM_SYSTEM_FAST
                    },
                    {
                        "role": "user",
                        "content": "Responde únicamente: listo."
                    }
                ],
                "options": {
                    "temperature": 0,
                    "num_predict": 1,
                    "num_ctx": LLM_NUM_CTX
                }
            },
            timeout=120
        )

        respuesta.raise_for_status()
        print("Modelo local listo y caliente.\n")

    except Exception as e:
        print(f"No pude precalentar Ollama: {e}")

def es_comando_detener_voz(texto):
    """
    Detecta órdenes naturales para que Charsbotai deje de hablar.
    Durante la propia voz del robot se acepta una lista más tolerante,
    porque Sherpa puede reconocer "cállate" con pequeñas variaciones.
    """
    if not texto:
        return False

    t = corregir_texto_vosk(texto)
    t = re.sub(r"[.,;:¿?¡!]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()

    if not t:
        return False

    # Frases inequívocas.
    frases_fuertes = [
        "callate",
        "caya te",
        "cayate",
        "calla",
        "silencio",
        "deja de hablar",
        "para de hablar",
        "pare de hablar",
        "deja de decir",
        "ya callate",
        "ya cayate",
        "ya deja de hablar",
        "ya para de hablar",
        "no hables",
        "detente",
        "deten",
        "stop"
    ]

    if any(frase in t for frase in frases_fuertes):
        return True

    palabras = t.split()

    # Mientras Charsbotai está hablando, una frase corta con "para/pare"
    # también se acepta. Así funciona aunque Sherpa omita la palabra "robot".
    if len(palabras) <= 5:
        if t in ["para", "pare", "ya para", "ya pare"]:
            return True

        if "robot" in palabras and any(
            palabra in palabras for palabra in ["para", "pare", "detente"]
        ):
            return True

        if t.startswith("oye robot") and any(
            palabra in palabras for palabra in ["para", "pare", "detente"]
        ):
            return True

    return False


def cargar_vad_silero():
    """
    Carga Silero VAD una sola vez.

    Silero decide si el sonido contiene voz humana. Así ya no dependemos
    principalmente del nivel RMS para decidir cuándo alguien habla.

    Si el modelo no está instalado, Charsbotai conserva automáticamente
    el detector RMS anterior como respaldo.
    """
    global SILERO_VAD, SILERO_VAD_ACTIVO, SILERO_VAD_WINDOW_SIZE

    SILERO_VAD = None
    SILERO_VAD_ACTIVO = False

    if sherpa_onnx is None or np is None:
        print("Silero VAD no disponible en la API Python.")
        if np is None:
            print(f" - NumPy: {NUMPY_IMPORT_ERROR or 'no disponible'}")
        if sherpa_onnx is None:
            print(f" - sherpa_onnx: {SHERPA_ONNX_IMPORT_ERROR or 'no disponible'}")
        print("Usaré el detector de voz anterior como respaldo.")
        return False

    if not os.path.isfile(SILERO_VAD_MODEL):
        print("No encontré silero_vad.onnx.")
        print("Por ahora usaré el detector RMS anterior.")
        print("Ruta esperada:")
        print(SILERO_VAD_MODEL)
        return False

    try:
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = SILERO_VAD_MODEL
        config.silero_vad.threshold = SILERO_VAD_THRESHOLD
        config.silero_vad.min_silence_duration = SILERO_VAD_MIN_SILENCE
        config.silero_vad.min_speech_duration = SILERO_VAD_MIN_SPEECH
        config.silero_vad.max_speech_duration = SILERO_VAD_MAX_SPEECH
        config.silero_vad.window_size = SILERO_VAD_WINDOW_SIZE
        config.sample_rate = SAMPLE_RATE
        config.num_threads = 1
        config.provider = "cpu"
        config.debug = False

        SILERO_VAD_WINDOW_SIZE = config.silero_vad.window_size

        SILERO_VAD = sherpa_onnx.VoiceActivityDetector(
            config,
            buffer_size_in_seconds=SILERO_VAD_BUFFER_SECONDS
        )

        SILERO_VAD_ACTIVO = True

        print("Silero VAD listo: voz humana + audio completo con pre/post-roll.")
        print(
            f"VAD threshold={SILERO_VAD_THRESHOLD}, "
            f"silencio final={SILERO_VAD_MIN_SILENCE:.2f}s"
        )
        return True

    except Exception as e:
        SILERO_VAD = None
        SILERO_VAD_ACTIVO = False
        print(f"No pude iniciar Silero VAD: {e}")
        print("Usaré el detector RMS anterior como respaldo.")
        return False


def cargar_detector_interrupcion():
    """
    Carga un modelo Vosk pequeño SOLO para detectar la orden de detener voz.

    Esto no reemplaza Sherpa. El reconocimiento normal de Charsbotai sigue
    siendo Canary/Sherpa. Vosk queda restringido a una gramática diminuta,
    lo que lo hace apropiado para escuchar "robot para", "cállate", etc.,
    incluso mientras el robot está hablando.
    """
    global VOSK_STOP_MODEL

    if VOSK_STOP_MODEL is not None:
        return True

    if VoskModel is None or VoskRecognizer is None:
        print("Detector rápido de interrupción no disponible; usaré Sherpa como respaldo.")
        return False

    if not os.path.isdir(VOSK_STOP_MODEL_PATH):
        print("No encontré el modelo Vosk auxiliar para interrupciones.")
        return False

    try:
        if VoskSetLogLevel is not None:
            VoskSetLogLevel(-1)

        print("Cargando detector rápido de 'cállate'...")
        VOSK_STOP_MODEL = VoskModel(VOSK_STOP_MODEL_PATH)
        print("Detector de interrupción listo.")
        return True

    except Exception as e:
        VOSK_STOP_MODEL = None
        print(f"No pude cargar el detector de interrupción: {e}")
        return False


def detectar_llamada_vosk(audio_pcm):
    """
    Detecta únicamente si el usuario llamó al robot.

    Retorna:
        (detectado: bool, texto_wake: str)
    """
    if not audio_pcm:
        return False, ""

    if VOSK_STOP_MODEL is None or VoskRecognizer is None:
        return False, ""

    try:
        grammar_json = json.dumps(
            WAKE_GRAMMAR,
            ensure_ascii=False
        )

        recognizer = VoskRecognizer(
            VOSK_STOP_MODEL,
            SAMPLE_RATE,
            grammar_json
        )

        for i in range(0, len(audio_pcm), 4000):
            recognizer.AcceptWaveform(audio_pcm[i:i + 4000])

        resultado = json.loads(recognizer.FinalResult())
        texto = resultado.get("text", "").strip()
        t = normalizar_texto(texto)

        detectado = bool(
            re.search(r"\brobot\b", t)
            or "oye robot" in t
            or re.search(r"\basistente\b", t)
            or "charsbotai" in t
            or "char bot" in t
        )

        return detectado, texto

    except Exception:
        return False, ""


def detectar_llamada_en_texto(texto):
    """
    Respaldo cuando el detector Vosk de wake word no está disponible.
    """
    if not texto:
        return False

    return extraer_mensaje_despues_wake_word(texto) is not None


def detener_proceso_voz(proceso_voz):
    """
    Detiene espeak-ng de forma completa, incluyendo su grupo de proceso.
    """
    if not proceso_voz:
        return

    if proceso_voz.poll() is not None:
        return

    try:
        os.killpg(os.getpgid(proceso_voz.pid), signal.SIGTERM)
        proceso_voz.wait(timeout=1)
        return
    except Exception:
        pass

    try:
        proceso_voz.terminate()
        proceso_voz.wait(timeout=1)
        return
    except Exception:
        pass

    try:
        os.killpg(os.getpgid(proceso_voz.pid), signal.SIGKILL)
    except Exception:
        try:
            proceso_voz.kill()
        except Exception:
            pass


def escuchar_orden_detener_vosk(proceso_voz, microfono):
    """
    Escucha continuamente mientras espeak-ng está hablando.

    A diferencia de la versión anterior:
    - no abre/cierra el micrófono cada 2 segundos;
    - usa un único flujo continuo;
    - usa una gramática limitada a órdenes de detener;
    - puede cortar espeak-ng apenas detecta la orden.
    """
    if VOSK_STOP_MODEL is None:
        return False, ""

    if not microfono:
        return False, ""

    try:
        grammar_json = json.dumps(
            STOP_GRAMMAR,
            ensure_ascii=False
        )

        recognizer = VoskRecognizer(
            VOSK_STOP_MODEL,
            SAMPLE_RATE,
            grammar_json
        )

    except Exception:
        return False, ""

    comando_audio = [
        "parec",
        "--device", microfono,
        "--rate", str(SAMPLE_RATE),
        "--channels", "1",
        "--format", "s16le"
    ]

    proceso_audio = None

    try:
        proceso_audio = subprocess.Popen(
            comando_audio,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

        while proceso_voz.poll() is None:
            data = proceso_audio.stdout.read(4000)

            if not data:
                break

            # Revisamos resultado parcial para poder interrumpir rápido.
            recognizer.AcceptWaveform(data)

            try:
                parcial = json.loads(recognizer.PartialResult()).get(
                    "partial", ""
                ).strip()
            except Exception:
                parcial = ""

            if parcial and es_comando_detener_voz(parcial):
                detener_proceso_voz(proceso_voz)
                return True, parcial

            # También revisamos resultados completos.
            try:
                resultado = json.loads(recognizer.Result()).get(
                    "text", ""
                ).strip()
            except Exception:
                resultado = ""

            if resultado and es_comando_detener_voz(resultado):
                detener_proceso_voz(proceso_voz)
                return True, resultado

        return False, ""

    finally:
        if proceso_audio:
            try:
                proceso_audio.terminate()
                proceso_audio.wait(timeout=0.5)
            except Exception:
                try:
                    proceso_audio.kill()
                except Exception:
                    pass


def escuchar_orden_detener_sherpa(proceso_voz, microfono):
    """
    Respaldo si no está disponible el detector Vosk auxiliar.
    """
    while proceso_voz.poll() is None:
        audio = capturar_bloque_audio(
            microfono,
            duracion_segundos=1.8
        )

        if not audio:
            continue

        if proceso_voz.poll() is not None:
            break

        if not audio_sherpa_confiable(audio, modo_interrupcion=True):
            continue

        texto = reconocer_sherpa_pcm(audio)

        if texto and es_comando_detener_voz(texto):
            detener_proceso_voz(proceso_voz)
            return True, texto

    return False, ""


def obtener_salida_default():
    """
    Obtiene la salida de audio predeterminada de PipeWire/PulseAudio.
    """
    salida = ejecutar_linux(["pactl", "get-default-sink"])
    return salida.strip() if salida else None


def fuente_audio_existe(nombre):
    if not nombre:
        return False

    salida = ejecutar_linux(["pactl", "list", "short", "sources"])
    return any(nombre == linea.split()[1] for linea in salida.splitlines() if len(linea.split()) >= 2)


def salida_audio_existe(nombre):
    if not nombre:
        return False

    salida = ejecutar_linux(["pactl", "list", "short", "sinks"])
    return any(nombre == linea.split()[1] for linea in salida.splitlines() if len(linea.split()) >= 2)


def configurar_cancelacion_eco(microfono):
    """
    Intenta crear una fuente/salida con cancelación de eco.

    Si PipeWire/PulseAudio no permite cargar module-echo-cancel,
    Charsbotai continúa usando el micrófono y salida normales.
    """
    global MICROFONO_INTERRUPCION, SALIDA_TTS, AEC_MODULE_ID

    MICROFONO_INTERRUPCION = microfono
    SALIDA_TTS = None
    AEC_MODULE_ID = None

    fuente_aec = "charsbotai_aec_source"
    salida_aec = "charsbotai_aec_sink"

    # Si quedaron creadas de una ejecución anterior, se pueden reutilizar.
    if fuente_audio_existe(fuente_aec) and salida_audio_existe(salida_aec):
        MICROFONO_INTERRUPCION = fuente_aec
        SALIDA_TTS = salida_aec
        print("Cancelación de eco disponible para interrupciones.")
        return True

    salida_default = obtener_salida_default()

    if not salida_default:
        print("No pude detectar la salida de audio para cancelación de eco.")
        return False

    comando = [
        "pactl",
        "load-module",
        "module-echo-cancel",
        f"source_master={microfono}",
        f"sink_master={salida_default}",
        f"source_name={fuente_aec}",
        f"sink_name={salida_aec}",
        "aec_method=webrtc"
    ]

    try:
        resultado = subprocess.run(
            comando,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )

        if resultado.returncode != 0:
            print("Cancelación de eco no disponible; usaré el audio normal.")
            return False

        modulo = resultado.stdout.strip()

        if modulo:
            AEC_MODULE_ID = modulo

        # Espera breve a que PipeWire/PulseAudio cree los dispositivos.
        time.sleep(0.4)

        if fuente_audio_existe(fuente_aec) and salida_audio_existe(salida_aec):
            MICROFONO_INTERRUPCION = fuente_aec
            SALIDA_TTS = salida_aec
            print("Cancelación de eco activada para poder interrumpir la voz.")
            return True

    except Exception as e:
        print(f"No pude activar cancelación de eco: {e}")

    MICROFONO_INTERRUPCION = microfono
    SALIDA_TTS = None
    AEC_MODULE_ID = None
    return False


def liberar_cancelacion_eco():
    """
    Descarga el módulo de cancelación de eco creado por esta ejecución.
    """
    global AEC_MODULE_ID

    if not AEC_MODULE_ID:
        return

    try:
        subprocess.run(
            ["pactl", "unload-module", str(AEC_MODULE_ID)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False
        )
    except Exception:
        pass

    AEC_MODULE_ID = None


def capturar_bloque_audio(microfono, duracion_segundos=1.25):
    """
    Captura un bloque corto de audio sin esperar silencios.

    Se usa únicamente mientras Charsbotai está hablando para poder detectar
    rápidamente frases como "robot para" o "cállate".
    """
    if not microfono:
        return None

    bytes_objetivo = int(SAMPLE_RATE * 2 * duracion_segundos)

    comando_audio = [
        "parec",
        "--device", microfono,
        "--rate", str(SAMPLE_RATE),
        "--channels", "1",
        "--format", "s16le"
    ]

    proceso = None

    try:
        proceso = subprocess.Popen(
            comando_audio,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )

        datos = proceso.stdout.read(bytes_objetivo)

        if not datos:
            return None

        return datos

    except Exception:
        return None

    finally:
        if proceso:
            try:
                proceso.terminate()
                proceso.wait(timeout=0.5)
            except Exception:
                try:
                    proceso.kill()
                except Exception:
                    pass


def obtener_monitor_salida_tts():
    """Localiza el monitor de la salida que utiliza eSpeak, sin cambiarla."""
    try:
        salida = SALIDA_TTS or os.environ.get("PULSE_SINK")
        if not salida:
            salida = subprocess.run(
                ["pactl", "get-default-sink"], capture_output=True,
                text=True, timeout=1, check=True
            ).stdout.strip()

        fuentes = subprocess.run(
            ["pactl", "list", "short", "sources"], capture_output=True,
            text=True, timeout=1, check=True
        ).stdout.splitlines()
        monitor = f"{salida}.monitor"
        if any(len(linea.split()) >= 2 and linea.split()[1] == monitor
               for linea in fuentes):
            return monitor

        # Si la salida virtual AEC no expone monitor, intentar el de la
        # salida física predeterminada (si sigue siendo la salida del AEC).
        if SALIDA_TTS and salida == SALIDA_TTS:
            salida_default = subprocess.run(
                ["pactl", "get-default-sink"], capture_output=True,
                text=True, timeout=1, check=True
            ).stdout.strip()
            monitor = f"{salida_default}.monitor"
            if salida_default != salida and any(
                len(linea.split()) >= 2 and linea.split()[1] == monitor
                for linea in fuentes
            ):
                return monitor
    except Exception:
        pass
    return None


def vigilar_animacion_voz(proceso_monitor, detener):
    """Lee PCM de la salida y cambia la cara solo al detectar voz o pausa."""
    estado_hablando = False
    silencio_ms = 0
    bytes_bloque = SAMPLE_RATE * 2 * ANIMACION_VOZ_BLOQUE_MS // 1000
    buffer = bytearray()
    ultimo_audio = time.monotonic()

    try:
        while not detener.is_set():
            prontos, _, _ = select.select([proceso_monitor.stdout], [], [], 0.05)
            if not prontos:
                if time.monotonic() - ultimo_audio > 0.6:
                    raise RuntimeError("El monitor no entrega audio")
                continue

            data = os.read(proceso_monitor.stdout.fileno(), 4096)
            if not data:
                raise RuntimeError("El monitor de audio se cerró")
            ultimo_audio = time.monotonic()
            buffer.extend(data)

            while len(buffer) >= bytes_bloque and not detener.is_set():
                bloque = bytes(buffer[:bytes_bloque])
                del buffer[:bytes_bloque]
                rms = calcular_rms_pcm(bloque)

                if not estado_hablando:
                    if rms >= ANIMACION_VOZ_RMS_INICIO:
                        enviar_emocion("speaking", modo="state")
                        estado_hablando = True
                        silencio_ms = 0
                elif rms >= ANIMACION_VOZ_RMS_FIN:
                    silencio_ms = 0
                else:
                    silencio_ms += ANIMACION_VOZ_BLOQUE_MS
                    if silencio_ms >= ANIMACION_VOZ_SILENCIO_MS:
                        enviar_emocion("neutral", modo="state")
                        estado_hablando = False
                        silencio_ms = 0

    except Exception as e:
        if not detener.is_set() and not APAGANDO:
            print(f"[Animación] Monitor no disponible: {e}. Usaré el fragmento de voz.")
            if not estado_hablando:
                enviar_emocion("speaking", modo="state")


def iniciar_monitor_animacion():
    """Abre un monitor de solo lectura; no cambia ni graba el audio a disco."""
    global MONITOR_TTS_AVISADO
    monitor = obtener_monitor_salida_tts()
    if not monitor:
        if not MONITOR_TTS_AVISADO:
            print("[Animación] No encontré un monitor de salida. Usaré animación por fragmentos.")
            MONITOR_TTS_AVISADO = True
        return None

    try:
        return subprocess.Popen(
            ["parec", "--device", monitor, "--rate", str(SAMPLE_RATE),
             "--channels", "1", "--format", "s16le", "--latency-msec=40",
             "--process-time-msec=20"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
        )
    except Exception:
        return None


def cerrar_monitor_animacion(proceso_monitor, detener, hilo):
    """Cierra el monitor antes de mostrar la emoción final de la respuesta."""
    detener.set()
    if hilo is not None and hilo.is_alive():
        hilo.join()
    if proceso_monitor is None:
        return
    try:
        proceso_monitor.terminate()
        proceso_monitor.wait(timeout=0.5)
    except Exception:
        try:
            proceso_monitor.kill()
            proceso_monitor.wait(timeout=0.5)
        except Exception:
            pass
    finally:
        if proceso_monitor.stdout is not None:
            proceso_monitor.stdout.close()


def hablar(texto, aplicar_emocion_final=True):
    """
    Hace que Charsbotai hable usando espeak-ng.

    La respuesta puede estar ya generada completa, pero espeak-ng se ejecuta
    como un proceso independiente. Por eso sí se puede interrumpir a mitad
    de una respuesta: basta con detectar la orden y terminar ese proceso.

    Mientras habla:
    - el reconocimiento normal sigue siendo Sherpa;
    - un detector auxiliar muy restringido escucha solo órdenes de parar.
    """
    global emocion_final_pendiente, emocion_final_modo_pendiente
    global ULTIMA_VOZ_COMPLETA

    ULTIMA_VOZ_COMPLETA = False
    proceso_voz = None
    proceso_monitor = None
    hilo_animacion = None
    detener_animacion = threading.Event()
    interrumpido = False
    texto_interrupcion = ""

    try:
        if aplicar_emocion_final:
            emocion_final = emocion_final_pendiente
            modo_final = emocion_final_modo_pendiente
            emocion_final_pendiente = None
            emocion_final_modo_pendiente = "state"
        else:
            # En streaming la emoción final se aplica al terminar toda
            # la respuesta, no al terminar cada fragmento.
            emocion_final = None
            modo_final = "state"

        # Se conserva el proceso de voz original. El monitor solo lee su
        # salida y permite cerrar la boca durante las pausas reales.
        if socket_conectado and ANIMACION_VOZ_SINCRONIZADA:
            proceso_monitor = iniciar_monitor_animacion()

        # Conservamos la pequeña espera original en las respuestas normales.
        if aplicar_emocion_final:
            time.sleep(0.05)

        entorno = os.environ.copy()

        if SALIDA_TTS:
            entorno["PULSE_SINK"] = SALIDA_TTS

        proceso_voz = subprocess.Popen(
            ["espeak-ng", "-v", "es-mx", "-s", "145", texto],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=entorno,
            start_new_session=True
        )

        if proceso_monitor is not None:
            hilo_animacion = threading.Thread(
                target=vigilar_animacion_voz,
                args=(proceso_monitor, detener_animacion), daemon=True
            )
            hilo_animacion.start()
        else:
            # Si no hay monitor, mantenemos la animación por fragmento.
            enviar_emocion("speaking", modo="state")

        vigilar_interrupcion = (
            MICROFONO_ACTUAL is not None
            and len(texto.split()) >= 5
        )

        if not vigilar_interrupcion:
            proceso_voz.wait()

        else:
            microfono_interrupcion = (
                MICROFONO_INTERRUPCION
                if MICROFONO_INTERRUPCION
                else MICROFONO_ACTUAL
            )

            if VOSK_STOP_MODEL is not None:
                interrumpido, texto_interrupcion = escuchar_orden_detener_vosk(
                    proceso_voz,
                    microfono_interrupcion
                )
            else:
                interrumpido, texto_interrupcion = escuchar_orden_detener_sherpa(
                    proceso_voz,
                    microfono_interrupcion
                )

            # Si terminó por sí solo y no hubo orden, garantizamos wait().
            if not interrumpido and proceso_voz.poll() is None:
                proceso_voz.wait()

        # La escucha de interrupciones continúa siendo la original.
        # El monitor se cierra primero para evitar que sus eventos
        # sobrescriban la expresión final o la de interrupción.
        cerrar_monitor_animacion(proceso_monitor, detener_animacion, hilo_animacion)
        proceso_monitor = None
        ULTIMA_VOZ_COMPLETA = not interrumpido and proceso_voz.returncode == 0

        if interrumpido:
            print(f"\nTú dijiste mientras hablaba: {texto_interrupcion}")
            print("Charsbotai: Dejé de hablar.\n")

            enviar_emocion(
                "annoyed",
                modo="natural",
                duracion_ms=NATURAL_EMOTION_DURATION_MS
            )

        elif aplicar_emocion_final and emocion_final:
            enviar_emocion(emocion_final, modo=modo_final)

        elif aplicar_emocion_final:
            enviar_emocion("neutral", modo="state")

        else:
            # Ya no hay voz: mostrar la cara normal durante la espera
            # hasta que Ollama entregue el próximo fragmento.
            enviar_emocion("neutral", modo="state")

        return interrumpido

    except KeyboardInterrupt:
        detener_proceso_voz(proceso_voz)
        raise

    except Exception as e:
        ULTIMA_VOZ_COMPLETA = False
        detener_proceso_voz(proceso_voz)
        cerrar_monitor_animacion(proceso_monitor, detener_animacion, hilo_animacion)
        proceso_monitor = None
        if not APAGANDO:
            print(f"[Error de voz]: {e}")
            enviar_emocion("sad")
        return False

    finally:
        if proceso_monitor is not None:
            cerrar_monitor_animacion(proceso_monitor, detener_animacion, hilo_animacion)


def limpiar_texto(texto):
    """
    Limpia espacios y comillas innecesarias.
    """
    return texto.strip().strip('"').strip("'")

def extraer_mensaje_despues_wake_word(texto):
    """
    Detecta palabra de activación y devuelve lo que se dijo después.
    """
    t = corregir_texto_vosk(texto)

    palabras_ordenadas = sorted(WAKE_WORDS, key=len, reverse=True)

    for palabra in palabras_ordenadas:
        p = normalizar_texto(palabra)

        patron = rf"\b{re.escape(p)}\b"
        match = re.search(patron, t)

        if match:
            mensaje = t[match.end():].strip()
            mensaje = re.sub(r"^[,.:;¿?¡!\s]+", "", mensaje)
            return limpiar_texto(mensaje)

    return None

def detectar_comando_domotico(texto):
    """
    Detecta comandos domóticos de forma más flexible.
    """
    t = corregir_texto_vosk(texto)

    encender = any(p in t for p in [
        "enciende", "encender",
        "prende", "prender",
        "activa", "activar"
    ])

    apagar = any(p in t for p in [
        "apaga", "apagar",
        "desactiva", "desactivar"
    ])

    abrir = any(p in t for p in ["abre", "abrir"])
    cerrar = any(p in t for p in ["cierra", "cerrar"])

    if abrir and "puerta" in t:
        return "puerta:abrir"

    if cerrar and "puerta" in t:
        return "puerta:cerrar"

    if encender and "ventilador" in t:
        return "ventilador:on"

    if apagar and "ventilador" in t:
        return "ventilador:off"

    accion = None

    if encender:
        accion = "on"

    if apagar:
        accion = "off"

    if not accion:
        return None

    comandos_luces = [
        "entrada",
        "cocina",
        "bano",
        "cuarto",
        "foco"
    ]

    palabras_todas = [
        "todas las luces",
        "todos los focos",
        "todos los leds",
        "todas las lamparas",
        "las luces",
        "los focos",
        "los leds",
        "toda",
        "todas",
        "todo",
        "todos"
    ]

    if any(p in t for p in palabras_todas):
        return [f"{cmd}:{accion}" for cmd in comandos_luces]

    zonas = {
        "entrada": ["entrada", "recibidor"],
        "cocina": ["cocina"],
        "bano": ["bano", "banio", "sanitario"],
        "cuarto": ["cuarto", "habitacion", "recamara", "dormitorio"],
        "foco": ["foco", "foco principal", "luz principal"]
    }

    for dispositivo, palabras in zonas.items():
        for palabra in palabras:
            if palabra in t:
                return f"{dispositivo}:{accion}"

    if any(p in t for p in ["luz", "led", "foco", "lampara"]):
        return f"foco:{accion}"

    return None

def construir_contexto_llm_rapido():
    """
    Construye un prompt pequeño para reducir el tiempo al primer token.
    Las funciones del robot se resuelven antes de llegar al LLM.
    """
    system_rapido = {
        "role": "system",
        "content": LLM_SYSTEM_FAST
    }

    recientes = historial[1:][-LLM_CONTEXT_MESSAGES:]
    return [system_rapido] + recientes

def quitar_saludo_inicial_llm(texto):
    """
    Quita saludos/fórmulas vacías solo al inicio y limpia signos sueltos.
    """
    if not texto:
        return texto

    limpio = texto.lstrip()

    patrones = [
        r"^[\s¡!¿?.,;:\-]*hola\b[\s¡!¿?.,;:\-]*",
        r"^[\s¡!¿?.,;:\-]*claro\b[\s¡!¿?.,;:\-]*",
        r"^[\s¡!¿?.,;:\-]*por supuesto\b[\s¡!¿?.,;:\-]*",
        r"^[\s¡!¿?.,;:\-]*aqui tienes\b[\s¡!¿?.,;:\-]*",
        r"^[\s¡!¿?.,;:\-]*aquí tienes\b[\s¡!¿?.,;:\-]*",
    ]

    cambiado = True

    while cambiado and limpio:
        cambiado = False

        for patron in patrones:
            nuevo = re.sub(
                patron,
                "",
                limpio,
                count=1,
                flags=re.I
            )

            if nuevo != limpio:
                limpio = nuevo.lstrip()
                cambiado = True
                break

    limpio = re.sub(r"^[\s¡!¿?.,;:\-]+", "", limpio)
    return limpio

def cortar_por_palabras(buffer, cantidad):
    """
    Corta en un límite de palabra, nunca a mitad de una palabra.
    """
    # Ollama transmite tokens, no palabras completas. El final del buffer
    # NO confirma una palabra: puede llegar después el resto de sus letras.
    # Solo contamos palabras que ya tienen un espacio confirmado detrás.
    matches = list(re.finditer(r"\S+(?=\s)", buffer))

    if len(matches) < cantidad:
        return None, buffer

    fin = matches[cantidad - 1].end()

    # Si hay puntuación natural muy cerca, preferirla, pero únicamente
    # si también termina una unidad completa (ya llegó un espacio).
    for m in re.finditer(r"[,;:.!?]+(?=\s)", buffer):
        if fin <= m.end() <= fin + 35:
            fin = m.end()
            break

    fragmento = buffer[:fin].strip()
    resto = buffer[fin:].lstrip()
    return fragmento, resto


def extraer_fragmento_hablable(buffer, primer_fragmento=False):
    """
    Divide la respuesta pensando en dos objetivos:

    1. PRIMER FRAGMENTO:
       Sale pronto para conservar la sensación de respuesta rápida.
       Puede aprovechar una coma o puntuación natural.

    2. FRAGMENTOS SIGUIENTES:
       Son bastante más largos y no se cortan por cada coma.
       Se intenta hablar una oración completa o un bloque largo de texto,
       reduciendo las pausas entre procesos de espeak-ng.

    Nunca se corta una palabra por la mitad.
    """
    if not buffer:
        return None, buffer

    buffer = buffer.lstrip()

    if primer_fragmento:
        # Para empezar rápido sí aceptamos una frontera natural corta.
        for m in re.finditer(r"[,;:.!?]+(?=\s)", buffer):
            candidato = buffer[:m.end()].strip()

            if len(candidato.split()) >= 6:
                return candidato, buffer[m.end():].lstrip()

        return cortar_por_palabras(
            buffer,
            LLM_PRIMER_FRAGMENTO_PALABRAS
        )

    # Después del primer bloque buscamos principalmente final de oración.
    # No cortamos por cada coma, que era lo que producía demasiadas pausas.
    for m in re.finditer(r"[.!?]+(?=\s)", buffer):
        candidato = buffer[:m.end()].strip()

        # Si la oración/bloque ya tiene una longitud razonable, se habla
        # completo en una sola llamada a espeak-ng.
        if len(candidato.split()) >= 16:
            return candidato, buffer[m.end():].lstrip()

    # Si Qwen genera una oración muy larga sin punto, soltamos un bloque
    # mayor para que la voz continúe sin quedarse esperando demasiado.
    return cortar_por_palabras(
        buffer,
        LLM_FRAGMENTO_PALABRAS
    )


def preguntar_llm_streaming_y_hablar(mensaje):
    """
    Ollama genera en streaming. Charsbotai empieza rápido con un primer
    bloque corto y después usa bloques largos para mantener una voz fluida.

    También imprime dos tiempos distintos:
    - Primer token: cuánto tardó Ollama en empezar a generar.
    - Inicio de voz: cuánto tardó hasta tener un fragmento seguro para hablar.

    Si el usuario dice 'cállate', se corta espeak-ng y se cancela la respuesta.
    """
    global ULTIMA_VOZ_COMPLETA
    ULTIMA_VOZ_COMPLETA = False
    voz_completa = True
    hubo_voz = False

    historial.append({"role": "user", "content": mensaje})

    respuesta_muy_breve = usuario_pide_respuesta_breve(mensaje)
    max_tokens = 72 if respuesta_muy_breve else 140

    mensajes_solicitud = construir_contexto_llm_rapido()

    caracteres_prompt = sum(
        len(str(m.get("content", "")))
        for m in mensajes_solicitud
    )

    payload = {
        "model": MODEL,
        "stream": True,
        "keep_alive": "30m",
        "messages": mensajes_solicitud,
        "options": {
            "temperature": 0.20,
            "num_predict": max_tokens,
            "num_ctx": LLM_NUM_CTX
        }
    }

    respuesta_http = None
    respuesta_completa = ""
    texto_hablado = ""
    buffer_habla = ""
    interrumpido = False
    inicio = time.time()
    tiempo_primer_token = None
    tiempo_inicio_voz = None
    primer_fragmento = True

    try:
        print(
            f"[Prompt LLM: {len(mensajes_solicitud)} mensajes, "
            f"{caracteres_prompt} caracteres]",
            flush=True
        )

        respuesta_http = requests.post(
            OLLAMA_URL,
            json=payload,
            stream=True,
            timeout=(10, 120)
        )
        respuesta_http.raise_for_status()

        print("Charsbotai: ", end="", flush=True)

        for linea in respuesta_http.iter_lines(decode_unicode=True):
            if not linea:
                continue

            try:
                data = json.loads(linea)
            except Exception:
                continue

            contenido = (
                data.get("message", {}).get("content", "")
                if isinstance(data, dict)
                else ""
            )

            if contenido:
                if tiempo_primer_token is None:
                    tiempo_primer_token = time.time() - inicio
                    print(
                        f"\n[Primer token de Ollama: {tiempo_primer_token:.2f} s]",
                        flush=True
                    )

                respuesta_completa += contenido
                buffer_habla += contenido

            while True:
                # Limpiar el saludo solo en el primer bloque y sin alterar
                # el buffer original hasta tener un fragmento confirmado.
                # Así no se pierde texto de un token todavía incompleto.
                candidato = (
                    quitar_saludo_inicial_llm(buffer_habla)
                    if primer_fragmento else buffer_habla
                )
                fragmento, resto = extraer_fragmento_hablable(
                    candidato,
                    primer_fragmento=primer_fragmento
                )

                if not fragmento:
                    break

                buffer_habla = resto

                if tiempo_inicio_voz is None:
                    tiempo_inicio_voz = time.time() - inicio
                    print(
                        f"[Inicio de voz: {tiempo_inicio_voz:.2f} s]\nCharsbotai: ",
                        end="",
                        flush=True
                    )

                print(fragmento, end=" ", flush=True)
                texto_hablado += (" " if texto_hablado else "") + fragmento
                primer_fragmento = False

                interrumpido = hablar(fragmento, aplicar_emocion_final=False)
                hubo_voz = True
                voz_completa = voz_completa and ULTIMA_VOZ_COMPLETA
                if interrumpido:
                    break

            if interrumpido:
                break

            if data.get("done"):
                break

        # Pronunciar lo que quede al finalizar la generación. Solo ahora
        # podemos aceptar la última palabra sin un espacio posterior.
        if not interrumpido:
            if primer_fragmento:
                buffer_habla = quitar_saludo_inicial_llm(buffer_habla)
            resto_final = buffer_habla.strip()

            if resto_final:
                if tiempo_inicio_voz is None:
                    tiempo_inicio_voz = time.time() - inicio
                    print(
                        f"[Inicio de voz: {tiempo_inicio_voz:.2f} s]\nCharsbotai: ",
                        end="",
                        flush=True
                    )

                print(resto_final, end=" ", flush=True)
                texto_hablado += (" " if texto_hablado else "") + resto_final

                interrumpido = hablar(resto_final, aplicar_emocion_final=False)
                hubo_voz = True
                voz_completa = voz_completa and ULTIMA_VOZ_COMPLETA

        print()

        respuesta_guardada = texto_hablado.strip()

        if not respuesta_guardada:
            respuesta_guardada = quitar_saludo_inicial_llm(
                respuesta_completa.strip()
            )

        if respuesta_guardada:
            historial.append({
                "role": "assistant",
                "content": respuesta_guardada
            })

        if len(historial) > 21:
            del historial[1:3]

        if not interrumpido:
            emocion_natural = inferir_emocion_natural(
                mensaje,
                respuesta_guardada
            )
            enviar_emocion(
                emocion_natural,
                modo="natural",
                duracion_ms=NATURAL_EMOTION_DURATION_MS
            )
        # Si fue interrumpido, hablar() ya dejó la cara annoyed.

        ULTIMA_VOZ_COMPLETA = hubo_voz and voz_completa and not interrumpido
        return respuesta_guardada

    except Exception as e:
        if not APAGANDO:
            print(f"\n[Ollama streaming] {e}")

        error = "No pude comunicarme con mi modelo local."

        if not interrumpido:
            hablar(error)

        ULTIMA_VOZ_COMPLETA = False
        return error

    finally:
        if respuesta_http is not None:
            try:
                respuesta_http.close()
            except Exception:
                pass

def preguntar_llm(mensaje):
    """
    Envía el mensaje a Ollama manteniendo historial.

    - El modelo se mantiene cargado.
    - Una respuesta normal se limita por estilo, no cortando palabras.
    - Si el usuario pide brevedad, se fuerza una respuesta de 1-2 oraciones.
    """
    historial.append({
        "role": "user",
        "content": mensaje
    })

    respuesta_muy_breve = usuario_pide_respuesta_breve(mensaje)

    # Un límite amplio evita que Ollama corte una palabra a la mitad.
    # La brevedad se controla con instrucciones y después por oraciones.
    max_tokens = 180 if respuesta_muy_breve else 320

    mensajes_solicitud = list(historial)

    if respuesta_muy_breve:
        mensajes_solicitud.append({
            "role": "system",
            "content": (
                "Para esta respuesta únicamente: responde en máximo 2 oraciones "
                "muy cortas y completas. Ve directo a la respuesta, sin saludo, "
                "sin introducción y sin información adicional."
            )
        })
    else:
        mensajes_solicitud.append({
            "role": "system",
            "content": (
                "Para esta respuesta usa como máximo 4 oraciones completas. "
                "Ve directo a la información necesaria y evita introducciones innecesarias."
            )
        })

    payload = {
        "model": MODEL,
        "stream": False,
        "keep_alive": "30m",
        "messages": mensajes_solicitud,
        "options": {
            "temperature": 0.25,
            "num_predict": max_tokens
        }
    }

    try:
        respuesta = requests.post(OLLAMA_URL, json=payload, timeout=90)
        respuesta.raise_for_status()

        data = respuesta.json()
        respuesta_llm = data["message"]["content"].strip()

        # Recorta solo por oraciones completas, nunca por caracteres/palabras.
        respuesta_llm = limitar_respuesta_llm(
            respuesta_llm,
            breve=respuesta_muy_breve
        )

        emocion_natural = inferir_emocion_natural(mensaje, respuesta_llm)
        fijar_emocion_final(emocion_natural)

        historial.append({
            "role": "assistant",
            "content": respuesta_llm
        })

        # Limita el historial para que no crezca demasiado.
        # Conserva siempre el mensaje system.
        if len(historial) > 21:
            del historial[1:3]

        return respuesta_llm

    except Exception as e:
        enviar_emocion("sad")
        fijar_emocion_final("sad")
        return f"No pude comunicarme con mi modelo local. Error: {e}"


def parece_comando_domotico(texto):
    """
    Evita que una orden mal reconocida se vaya al LLM.
    """
    t = corregir_texto_vosk(texto)

    palabras_accion = [
        "prende", "enciende", "activa",
        "apaga", "desactiva",
        "abre", "cierra"
    ]

    palabras_casa = [
        "luz", "luces", "led", "leds", "foco", "focos",
        "puerta", "ventilador", "cocina", "cuarto",
        "bano", "entrada"
    ]

    return any(p in t for p in palabras_accion) or any(p in t for p in palabras_casa)

EXPRESIONES_ROBOT = {
    "happy": ["feliz", "sonrisa", "sonriendo", "contento", "alegre"],
    "sad": ["triste", "tristeza"],
    "angry": ["enojado", "enojada", "enojo", "furioso", "furiosa", "molesto fuerte"],
    "annoyed": ["serio molesto", "seria molesta", "molesto", "molesta", "serio", "seria"],
    "neutral": ["neutral", "normal", "sereno", "tranquilo"],
    "excited": ["emocionado", "emocionada", "riendo", "muy feliz", "reir", "risa"],
    "surprised": ["sorprendido", "sorprendida", "sorpresa", "asombrado", "asombrada"],
    "thinking": ["pensando", "pensar", "pensativo", "pensativa"],
    "confused": ["confundido", "confundida", "confuso", "confusa", "dudoso", "duda"],
    "sleeping": ["durmiendo", "dormir", "dormido", "dormida", "sueño", "sueno"],
    "listening": ["escuchando", "escucha", "atento", "atenta"],
    "speaking": ["hablando", "habla"],
}

RESPUESTAS_EXPRESION = {
    "happy": "Claro, esta es mi cara feliz.",
    "sad": "Claro, esta es mi cara triste.",
    "angry": "Claro, esta es mi cara de enojado.",
    "annoyed": "Claro, esta es mi cara de serio molesto.",
    "neutral": "Claro, esta es mi cara neutral.",
    "excited": "Claro, esta es mi cara emocionada.",
    "surprised": "Claro, esta es mi cara de sorprendido.",
    "thinking": "Claro, esta es mi cara de pensando.",
    "confused": "Claro, esta es mi cara de confundido.",
    "sleeping": "Claro, esta es mi cara de durmiendo.",
    "listening": "Claro, esta es mi cara de escuchando.",
    "speaking": "Claro, esta es mi cara de hablando.",
}

def detectar_solicitud_expresion(texto):
    """
    Detecta frases como:
    - pon cara feliz
    - muestra tu expresión de triste
    - pon tu cara de serio molesto
    - haz cara de sorprendido
    """
    t = corregir_texto_vosk(texto)

    activadores = [
        "cara", "expresion", "gesto",
        "pon", "ponme", "ponte", "muestra", "ensena", "haz"
    ]

    if not any(a in t for a in activadores):
        return None

    # Revisar primero frases más específicas.
    if "serio molesto" in t or "seria molesta" in t:
        return "annoyed"

    for emotion, palabras in EXPRESIONES_ROBOT.items():
        for palabra in palabras:
            patron = rf"\b{re.escape(palabra)}\b"
            if re.search(patron, t):
                return emotion

    return None

def fijar_emocion_final(emotion, modo="natural"):
    """
    Guarda la emoción que debe mostrarse después de hablar.

    modo="natural": se muestra 10 segundos y vuelve a neutral.
    modo="manual": se queda fija porque el usuario la pidió.
    modo="state": representa un estado real, como sleeping o neutral.
    """
    global emocion_final_pendiente, emocion_final_modo_pendiente
    emocion_final_pendiente = emotion
    emocion_final_modo_pendiente = modo

def ejecutar_solicitud_expresion(emotion):
    """
    Cambia la cara del robot sin pasar por el LLM.
    La emoción queda fija después de que Charsbotai confirme con voz.
    """
    enviar_emocion(emotion, modo="manual")
    fijar_emocion_final(emotion, modo="manual")
    return RESPUESTAS_EXPRESION.get(emotion, "Claro, cambié mi expresión.")

def consultar_estado_ollama():
    """Consulta el catálogo local; no genera texto ni carga un modelo."""
    try:
        respuesta = requests.get(OLLAMA_URL.rsplit("/", 1)[0] + "/tags", timeout=2)
        respuesta.raise_for_status()
        modelos = respuesta.json().get("models")
        if not isinstance(modelos, list):
            return "Ollama respondió, pero no pude comprobar su catálogo de modelos."
        if any(isinstance(m, dict) and MODEL in (m.get("name"), m.get("model")) for m in modelos):
            return "Ollama responde y el modelo de conversación está disponible localmente."
        return "Ollama responde, pero no encontré el modelo de conversación configurado."
    except Exception:
        return "No pude comprobar la disponibilidad de Ollama."


def consultar_estado_general_robot(mensaje):
    return responder_estado_general(
        mensaje, consultar_vision=consultar_estado_vision,
        consultar_backend=obtener_estado_domotico, consultar_ollama=consultar_estado_ollama,
        sherpa_en_memoria=SHERPA_RECOGNIZER is not None,
        silero_activo=SILERO_VAD_ACTIVO, vosk_en_memoria=VOSK_STOP_MODEL is not None,
        consultar_disco=consultar_estado_disco_externo, microfono=MICROFONO_ACTUAL,
        salida_tts=SALIDA_TTS or os.environ.get("PULSE_SINK"),
        consultar_saludos=lambda: CONTROL_SALUDOS.estado(callable(PROVEEDOR_PROXIMIDAD_SALUDOS)),
    )


def procesar_mensaje(mensaje):
    """
    Decide si el mensaje es consulta de sensores, comando domótico o conversación normal.
    """
    mensaje = limpiar_texto(mensaje)
    mensaje = corregir_texto_vosk(mensaje)

    if not mensaje:
        emocion = registrar_no_entendido()
        enviar_emocion(emocion)
        fijar_emocion_final(emocion)
        return "No escuché una instrucción clara."

    if detectar_solicitud_peligrosa(mensaje):
        resetear_confusiones()
        enviar_emocion("angry")
        fijar_emocion_final("angry")
        return "No voy a hacer eso porque podría ser peligroso."

    descanso = detectar_comando_descanso(mensaje)

    if descanso:
        resetear_confusiones()
        enviar_emocion(descanso, modo="state")
        fijar_emocion_final(descanso, modo="state")
        if descanso == "sleeping":
            return "Está bien, entraré en modo descanso."
        return "Listo, vuelvo a estar activo."

    expresion = detectar_solicitud_expresion(mensaje)

    if expresion:
        resetear_confusiones()
        return ejecutar_solicitud_expresion(expresion)

    # Los saludos se controlan localmente, antes de la domótica y Ollama.
    respuesta_saludo = CONTROL_SALUDOS.responder(mensaje, callable(PROVEEDOR_PROXIMIDAD_SALUDOS))
    if respuesta_saludo is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_saludo

    # Fecha y hora reales del sistema, sin generar datos con Ollama.
    respuesta_sistema = responder_fecha_hora(mensaje)
    if respuesta_sistema is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_sistema

    # Selección de archivos vigente: prioridad frente a "abre la foto" de cámara.
    respuesta_archivos = responder_dialogo_archivos(mensaje)
    if respuesta_archivos is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_archivos

    # Respuesta sí/no a una consulta de información reciente o exacta.
    # La pregunta ha sido ofrecida explícitamente por el robot; nunca
    # navegación remota automática sin aceptación del usuario.
    respuesta_consulta_pendiente = responder_dialogo_investigacion(mensaje)
    if respuesta_consulta_pendiente is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_consulta_pendiente

    # Órdenes de cámara: se resuelven localmente, sin enviar al LLM.
    respuesta_camara = responder_camara(mensaje)
    if respuesta_camara is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_camara

    # Diagnóstico del propio robot, antes de las consultas domóticas genéricas.
    respuesta_general = consultar_estado_general_robot(mensaje)
    if respuesta_general is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_general

    # Control local de aplicaciones, archivos y navegador.
    respuesta_software = responder_control_software(mensaje)
    if respuesta_software is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_software

    respuesta_internet = responder_consulta_internet(mensaje)
    if respuesta_internet is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_internet

    consulta_sensor = detectar_consulta_sensor(mensaje)

    if consulta_sensor:
        resetear_confusiones()
        return responder_consulta_sensor(consulta_sensor)

    comando = detectar_comando_domotico(mensaje)

    if comando:
        resetear_confusiones()
        return ejecutar_comando_domotico(comando)

    if parece_comando_domotico(mensaje):
        emocion = registrar_no_entendido()
        enviar_emocion(emocion)
        fijar_emocion_final(emocion)
        if emocion == "angry":
            return "Ya intenté entender el comando varias veces, pero sigue sin estar claro. Repítelo más simple, por favor."
        if emocion == "annoyed":
            return "Creo que intentaste darme un comando de la casita, pero todavía no lo entendí bien. Dímelo más claro, por favor."
        return (
            "Creo que intentaste darme un comando de la casita, "
            "pero no lo entendí bien. ¿Puedes repetirlo más claro?"
        )

    resetear_confusiones()
    return preguntar_llm(mensaje)

def procesar_mensaje_con_streaming(mensaje):
    """
    Conserva la lógica existente. Solo la conversación normal con Ollama
    usa streaming; domótica, sensores, expresiones y estados siguen igual.

    Retorna: (respuesta, ya_fue_hablada)
    """
    mensaje = limpiar_texto(mensaje)
    mensaje = corregir_texto_vosk(mensaje)

    if not mensaje:
        emocion = registrar_no_entendido()
        enviar_emocion(emocion)
        fijar_emocion_final(emocion)
        return "No escuché una instrucción clara.", False

    if detectar_solicitud_peligrosa(mensaje):
        resetear_confusiones()
        enviar_emocion("angry")
        fijar_emocion_final("angry")
        return "No voy a hacer eso porque podría ser peligroso.", False

    descanso = detectar_comando_descanso(mensaje)
    if descanso:
        resetear_confusiones()
        enviar_emocion(descanso, modo="state")
        fijar_emocion_final(descanso, modo="state")
        if descanso == "sleeping":
            return "Está bien, entraré en modo descanso.", False
        return "Listo, vuelvo a estar activo.", False

    expresion = detectar_solicitud_expresion(mensaje)
    if expresion:
        resetear_confusiones()
        return ejecutar_solicitud_expresion(expresion), False

    # Los saludos se controlan localmente, antes de la domótica y Ollama.
    respuesta_saludo = CONTROL_SALUDOS.responder(mensaje, callable(PROVEEDOR_PROXIMIDAD_SALUDOS))
    if respuesta_saludo is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_saludo, False

    # Fecha y hora reales del sistema, sin generar datos con Ollama.
    respuesta_sistema = responder_fecha_hora(mensaje)
    if respuesta_sistema is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_sistema, False

    # Selección de archivos vigente: prioridad frente a "abre la foto" de cámara.
    respuesta_archivos = responder_dialogo_archivos(mensaje)
    if respuesta_archivos is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_archivos, False

    # Respuesta sí/no a una consulta de información reciente o exacta.
    # La pregunta ha sido ofrecida explícitamente por el robot; nunca
    # navegación remota automática sin aceptación del usuario.
    respuesta_consulta_pendiente = responder_dialogo_investigacion(mensaje)
    if respuesta_consulta_pendiente is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_consulta_pendiente, False

    # Órdenes de cámara: se resuelven localmente, sin enviar al LLM.
    respuesta_camara = responder_camara(mensaje)
    if respuesta_camara is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_camara, False

    # Diagnóstico del propio robot, antes de las consultas domóticas genéricas.
    respuesta_general = consultar_estado_general_robot(mensaje)
    if respuesta_general is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_general, False

    # Control local de aplicaciones, archivos y navegador.
    respuesta_software = responder_control_software(mensaje)
    if respuesta_software is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_software, False

    respuesta_internet = responder_consulta_internet(mensaje)
    if respuesta_internet is not None:
        resetear_confusiones()
        fijar_emocion_final("neutral", modo="state")
        return respuesta_internet, False

    consulta_sensor = detectar_consulta_sensor(mensaje)
    if consulta_sensor:
        resetear_confusiones()
        return responder_consulta_sensor(consulta_sensor), False

    comando = detectar_comando_domotico(mensaje)
    if comando:
        resetear_confusiones()
        return ejecutar_comando_domotico(comando), False

    if parece_comando_domotico(mensaje):
        emocion = registrar_no_entendido()
        enviar_emocion(emocion)
        fijar_emocion_final(emocion)

        if emocion == "angry":
            return (
                "Ya intenté entender el comando varias veces, pero sigue sin "
                "estar claro. Repítelo más simple, por favor.", False
            )
        if emocion == "annoyed":
            return (
                "Creo que intentaste darme un comando de la casita, pero "
                "todavía no lo entendí bien. Dímelo más claro, por favor.", False
            )
        return (
            "Creo que intentaste darme un comando de la casita, pero no lo "
            "entendí bien. ¿Puedes repetirlo más claro?", False
        )

    resetear_confusiones()
    respuesta = preguntar_llm_streaming_y_hablar(mensaje)
    return respuesta, True


def ejecutar_linux(comando):
    """
    Ejecuta un comando de Linux y devuelve su salida.
    """
    try:
        resultado = subprocess.run(
            comando,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
        )
        return resultado.stdout.strip()
    except Exception:
        return ""

def obtener_fuente_default():
    """
    Obtiene el micrófono predeterminado de PipeWire/PulseAudio.
    """
    salida = ejecutar_linux(["pactl", "get-default-source"])
    return salida.strip() if salida else None


def listar_fuentes_audio():
    """
    Lista micrófonos disponibles desde PipeWire/PulseAudio.
    Funciona con Bluetooth, USB, webcam, etc.
    """
    salida = ejecutar_linux(["pactl", "list", "short", "sources"])
    fuentes = []

    for linea in salida.splitlines():
        partes = linea.split()
        if len(partes) >= 2:
            nombre = partes[1]

            # Evitar monitores de salida
            if ".monitor" in nombre:
                continue

            fuentes.append(nombre)

    return fuentes


def seleccionar_microfono():
    """
    Selecciona automáticamente el micrófono.
    Primero usa el predeterminado; si no, usa el primero disponible.
    """
    fuente_default = obtener_fuente_default()
    fuentes = listar_fuentes_audio()

    print("\nFuentes de audio detectadas:")

    if fuentes:
        for fuente in fuentes:
            marca = " ← predeterminado" if fuente == fuente_default else ""
            print(f" - {fuente}{marca}")
    else:
        print("No se detectaron micrófonos.")

    if fuente_default and fuente_default in fuentes:
        return fuente_default

    if fuentes:
        return fuentes[0]

    return None

def extraer_mensaje_despues_wake_word(texto):
    """
    Permite activar con:
    - Charsbotai
    - robot
    - oye robot
    - asistente

    También permite:
    - Charsbotai abre la puerta
    - robot dame la temperatura
    """
    texto_normalizado = corregir_texto_vosk(texto)

    palabras_ordenadas = sorted(WAKE_WORDS, key=len, reverse=True)

    for palabra in palabras_ordenadas:
        palabra_normalizada = normalizar_texto(palabra)
        patron = rf"\b{re.escape(palabra_normalizada)}\b"
        match = re.search(patron, texto_normalizado)

        if match:
            mensaje = texto_normalizado[match.end():].strip()
            mensaje = re.sub(r"^[,.:;¿?¡!\s]+", "", mensaje)
            return limpiar_texto(mensaje)

    return None


def frase_activa_parece_intencion_real(texto):
    """
    Filtro conservador para el segundo turno después de decir solo "robot".

    Evita que una alucinación larga y declarativa pase al LLM mientras
    Charsbotai quedó en modo 'Te escucho'.
    """
    if not texto:
        return False

    t = corregir_texto_vosk(texto)
    palabras = t.split()

    if len(palabras) <= 12:
        return True

    marcadores = [
        "explicame", "dime", "cuentame", "hablame", "quiero",
        "puedes", "podrias", "que es", "como", "por que",
        "para que", "resume", "define", "pon", "abre", "cierra",
        "enciende", "apaga", "ayudame", "necesito"
    ]

    if any(marcador in t for marcador in marcadores):
        return True

    # Si Sherpa conservó signos de pregunta, lo tratamos como pregunta real.
    if "?" in texto or "¿" in texto:
        return True

    return False


def parece_mensaje_dirigido_sin_wake_word(texto):
    """
    Recupera órdenes claras cuando Sherpa reconoce la frase completa
    pero omite solamente la palabra inicial "robot".
    """
    if not texto:
        return False

    t = corregir_texto_vosk(texto)
    t = re.sub(r"^[,.:;¿?¡!\s]+", "", t)
    t = re.sub(r"\s+", " ", t).strip()

    palabras = t.split()

    if len(palabras) < 2:
        return False

    inicios_claros = [
        "explicame",
        "dime",
        "cuentame",
        "hablame",
        "resumeme",
        "resume",
        "define",
        "que es",
        "como funciona",
        "por que",
        "para que sirve",
        "quiero que",
        "puedes explicarme",
        "me puedes explicar",
        "pon cara",
        "muestra tu",
        "haz cara",
        "duerme",
        "descansa",
        "despierta",
        "enciende",
        "prende",
        "apaga",
        "abre",
        "cierra",
        "activa",
        "desactiva"
    ]

    return any(
        t == inicio or t.startswith(inicio + " ")
        for inicio in inicios_claros
    )


def verificar_sherpa():
    """
    Verifica los archivos del modelo y que exista al menos una forma de
    ejecutar Sherpa: API de Python (preferida) o CLI como respaldo.
    """
    faltantes = [
        ruta
        for ruta in (SHERPA_ENCODER, SHERPA_DECODER, SHERPA_TOKENS)
        if not os.path.isfile(ruta)
    ]

    if faltantes:
        print("Faltan archivos del modelo Sherpa:")
        for ruta in faltantes:
            print(f" - {ruta}")
        return False

    api_disponible = sherpa_onnx is not None and np is not None
    cli_disponible = shutil.which("sherpa-onnx-offline") is not None

    if not api_disponible and not cli_disponible:
        print("No encontré Sherpa-ONNX ni como módulo de Python ni como ejecutable.")
        print("Activa el entorno virtual de Domitica-main y verifica la instalación.")
        return False

    return True


def cargar_reconocedor_sherpa():
    """
    Carga Canary una sola vez en memoria.

    Esto evita crear de nuevo el reconocedor cada vez que el usuario habla,
    reduciendo el tiempo de procesamiento entre una frase y la siguiente.
    """
    global SHERPA_RECOGNIZER

    if SHERPA_RECOGNIZER is not None:
        return True

    if sherpa_onnx is None or np is None:
        return False

    try:
        print("Cargando modelo Sherpa en memoria...")

        SHERPA_RECOGNIZER = sherpa_onnx.OfflineRecognizer.from_nemo_canary(
            encoder=SHERPA_ENCODER,
            decoder=SHERPA_DECODER,
            tokens=SHERPA_TOKENS,
            src_lang="es",
            tgt_lang="es",
            num_threads=SHERPA_NUM_THREADS,
            debug=False,
            provider="cpu"
        )

        print("Modelo Sherpa listo.")
        return True

    except Exception as e:
        SHERPA_RECOGNIZER = None
        print(f"No pude mantener Sherpa cargado en memoria: {e}")
        print("Usaré el ejecutable sherpa-onnx-offline como respaldo.")
        return False


def calcular_rms_pcm(data):
    """
    Calcula RMS de audio PCM mono s16le sin depender de NumPy.
    """
    if not data or len(data) < 2:
        return 0

    usable = len(data) - (len(data) % 2)
    if usable <= 0:
        return 0

    total = 0
    muestras = 0

    for (valor,) in struct.iter_unpack("<h", data[:usable]):
        total += valor * valor
        muestras += 1

    if muestras == 0:
        return 0

    return int((total / muestras) ** 0.5)


def analizar_calidad_audio(audio_pcm):
    """
    Analiza una frase PCM en bloques de 100 ms.

    Canary puede inventar texto cuando recibe ruido o muy poca voz.
    Este filtro no intenta entender el contenido: solo comprueba que haya
    suficiente señal parecida a voz antes de mandar el audio al ASR.
    """
    if not audio_pcm:
        return {
            "frames": 0,
            "voiced_frames": 0,
            "voiced_ratio": 0.0,
            "peak_rms": 0,
            "valid": False,
        }

    tamano = SHERPA_CHUNK_BYTES
    rms_frames = []

    for i in range(0, len(audio_pcm), tamano):
        bloque = audio_pcm[i:i + tamano]

        if len(bloque) < 2:
            continue

        rms_frames.append(calcular_rms_pcm(bloque))

    if not rms_frames:
        return {
            "frames": 0,
            "voiced_frames": 0,
            "voiced_ratio": 0.0,
            "peak_rms": 0,
            "valid": False,
        }

    voiced_frames = sum(
        1 for rms in rms_frames
        if rms >= SHERPA_RMS_THRESHOLD
    )

    ratio = voiced_frames / len(rms_frames)
    peak = max(rms_frames)

    valido = (
        voiced_frames >= SHERPA_MIN_VOICED_FRAMES
        and ratio >= SHERPA_MIN_VOICED_RATIO
        and peak >= SHERPA_MIN_PEAK_RMS
    )

    return {
        "frames": len(rms_frames),
        "voiced_frames": voiced_frames,
        "voiced_ratio": ratio,
        "peak_rms": peak,
        "valid": valido,
    }


def audio_sherpa_confiable(audio_pcm, modo_interrupcion=False):
    """
    Decide si el audio contiene suficiente voz real.

    Para una orden de interrupción se permite una frase más corta.
    """
    calidad = analizar_calidad_audio(audio_pcm)

    if modo_interrupcion:
        return (
            calidad["voiced_frames"] >= 1
            and calidad["voiced_ratio"] >= 0.06
            and calidad["peak_rms"] >= 280
        )

    return calidad["valid"]


def guardar_pcm_como_wav(audio_pcm):
    """
    Guarda PCM s16le mono/16 kHz en un WAV temporal.
    """
    temporal = tempfile.NamedTemporaryFile(
        prefix="charsbotai_sherpa_",
        suffix=".wav",
        delete=False,
    )
    ruta = temporal.name
    temporal.close()

    with wave.open(ruta, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(audio_pcm)

    return ruta


def texto_parece_alucinacion_sherpa(texto):
    """
    Detecta patrones frecuentes de alucinación de Canary cuando hay
    silencio, ruido o una frase mal segmentada.
    """
    if not texto:
        return True

    t = normalizar_texto(texto)
    palabras = re.findall(r"[a-z0-9ñ]+", t)

    if not palabras:
        return True

    if len(palabras) >= 8:
        unicas = set(palabras)

        if len(unicas) <= 2:
            return True

    # Secuencias de 3 a 6 palabras repetidas 3 o más veces.
    if len(palabras) >= 12:
        for tamano in range(3, min(7, len(palabras) // 3 + 1)):
            conteo = {}

            for i in range(len(palabras) - tamano + 1):
                bloque = tuple(palabras[i:i + tamano])
                conteo[bloque] = conteo.get(bloque, 0) + 1

                if conteo[bloque] >= 3:
                    return True

    patrones_ruido = [
        "si si si",
        "no no no",
        "que no me acuerdo de verdad que no me acuerdo",
        "se me ha dado la informacion de que se me ha dado",
        "se me ha dado la oportunidad de que se me ha dado"
    ]

    texto_plano = " ".join(palabras)

    if any(patron in texto_plano for patron in patrones_ruido):
        return True

    # Otra señal común de alucinación es una frase larga construida casi
    # enteramente con conectores/fórmulas repetitivas, sin una intención clara.
    # No rechazamos preguntas/órdenes normales.
    if len(palabras) >= 18:
        marcadores_intencion = [
            "robot", "explicame", "dime", "cuentame", "hablame",
            "quiero", "puedes", "podrias", "que es", "como", "por que",
            "para que", "resume", "define", "pon", "abre", "cierra",
            "enciende", "apaga"
        ]

        if not any(marcador in texto_plano for marcador in marcadores_intencion):
            conectores = {
                "que", "de", "la", "el", "los", "las", "en", "con",
                "y", "se", "me", "un", "una", "por", "para", "del",
                "ellos", "todos", "sin"
            }

            cantidad_conectores = sum(
                1 for palabra in palabras if palabra in conectores
            )

            if cantidad_conectores / len(palabras) >= 0.55:
                return True

    return False


def corregir_resultado_sherpa(texto):
    """
    Limpieza previa específica de Sherpa.
    """
    if not texto:
        return ""

    texto = texto.strip()
    texto = re.sub(r"\s+", " ", texto)

    if texto_parece_alucinacion_sherpa(texto):
        return ""

    return texto


def reconocer_sherpa_pcm(audio_pcm):
    """
    Reconoce una frase con Canary.

    Primero usa la API de Python con el modelo ya cargado en memoria.
    Si esa API no está disponible, conserva el CLI como respaldo.
    """
    if not audio_pcm:
        return ""

    # Ruta rápida: Sherpa permanece cargado en RAM.
    if SHERPA_RECOGNIZER is not None and np is not None:
        try:
            muestras = np.frombuffer(audio_pcm, dtype=np.int16)
            muestras = muestras.astype(np.float32) / 32768.0

            stream = SHERPA_RECOGNIZER.create_stream()
            stream.accept_waveform(SAMPLE_RATE, muestras)
            SHERPA_RECOGNIZER.decode_stream(stream)

            texto = stream.result.text.strip()
            return corregir_resultado_sherpa(texto)

        except Exception as e:
            print(f"[Sherpa] Falló la API rápida: {e}")
            print("[Sherpa] Intentaré con el ejecutable como respaldo.")

    # Respaldo: comportamiento anterior con sherpa-onnx-offline.
    ruta_wav = None

    try:
        ruta_wav = guardar_pcm_como_wav(audio_pcm)

        comando = [
            "sherpa-onnx-offline",
            f"--canary-encoder={SHERPA_ENCODER}",
            f"--canary-decoder={SHERPA_DECODER}",
            f"--tokens={SHERPA_TOKENS}",
            "--canary-src-lang=es",
            "--canary-tgt-lang=es",
            f"--num-threads={SHERPA_NUM_THREADS}",
            ruta_wav,
        ]

        proceso_sherpa = None

        try:
            proceso_sherpa = subprocess.Popen(
                comando,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True
            )

            stdout_sherpa, stderr_sherpa = proceso_sherpa.communicate()

            class ResultadoSherpa:
                pass

            resultado = ResultadoSherpa()
            resultado.stdout = stdout_sherpa
            resultado.stderr = stderr_sherpa
            resultado.returncode = proceso_sherpa.returncode

        except KeyboardInterrupt:
            if proceso_sherpa and proceso_sherpa.poll() is None:
                try:
                    os.killpg(os.getpgid(proceso_sherpa.pid), signal.SIGTERM)
                except Exception:
                    try:
                        proceso_sherpa.terminate()
                    except Exception:
                        pass

            raise

        salida = (resultado.stdout or "") + "\n" + (resultado.stderr or "")

        for linea in salida.splitlines():
            linea = linea.strip()

            if not (linea.startswith("{") and '"text"' in linea):
                continue

            try:
                data = json.loads(linea)
                texto = data.get("text", "").strip()
                return corregir_resultado_sherpa(texto)
            except json.JSONDecodeError:
                continue

        if resultado.returncode != 0:
            print("[Sherpa] El reconocedor terminó con error.")
            if resultado.stderr:
                print(resultado.stderr.strip())

        return ""

    finally:
        if ruta_wav and os.path.exists(ruta_wav):
            try:
                os.remove(ruta_wav)
            except OSError:
                pass


def capturar_frase_vad(
    microfono,
    tiempo_espera=None,
    mostrar_deteccion=True
):
    """
    Captura una frase con Silero VAD, pero devuelve AUDIO CRUDO continuo.

    Silero solo decide dónde hay voz. Sherpa recibe:
    - ~500 ms anteriores al comienzo detectado;
    - toda la frase original;
    - ~220 ms posteriores al final.

    Esto ayuda con cualquier petición y reduce palabras perdidas al inicio.
    """
    global SILENCIO_VERIFICADO_SALUDO
    SILENCIO_VERIFICADO_SALUDO = False

    if not SILERO_VAD_ACTIVO or SILERO_VAD is None or np is None:
        return None

    if tiempo_espera is None:
        tiempo_espera = SHERPA_WAIT_FOR_VOICE_SECONDS

    comando_audio = [
        "parec",
        "--device", microfono,
        "--rate", str(SAMPLE_RATE),
        "--channels", "1",
        "--format", "s16le",
    ]

    proceso = None
    inicio_espera = time.time()
    voz_mostrada = False
    capturando_frase = False
    audio_frase = []

    ventanas_pre = max(
        1,
        int(round(VAD_PRE_ROLL_SECONDS * SAMPLE_RATE / SILERO_VAD_WINDOW_SIZE))
    )
    ventanas_post = max(
        1,
        int(round(VAD_POST_ROLL_SECONDS * SAMPLE_RATE / SILERO_VAD_WINDOW_SIZE))
    )
    pre_roll = deque(maxlen=ventanas_pre)

    try:
        SILERO_VAD.reset()
        proceso = subprocess.Popen(
            comando_audio,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        bytes_por_ventana = SILERO_VAD_WINDOW_SIZE * 2

        while True:
            data = proceso.stdout.read(bytes_por_ventana)
            if not data:
                return None
            if len(data) < bytes_por_ventana:
                continue

            muestras_int16 = np.frombuffer(data, dtype=np.int16).copy()
            muestras_float = muestras_int16.astype(np.float32) / 32768.0

            if not capturando_frase:
                pre_roll.append(muestras_int16)

            SILERO_VAD.accept_waveform(muestras_float)

            try:
                segmento_actual = SILERO_VAD.current_segment
                hay_voz_actual = len(segmento_actual.samples) > 0
            except Exception:
                hay_voz_actual = False

            if hay_voz_actual and not capturando_frase:
                capturando_frase = True
                voz_mostrada = True
                audio_frase = [bloque.copy() for bloque in pre_roll]
                if mostrar_deteccion:
                    print("Voz humana detectada...", end="\\r")
            elif capturando_frase:
                audio_frase.append(muestras_int16)

            if not SILERO_VAD.empty():
                SILERO_VAD.pop()

                for _ in range(ventanas_post):
                    data_post = proceso.stdout.read(bytes_por_ventana)
                    if not data_post or len(data_post) < bytes_por_ventana:
                        break
                    audio_frase.append(
                        np.frombuffer(data_post, dtype=np.int16).copy()
                    )

                if not audio_frase:
                    return None

                return np.concatenate(audio_frase).astype(np.int16).tobytes()

            if (
                not voz_mostrada
                and time.time() - inicio_espera >= tiempo_espera
            ):
                SILENCIO_VERIFICADO_SALUDO = True
                return None

    finally:
        try:
            SILERO_VAD.reset()
        except Exception:
            pass

        if proceso:
            try:
                proceso.terminate()
                proceso.wait(timeout=1)
            except Exception:
                try:
                    proceso.kill()
                except Exception:
                    pass


def capturar_frase_inteligente(
    microfono,
    tiempo_espera=None,
    mostrar_deteccion=True
):
    """
    Usa Silero VAD cuando está disponible.
    Si no, conserva el detector RMS anterior.
    """
    if SILERO_VAD_ACTIVO:
        return capturar_frase_vad(
            microfono,
            tiempo_espera=tiempo_espera,
            mostrar_deteccion=mostrar_deteccion
        )

    return capturar_frase_sherpa(
        microfono,
        tiempo_espera=tiempo_espera,
        mostrar_deteccion=mostrar_deteccion
    )


def capturar_frase_sherpa(
    microfono,
    tiempo_espera=None,
    silencio_frames=None,
    max_segundos=None,
    mostrar_deteccion=True
):
    """
    Escucha el micrófono y extrae una sola frase mediante un VAD sencillo
    basado en energía RMS.

    - Espera hasta 5 s a que empiece la voz.
    - Conserva 300 ms previos al inicio para no cortar la primera sílaba.
    - Termina después de un breve silencio.
    - Limita una frase a 10 s.
    """
    global SILENCIO_VERIFICADO_SALUDO
    SILENCIO_VERIFICADO_SALUDO = False

    if tiempo_espera is None:
        tiempo_espera = SHERPA_WAIT_FOR_VOICE_SECONDS

    if silencio_frames is None:
        silencio_frames = SHERPA_END_SILENCE_FRAMES

    if max_segundos is None:
        max_segundos = SHERPA_MAX_UTTERANCE_SECONDS

    comando_audio = [
        "parec",
        "--device", microfono,
        "--rate", str(SAMPLE_RATE),
        "--channels", "1",
        "--format", "s16le",
    ]

    proceso = None
    pre_roll = deque(maxlen=SHERPA_PRE_ROLL_FRAMES)
    frames = []
    voz_iniciada = False
    frames_altos = 0
    frames_silencio = 0
    inicio_espera = time.time()
    inicio_frase = None

    try:
        proceso = subprocess.Popen(
            comando_audio,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

        while True:
            data = proceso.stdout.read(SHERPA_CHUNK_BYTES)

            if not data:
                return None

            rms = calcular_rms_pcm(data)

            if not voz_iniciada:
                pre_roll.append(data)

                if rms >= SHERPA_RMS_THRESHOLD:
                    frames_altos += 1
                else:
                    # Una caída aislada no borra por completo el intento.
                    # Ayuda cuando la voz empieza suave.
                    frames_altos = max(0, frames_altos - 1)

                if frames_altos >= SHERPA_START_FRAMES:
                    voz_iniciada = True
                    inicio_frase = time.time()
                    frames.extend(pre_roll)
                    pre_roll.clear()
                    frames_silencio = 0
                    if mostrar_deteccion:
                        print("Voz detectada...", end="\r")
                    continue

                if time.time() - inicio_espera >= tiempo_espera:
                    SILENCIO_VERIFICADO_SALUDO = True
                    return None

                continue

            frames.append(data)

            if rms >= SHERPA_RMS_THRESHOLD:
                frames_silencio = 0
            else:
                frames_silencio += 1

            if frames_silencio >= silencio_frames:
                break

            if inicio_frase and (
                time.time() - inicio_frase >= max_segundos
            ):
                break

        if not frames:
            return None

        return b"".join(frames)

    finally:
        if proceso:
            try:
                proceso.terminate()
                proceso.wait(timeout=1)
            except Exception:
                try:
                    proceso.kill()
                except Exception:
                    pass


def modo_solo_voz_sherpa(microfono):
    """
    Prueba segura: reconoce y muestra texto, sin ejecutar domótica,
    Ollama ni comandos del robot.
    """
    print("\n=== MODO DE PRUEBA SHERPA: SOLO VOZ ===")
    print("No se ejecutarán acciones del robot.")
    print("Habla de forma normal. Ctrl + C para salir.\n")

    try:
        while True:
            audio = capturar_frase_inteligente(microfono)

            if not audio:
                continue

            if (
                not SILERO_VAD_ACTIVO
                and not audio_sherpa_confiable(audio)
            ):
                print("\nAudio descartado: no hubo suficiente voz real.\n")
                continue

            print("\nProcesando con Sherpa...")
            texto = reconocer_sherpa_pcm(audio)

            if texto:
                print(f"Sherpa entendió: {texto}")
                print(f"Normalizado: {corregir_texto_vosk(texto)}\n")
            else:
                print("Sherpa descartó ruido, silencio o una frase poco confiable.\n")

    except KeyboardInterrupt:
        print("\nPrueba Sherpa finalizada.")
        return


def respuesta_requiere_contestacion(respuesta):
    """Detecta una invitación real a contestar al final de la respuesta.

    El criterio es conservador: una pregunta explicada por el propio robot
    o una pregunta citada no mantiene abierta la escucha. También acepta
    peticiones directas como 'Cuéntame qué pasó' o 'Repítelo más claro'.
    No hace una segunda consulta a Ollama ni altera lo que se pronuncia.
    """
    if not respuesta:
        return False

    original = respuesta.strip()
    # Retirar preguntas citadas (incluidos ejemplos entre comillas), no
    # solo comprobar la presencia de un signo de interrogación cualquiera.
    sin_citas = re.sub(r'"[^"\n]*"|“[^”]*”|«[^»]*»|‘[^’]*’', ' ', original)
    sin_citas = re.sub(r"'[^'\n]*'", ' ', sin_citas).strip()
    if not sin_citas:
        return False

    # La pregunta debe quedar pendiente al terminar de hablar. No se abre
    # un turno si después de la pregunta el robot dio su propia respuesta.
    if sin_citas.rstrip().endswith("?"):
        pregunta = re.split(r"[.!?]+", sin_citas.rstrip()[:-1])[-1]
        pregunta = normalizar_texto(pregunta).strip(" ¿?,;:!\t\n")
        if pregunta:
            # Ejemplos educativos y preguntas retóricas habituales.
            if re.search(
                r"\b(?:por ejemplo|un ejemplo|la pregunta es|la pregunta seria|"
                r"pregunta retorica|quien sabe|quien lo diria)\b", pregunta
            ):
                return False
            return True

    # Peticiones de respuesta que no llevan signos de interrogación.
    # Analizar únicamente la última oración, no las instrucciones citadas
    # o mencionadas dentro de una explicación anterior.
    ultimo = re.split(r"[.!?]+", sin_citas.rstrip(".!? "))[-1]
    ultimo = normalizar_texto(ultimo).strip(" ,;:¿¡\t\n")
    return bool(re.match(
        r"^(?:(?:y|ahora|por favor|entonces)\s*[,;:]?\s+)*"
        r"(?:cuentame|dime|dimelo|dinos|explicame|aclarame|respondeme|"
        r"responde|contestame|contesta|repite|repitelo|especifica|elige|"
        r"indicame|confirmame|confirma|hazme saber|dejame saber|"
        r"te escucho|(?:espero|necesito) (?:tu respuesta|que me (?:digas|confirmes|aclares)))\b",
        ultimo
    ))


def estado_despues_de_responder(respuesta):
    """Devuelve modo activo y su plazo, solo si se espera una contestación."""
    actualizar_dialogo_archivos(respuesta, CONVERSACION_CONTINUA and ULTIMA_VOZ_COMPLETA,
                                CONVERSACION_ESPERA_SEGUNDOS)
    actualizar_dialogo_camara(respuesta, CONVERSACION_CONTINUA and ULTIMA_VOZ_COMPLETA,
                              CONVERSACION_ESPERA_SEGUNDOS)
    actualizar_dialogo_investigacion(respuesta, CONVERSACION_CONTINUA and ULTIMA_VOZ_COMPLETA,
                                    CONVERSACION_ESPERA_SEGUNDOS)
    if (CONVERSACION_CONTINUA and ULTIMA_VOZ_COMPLETA
            and respuesta_requiere_contestacion(respuesta)):
        print(f"Charsbotai espera tu respuesta ({CONVERSACION_ESPERA_SEGUNDOS:g} s). "
              "No necesitas repetir 'robot'.\n")
        return True, time.monotonic() + CONVERSACION_ESPERA_SEGUNDOS

    print("Charsbotai volvió al modo espera.\n")
    return False, None


def saludos_listos_para_sondeo(activo):
    return (callable(PROVEEDOR_PROXIMIDAD_SALUDOS) and CONTROL_SALUDOS.habilitado
            and not activo and not APAGANDO and not sleeping_sent
            and ULTIMA_EMOCION_SALUDO[0] not in {"sleeping", "thinking", "speaking", "listening"}
            and ULTIMA_EMOCION_SALUDO[1] != "manual")


def revisar_saludo_en_silencio(activo):
    # Solo hilo principal, después de una captura que terminó por silencio.
    # Nunca usar un hilo de TTS que compita con Sherpa o con una conversación.
    if not SILENCIO_VERIFICADO_SALUDO or not saludos_listos_para_sondeo(activo):
        CONTROL_SALUDOS.reiniciar_muestras()
        return
    try:
        visual = consultar_muestra_saludo()
        distancia = PROVEEDOR_PROXIMIDAD_SALUDOS()
        if not CONTROL_SALUDOS.evaluar(visual, distancia, time.monotonic(), True):
            return
    except Exception:
        CONTROL_SALUDOS.reiniciar_muestras()
        return
    saludo = "Hola, qué gusto verte hoy."
    print(f"[Saludo autónomo] {saludo}")
    # No abrir ventana de conversación ni pedir respuesta; no usar Ollama.
    hablar(saludo, aplicar_emocion_final=False)
    if ULTIMA_VOZ_COMPLETA:
        enviar_emocion("happy", modo="natural", duracion_ms=NATURAL_EMOTION_DURATION_MS)


def main():
    global MICROFONO_ACTUAL, APAGANDO

    solo_voz = "--solo-voz" in sys.argv

    if solo_voz:
        print("Charsbotai Sherpa iniciado en modo de prueba de voz.")
    else:
        print("Charsbotai iniciado en modo voz con Sherpa.")
        print("Di 'robot' u 'oye robot' para activarme.")
        print("También puedes decir: 'Robot abre la puerta'.")
        print("Di 'salir' para cerrar.\n")

    if not verificar_sherpa():
        return

    # Preferimos la API Python porque mantiene el modelo cargado.
    # Si falla, reconocer_sherpa_pcm conserva el CLI anterior como respaldo.
    cargar_reconocedor_sherpa()

    microfono = seleccionar_microfono()

    if not microfono:
        print("\nNo encontré ningún micrófono disponible.")
        print("Revisa con:")
        print("pactl list short sources")
        return

    MICROFONO_ACTUAL = microfono

    print(f"\nUsando micrófono: {microfono}")
    print("Detección de voz: Silero VAD si el modelo está disponible.")

    if solo_voz:
        modo_solo_voz_sherpa(microfono)
        return

    # Prepara los componentes antes de la conversación.
    precargar_ollama()
    configurar_cancelacion_eco(microfono)

    # Vosk pequeño:
    # - detecta "robot" en espera,
    # - detecta "cállate/para" mientras Charsbotai habla.
    cargar_detector_interrupcion()

    # Silero filtra voz humana antes de cualquier reconocimiento de contenido.
    cargar_vad_silero()

    conectar_socket()

    modo_actual = obtener_modo_robot()

    if modo_actual == "iot":
        print("Modo detectado: conectado al ESP32D.\n")
    elif modo_actual == "backend":
        print("Modo detectado: backend activo, pero ESP32D no conectado.\n")
    else:
        print("Modo detectado: local, sin sistema domótico conectado.\n")

    hablar("Charsbotai iniciado.")

    activo = False
    limite_continuacion = None

    try:
        print("[Visión] " + iniciar_vision_al_arrancar())
        iniciar_control_escritorio()
        print("\nEscuchando. En modo espera solo responderé cuando digas \"robot\".\n")

        while True:
            # La llamada simple "robot" conserva su comportamiento previo.
            # El plazo se aplica únicamente a una conversación de seguimiento.
            tiempo_espera = None
            if limite_continuacion is not None:
                restante = limite_continuacion - time.monotonic()
                if restante <= 0:
                    activo = False
                    limite_continuacion = None
                    print("Tiempo de respuesta agotado. Di 'robot' para activarme.\n")
                else:
                    tiempo_espera = min(SHERPA_WAIT_FOR_VOICE_SECONDS, restante)

            revisar_inactividad(activo)

            # Solo acortar el sondeo cuando exista telemetría real integrada.
            if saludos_listos_para_sondeo(activo):
                tiempo_espera = 1.0
            audio = capturar_frase_inteligente(microfono, tiempo_espera=tiempo_espera)

            if not audio:
                revisar_saludo_en_silencio(activo)
                continue
            CONTROL_SALUDOS.reiniciar_muestras()  # Prioridad absoluta a la voz humana.

            # Con Silero ya sabemos que existe voz humana.
            # Solo en el modo de respaldo RMS aplicamos el filtro anterior.
            if (
                not SILERO_VAD_ACTIVO
                and not audio_sherpa_confiable(audio)
            ):
                continue

            wake_confirmado = activo
            texto_wake = ""

            # ----------------------------------------------------------
            # MODO ESPERA
            # ----------------------------------------------------------
            if not activo and VOSK_STOP_MODEL is not None:
                wake_confirmado, texto_wake = detectar_llamada_vosk(audio)

                if not wake_confirmado:
                    # Hubo voz humana, pero nadie llamó al robot.
                    # Se ignora sin mandar nada a Sherpa ni a Ollama.
                    continue

                print("\nLlamada 'robot' detectada.")

            print("\nProcesando voz con Sherpa...")
            texto = reconocer_sherpa_pcm(audio)

            if not texto:
                # Si Vosk sí oyó "robot" pero Canary no obtuvo una frase,
                # tratamos el turno como una llamada simple.
                if not activo and wake_confirmado:
                    activo = True
                    marcar_interaccion()
                    enviar_emocion("listening")
                    respuesta = "Te escucho."
                    print(f"Charsbotai: {respuesta}\n")
                    hablar(respuesta)

                continue

            texto_normalizado = corregir_texto_vosk(texto)

            # Respaldo si Vosk no está disponible.
            if not activo and VOSK_STOP_MODEL is None:
                wake_confirmado = detectar_llamada_en_texto(texto)

                if not wake_confirmado:
                    continue

            print(f"Tú dijiste: {texto}")
            marcar_interaccion()

            if texto_normalizado in [
                "salir", "cerrar", "terminar", "exit", "quit"
            ]:
                print("Charsbotai: Hasta luego.")
                hablar("Hasta luego.")
                break

            # ----------------------------------------------------------
            # Ya se confirmó que llamaron al robot.
            # ----------------------------------------------------------
            if not activo:
                mensaje_directo = extraer_mensaje_despues_wake_word(texto)

                # Canary puede perder la palabra "robot", pero ahora esto
                # es seguro porque Vosk ya confirmó la llamada en el audio.
                if mensaje_directo is None and wake_confirmado:
                    mensaje_directo = texto_normalizado

                # Dijeron únicamente "robot".
                if mensaje_directo == "":
                    activo = True
                    enviar_emocion("listening")
                    respuesta = "Te escucho."
                    print(f"Charsbotai: {respuesta}\n")
                    hablar(respuesta)
                    continue

                if mensaje_directo is None:
                    continue

                print("Charsbotai: Pensando...")
                enviar_emocion("thinking")

                respuesta, ya_fue_hablada = procesar_mensaje_con_streaming(
                    mensaje_directo
                )

                if not ya_fue_hablada:
                    print(f"Charsbotai: {respuesta}\n")
                    hablar(respuesta)

                activo, limite_continuacion = estado_despues_de_responder(respuesta)
                continue

            # ----------------------------------------------------------
            # Ya estaba activo porque antes dijeron solamente "robot"
            # o porque Charsbotai estaba esperando una contestación.
            # ----------------------------------------------------------
            print("Charsbotai: Pensando...")
            enviar_emocion("thinking")

            # En la continuación también se permite repetir el nombre,
            # pero solo se retira si aparece al principio de la frase.
            mensaje_activo = texto
            if limite_continuacion is not None:
                texto_inicio = texto_normalizado.lstrip(" ,.:;¿?¡!")
                for palabra in sorted(WAKE_WORDS, key=len, reverse=True):
                    coincidencia = re.match(rf"{re.escape(normalizar_texto(palabra))}\b", texto_inicio)
                    if coincidencia:
                        resto = texto_inicio[coincidencia.end():].lstrip(" ,.:;¿?¡!")
                        if resto:
                            mensaje_activo = resto
                        break
            respuesta, ya_fue_hablada = procesar_mensaje_con_streaming(
                mensaje_activo
            )

            if not ya_fue_hablada:
                print(f"Charsbotai: {respuesta}\n")
                hablar(respuesta)

            activo, limite_continuacion = estado_despues_de_responder(respuesta)

    except KeyboardInterrupt:
        APAGANDO = True
        print("\nCharsbotai detenido.")

    except Exception as e:
        if not APAGANDO:
            print("\nOcurrió un error en el modo voz Sherpa:")
            print(e)

    finally:
        APAGANDO = True
        cerrar_control_escritorio()
        cerrar_camara()
        liberar_cancelacion_eco()
        desconectar_socket_silenciosamente()

if __name__ == "__main__":
    main()
    