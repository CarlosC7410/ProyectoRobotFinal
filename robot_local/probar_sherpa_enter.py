import os
import signal
import subprocess
import time

from sherpa_asr import reconocer_archivo_sherpa


BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AUDIO_PRUEBA = os.path.join(BASE_DIR, "robot_local", "ultima_prueba_sherpa.wav")


def grabar_hasta_enter():
    if os.path.exists(AUDIO_PRUEBA):
        try:
            os.remove(AUDIO_PRUEBA)
        except Exception:
            pass

    comando = [
        "parecord",
        "--channels=1",
        "--rate=16000",
        "--format=s16le",
        "--file-format=wav",
        AUDIO_PRUEBA,
    ]

    print()
    input("Presiona ENTER para empezar a grabar...")
    print("Grabando. Habla ahora.")
    print("Cuando termines de hablar, presiona ENTER otra vez.")

    proceso = subprocess.Popen(
        comando,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    input()

    try:
        proceso.send_signal(signal.SIGINT)
        proceso.wait(timeout=2)
    except Exception:
        proceso.terminate()
        try:
            proceso.wait(timeout=2)
        except Exception:
            proceso.kill()

    time.sleep(0.5)

    return AUDIO_PRUEBA


def main():
    print("Prueba Sherpa tipo push-to-talk.")
    print("Esto evita que Sherpa procese mucho silencio.")
    print("Presiona Ctrl + C para salir.")

    try:
        while True:
            ruta = grabar_hasta_enter()

            print("Reproduciendo audio grabado...")
            subprocess.run(["paplay", ruta])

            print("Procesando con Sherpa...")
            texto = reconocer_archivo_sherpa(ruta)

            if texto:
                print("Sherpa entendió:")
                print(texto)
            else:
                print("No se detectó texto válido.")

            print("Audio guardado en:")
            print(ruta)
            print("-" * 50)

    except KeyboardInterrupt:
        print()
        print("Prueba finalizada.")


if __name__ == "__main__":
    main()
