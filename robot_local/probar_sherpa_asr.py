from sherpa_asr import escuchar_sherpa, verificar_sherpa


def main():
    if not verificar_sherpa():
        return

    print("Prueba rápida de Sherpa para Charsbotai.")
    print("Habla cuando diga: Escuchando...")
    print("Presiona Ctrl + C para salir.")
    print()

    try:
        while True:
            print("Escuchando...")
            texto = escuchar_sherpa(segundos=6)

            if texto:
                print("Sherpa entendió:")
                print(texto)
            else:
                print("No se detectó texto.")

            print("-" * 50)

    except KeyboardInterrupt:
        print()
        print("Prueba finalizada.")


if __name__ == "__main__":
    main()
