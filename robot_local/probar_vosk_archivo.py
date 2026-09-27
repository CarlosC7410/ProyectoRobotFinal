import os
import json
import wave
from vosk import Model, KaldiRecognizer


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

VOSK_MODEL_PATH = os.path.join(
    BASE_DIR,
    "modelos",
    "vosk-model-small-es-0.42"
)

AUDIO_PATH = os.path.join(
    BASE_DIR,
    "modelos",
    "sherpa",
    "prueba_manual.wav"
)


def main():
    if not os.path.isdir(VOSK_MODEL_PATH):
        print("No encontré el modelo Vosk:")
        print(VOSK_MODEL_PATH)
        return

    if not os.path.isfile(AUDIO_PATH):
        print("No encontré el audio:")
        print(AUDIO_PATH)
        return

    print("Modelo Vosk:")
    print(VOSK_MODEL_PATH)
    print()

    print("Audio:")
    print(AUDIO_PATH)
    print()

    wf = wave.open(AUDIO_PATH, "rb")

    if wf.getnchannels() != 1:
        print("El audio no es mono.")
        return

    if wf.getsampwidth() != 2:
        print("El audio no es de 16 bits.")
        return

    if wf.getframerate() != 16000:
        print("El audio no está en 16000 Hz.")
        print("Frecuencia detectada:", wf.getframerate())
        return

    model = Model(VOSK_MODEL_PATH)
    recognizer = KaldiRecognizer(model, 16000)

    while True:
        data = wf.readframes(4000)

        if len(data) == 0:
            break

        recognizer.AcceptWaveform(data)

    resultado = json.loads(recognizer.FinalResult())
    texto = resultado.get("text", "").strip()

    print("Vosk entendió:")
    print(texto)


if __name__ == "__main__":
    main()
    