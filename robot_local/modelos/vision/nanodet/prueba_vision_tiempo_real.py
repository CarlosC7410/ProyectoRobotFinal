import time
import cv2

from picamera2 import Picamera2
from nanodet import NanoDet
from demo import letterbox, vis

# ================================================================
# CONFIGURACIÓN DE LA PRUEBA
# ================================================================
ANCHO = 640
ALTO = 360
CONFIANZA = 0.45
VENTANA = "Carbotai - Vision artificial en tiempo real"

# Limitar el número de hilos del procesador.
cv2.setNumThreads(2)

print("Cargando NanoDet Plus...")

modelo = NanoDet(
    modelPath="object_detection_nanodet_2022nov.onnx",
    prob_threshold=CONFIANZA,
    backend_id=cv2.dnn.DNN_BACKEND_OPENCV,
    target_id=cv2.dnn.DNN_TARGET_CPU
)

print("Inicializando la camara Arducam...")

camara = Picamera2()

configuracion = camara.create_preview_configuration(
    main={
        "size": (ANCHO, ALTO),
        "format": "RGB888"
    },
    buffer_count=3
)

camara.configure(configuracion)

try:
    camara.start()
    time.sleep(1)

    cv2.namedWindow(VENTANA, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(VENTANA, ANCHO, ALTO)

    print("Vision artificial activa.")
    print("Presiona Q o ESC para finalizar.")

    while True:
        inicio = time.perf_counter()

        # Capturar imagen desde Picamera2.
        imagen = camara.capture_array()

        # Preparar imagen para NanoDet.
        imagen_rgb = cv2.cvtColor(
            imagen, cv2.COLOR_BGR2RGB
        )

        entrada, escala = letterbox(imagen_rgb)

        # Ejecutar deteccion.
        detecciones = modelo.infer(entrada)

        # Dibujar recuadros y nombres.
        resultado = vis(detecciones, imagen, escala)

        # Medir rendimiento aproximado.
        duracion = time.perf_counter() - inicio
        fps = 1.0 / max(duracion, 0.001)

        cv2.putText(
            resultado,
            f"FPS: {fps:.1f}",
            (10, 25),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 255, 255),
            2
        )

        # Mostrar la imagen procesada.
        cv2.imshow(VENTANA, resultado)

        tecla = cv2.waitKey(1) & 0xFF

        if tecla in (ord("q"), 27):
            break

        # Detectar cierre manual de ventana.
        if cv2.getWindowProperty(
            VENTANA, cv2.WND_PROP_VISIBLE
        ) < 1:
            break

except KeyboardInterrupt:
    print("\nPrueba interrumpida.")

finally:
    print("Cerrando vision artificial...")
    camara.stop()
    camara.close()
    cv2.destroyAllWindows()
    print("Camara liberada correctamente.")
