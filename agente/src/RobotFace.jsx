import { useEffect, useRef, useState } from "react";
import "./RobotFace.css";

const EMOTION_LABELS = {
  neutral: "Neutral",
  happy: "Feliz",
  excited: "Emocionado",
  sad: "Triste",
  surprised: "Sorprendido",
  thinking: "Pensando",
  speaking: "Hablando",
  listening: "Escuchando",
  sleeping: "Durmiendo",
  confused: "Confundido",
  angry: "Enojado",
  annoyed: "Serio molesto",
};

function Mouth({ emotion }) {
  if (emotion === "happy") {
    return (
      <svg className="mouth-svg mouth-happy" viewBox="0 0 190 110" aria-hidden="true">
        <path d="M 38 36 Q 95 92 152 36" />
      </svg>
    );
  }

  if (emotion === "excited") {
    return (
      <svg className="mouth-svg mouth-excited" viewBox="0 0 210 125" aria-hidden="true">
        <path d="M 34 36 Q 105 116 176 36" />
      </svg>
    );
  }

  if (emotion === "sad" || emotion === "angry") {
    return (
      <svg className="mouth-svg mouth-sad" viewBox="0 0 190 110" aria-hidden="true">
        <path d="M 38 78 Q 95 30 152 78" />
      </svg>
    );
  }

  if (emotion === "surprised") {
    return <div className="mouth-shape mouth-o" aria-hidden="true" />;
  }

  if (emotion === "speaking") {
    return <div className="mouth-shape mouth-speaking" aria-hidden="true" />;
  }

  if (emotion === "sleeping") {
    return <div className="mouth-shape mouth-sleep" aria-hidden="true" />;
  }

  if (emotion === "confused") {
    return <div className="mouth-shape mouth-confused-line" aria-hidden="true" />;
  }

  if (emotion === "annoyed") {
    return <div className="mouth-shape mouth-annoyed" aria-hidden="true" />;
  }

  return <div className="mouth-shape mouth-neutral" aria-hidden="true" />;
}

function Eye({ side, emotion }) {
  return (
    <div className={`eye ${side}-eye`}>
      <span className="eye-shine" />
    </div>
  );
}

function EmotionMarks({ emotion }) {
  if (emotion === "thinking") {
    return (
      <div className="thinking-dots" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    );
  }

  if (emotion === "sleeping") {
    return (
      <div className="sleeping-z" aria-hidden="true">
        <span>z</span>
        <span>Z</span>
        <span>Z</span>
      </div>
    );
  }

  if (emotion === "confused") {
    return (
      <div className="confused-marks" aria-hidden="true">
        <span className="dot">.</span>
        <span className="dot">.</span>
        <span className="dot">.</span>
        <span className="question">?</span>
      </div>
    );
  }

  return null;
}

function RobotFace({ emotion = "neutral", speaking = false, onClick }) {
  const pantallaRef = useRef(null);
  const [cursorOculto, setCursorOculto] = useState(false);

  // Mostrar el cursor al moverlo y ocultarlo tras 5 s de inactividad.
  // El temporizador no se reinicia al cambiar la emoción de la cara.
  useEffect(() => {
    const pantalla = pantallaRef.current;
    if (!pantalla) return;

    let temporizador;
    const mostrarCursor = (evento) => {
      // Posición del aviso junto al cursor, sin reiniciar las animaciones.
      if (evento) {
        pantalla.style.setProperty("--aviso-x", `${evento.clientX + 14}px`);
        pantalla.style.setProperty("--aviso-y", `${evento.clientY + 18}px`);
      }
      setCursorOculto(false);
      clearTimeout(temporizador);
      temporizador = setTimeout(() => setCursorOculto(true), 5000);
    };

    pantalla.addEventListener("mousemove", mostrarCursor);
    mostrarCursor();

    return () => {
      pantalla.removeEventListener("mousemove", mostrarCursor);
      clearTimeout(temporizador);
    };
  }, []);

  const currentEmotion = speaking ? "speaking" : emotion;
  const safeEmotion = EMOTION_LABELS[currentEmotion] ? currentEmotion : "neutral";

  return (
    <div
      ref={pantallaRef}
      className={`robot-screen ${safeEmotion}${cursorOculto ? " cursor-oculto" : ""}`}
      onClick={onClick}
      aria-description="Toca para hablar"
      aria-label={`Cara de Charsbotai: ${EMOTION_LABELS[safeEmotion]}`}
    >
      {/* Aviso propio: desaparece junto con el cursor, sin tooltip nativo. */}
      <span className="robot-aviso-hablar" aria-hidden="true">Toca para hablar</span>
      <div className="robot-face">
        <EmotionMarks emotion={safeEmotion} />

        <div className="eyes">
          <Eye side="left" emotion={safeEmotion} />
          <Eye side="right" emotion={safeEmotion} />
        </div>

        <div className="mouth-area">
          <Mouth emotion={safeEmotion} />
        </div>
      </div>
    </div>
  );
}

export default RobotFace;
