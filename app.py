import os
import io
import json
import base64
import platform
from datetime import datetime

import numpy as np
import streamlit as st
from PIL import Image
from streamlit_drawable_canvas import st_canvas
from openai import OpenAI

# Opcional (para leer en voz alta el análisis)
try:
    from gtts import gTTS
    TTS_AVAILABLE = True
except Exception:
    TTS_AVAILABLE = False

# ---------------- Configuración base ----------------
st.set_page_config(page_title="Tablero Inteligente", page_icon="🧠", layout="wide")
st.title("🧠 Tablero Inteligente · versión Pro")
st.caption(f"Versión de Python: {platform.python_version()}")

# ---------------- Barra lateral ----------------
with st.sidebar:
    st.subheader("🎛️ Lienzo")
    canvas_width  = st.slider("Ancho", 300, 1000, 560, 20)
    canvas_height = st.slider("Alto", 220, 800, 360, 20)

    stroke_color  = st.color_picker("Color de trazo", "#000000")
    stroke_width  = st.slider("Grosor de trazo", 1, 40, 6)
    fill_hex      = st.color_picker("Color de relleno", "#FFA500")
    fill_opacity  = st.slider("Opacidad del relleno", 0.0, 1.0, 0.25, 0.05)
    bg_color      = st.color_picker("Color de fondo", "#FFFFFF")

    st.divider()
    st.subheader("🧩 Guías")
    show_grid = st.toggle("Mostrar cuadrícula", value=False)
    grid_size = st.slider("Tamaño de celda", 10, 120, 30, 2, disabled=not show_grid)

    st.divider()
    st.subheader("📥 Cargar / Guardar")
    load_json    = st.file_uploader("Cargar JSON de anotaciones", type=["json"])
    enable_png   = st.checkbox("Permitir descarga PNG", value=True)
    enable_json  = st.checkbox("Permitir descarga JSON", value=True)

    st.divider()
    st.subheader("🤖 IA")
    api_key = st.text_input("Clave de OpenAI", type="password")
    analysis_lang = st.selectbox("Idioma del análisis", ["Español", "English"])
    prompt_style = st.selectbox(
        "Estilo de salida",
        ["Descripción breve", "Resumen con viñetas", "Crítica y mejoras", "Etiquetas (tags)"],
        index=1
    )
    detail_level = st.slider("Nivel de detalle", 1, 5, 3)
    temperature  = st.slider("Creatividad (temp.)", 0.0, 1.0, 0.3, 0.05)
    extra_context = st.text_area("Contexto / Pregunta adicional (opcional)", height=80)

    tts_out = st.checkbox("Leer en voz alta el análisis (gTTS)", value=False and TTS_AVAILABLE)
    if tts_out and not TTS_AVAILABLE:
        st.caption("gTTS no disponible en este entorno.")

# ---------------- Utilidades ----------------
def rgba_from_hex(hex_color: str, alpha: float) -> str:
    hex_color = hex_color.lstrip("#")
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:.3f})"

def make_grid_json(w: int, h: int, step: int, stroke="#E6E6E6"):
    objects = []
    for x in range(0, w, step):
        objects.append({
            "type": "line",
            "x1": x, "y1": 0, "x2": x, "y2": h,
            "stroke": stroke, "strokeWidth": 1,
            "selectable": False, "evented": False, "excludeFromExport": True
        })
    for y in range(0, h, step):
        objects.append({
            "type": "line",
            "x1": 0, "y1": y, "x2": w, "y2": y,
            "stroke": stroke, "strokeWidth": 1,
            "selectable": False, "evented": False, "excludeFromExport": True
        })
    return {"version": "4.6.0", "objects": objects}

def merge_fabric_json(a, b):
    if a is None and b is None: return None
    if a is None: return b
    if b is None: return a
    return {"version": b.get("version", "4.6.0"), "objects": (a.get("objects", []) + b.get("objects", []))}

def save_canvas_png(canvas_img, path="boceto.png") -> str:
    arr = np.array(canvas_img)
    pil_img = Image.fromarray(arr.astype("uint8"), "RGBA")
    pil_img.save(path)
    return path

def encode_image_to_base64(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception:
        return ""

def build_prompt(style: str, lang: str, level: int, extra: str) -> str:
    if lang.startswith("Esp"):
        base = "Analiza el boceto y devuelve"
        if style == "Descripción breve":
            goal = "una descripción breve y clara en español"
        elif style == "Resumen con viñetas":
            goal = "un resumen con viñetas (bullet points) en español, conciso y estructurado"
        elif style == "Crítica y mejoras":
            goal = "una crítica constructiva en español con 3–5 sugerencias de mejora"
        else:
            goal = "una lista de 5–10 etiquetas (tags) en español, separadas por comas"
        detail = f" Nivel de detalle: {level}/5."
        extra_q = f" Contexto adicional: {extra}" if extra.strip() else ""
        return f"{base} {goal}.{detail}{extra_q}"
    else:
        base = "Analyze the sketch and return"
        if style == "Descripción breve":
            goal = "a brief, clear description in English"
        elif style == "Resumen con viñetas":
            goal = "a concise, structured bullet list in English"
        elif style == "Crítica y mejoras":
            goal = "constructive critique in English with 3–5 improvement suggestions"
        else:
            goal = "a list of 5–10 tags in English, comma-separated"
        detail = f" Detail level: {level}/5."
        extra_q = f" Extra context: {extra}" if extra.strip() else ""
        return f"{base} {goal}.{detail}{extra_q}"

# ---------------- Lienzo + referencia opcional ----------------
left, right = st.columns([1.1, 1])

with left:
    grid_json = make_grid_json(canvas_width, canvas_height, grid_size) if show_grid else {"version": "4.6.0", "objects": []}
    initial_json = grid_json

    canvas = st_canvas(
        fill_color=rgba_from_hex(fill_hex, fill_opacity),
        stroke_width=stroke_width,
        stroke_color=stroke_color,
        background_color=bg_color,
        height=canvas_height,
        width=canvas_width,
        drawing_mode="freedraw",
        display_toolbar=True,
        initial_drawing=initial_json,
        key=f"canvas_{canvas_width}_{canvas_height}_{show_grid}_{grid_size}",
    )

with right:
    st.subheader("📌 Referencia (opcional)")
    ref_img = st.file_uploader("Sube una imagen de referencia (JPG/PNG)", type=["jpg", "jpeg", "png"])
    if ref_img:
        try:
            st.image(Image.open(ref_img), use_container_width=True)
        except Exception:
            st.warning("No se pudo abrir la imagen de referencia.")

    st.subheader("⚡ Acciones")
    c1, c2, c3 = st.columns(3)
    with c1:
        analyze = st.button("🔎 Analizar", type="primary", use_container_width=True)
    with c2:
        clear = st.button("🧽 Limpiar", use_container_width=True)
    with c3:
        rerender = st.button("🔁 Re-render", use_container_width=True)

    if clear:
        st.experimental_rerun()
    if rerender:
        st.experimental_rerun()

# ---------------- Análisis con IA ----------------
if analyze:
    if not api_key:
        st.warning("Ingresa tu clave de OpenAI en la barra lateral.")
    elif canvas.image_data is None:
        st.warning("Dibuja algo en el lienzo antes de analizar.")
    else:
        with st.spinner("Analizando tu boceto…"):
            try:
                path_png = save_canvas_png(canvas.image_data, "boceto.png")
                b64 = encode_image_to_base64(path_png)
                if not b64:
                    st.error("No se pudo procesar la imagen.")
                else:
                    prompt = build_prompt(prompt_style, analysis_lang, detail_level, extra_context)
                    client = OpenAI(api_key=api_key)
                    messages = [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {"type": "image_url",
                                 "image_url": {"url": f"data:image/png;base64,{b64}"}}
                            ],
                        }
                    ]
                    resp = client.chat.completions.create(
                        model="gpt-4o-mini",
                        messages=messages,
                        max_tokens=800,
                        temperature=temperature,
                    )
                    text = resp.choices[0].message.content if resp.choices else "(Sin respuesta)"
                    st.markdown("### 🗒️ Resultado")
                    st.write(text)

                    # Audio (opcional)
                    if tts_out and TTS_AVAILABLE:
                        try:
                            lang_code = "es" if analysis_lang.startswith("Esp") else "en"
                            tts = gTTS(text, lang=lang_code)
                            mp3_path = "analisis.mp3"
                            tts.save(mp3_path)
                            st.audio(open(mp3_path, "rb").read(), format="audio/mp3")
                        except Exception as e:
                            st.warning(f"No se pudo generar audio TTS: {e}")

                    # Descarga del informe
                    md = f"# Análisis del boceto\n\n**Fecha:** {datetime.now().isoformat(timespec='seconds')}\n\n**Prompt:** {prompt}\n\n---\n\n{text}\n"
                    st.download_button("⬇️ Descargar informe (Markdown)", data=md.encode("utf-8"),
                                       file_name="analisis_boceto.md", mime="text/markdown")

            except Exception as e:
                st.error(f"Ocurrió un error durante el análisis: {e}")

# ---------------- Exportaciones del lienzo ----------------
st.markdown("---")
exp_col1, exp_col2 = st.columns(2)

with exp_col1:
    if enable_png and canvas.image_data is not None:
        try:
            out = io.BytesIO()
            Image.fromarray(np.array(canvas.image_data).astype("uint8")).save(out, format="PNG")
            out.seek(0)
            st.download_button("⬇️ Descargar PNG del lienzo", data=out, file_name="boceto.png", mime="image/png")
        except Exception:
            st.warning("No se pudo exportar el PNG.")

with exp_col2:
    if enable_json and canvas.json_data is not None:
        try:
            js = json.dumps(canvas.json_data, ensure_ascii=False, indent=2)
            st.download_button("💾 Descargar JSON de anotaciones", data=js.encode("utf-8"),
                               file_name="anotaciones.json", mime="application/json")
        except Exception:
            st.warning("No se pudo exportar el JSON.")

# ---------------- Mensaje inicial ----------------
if canvas.image_data is None and not analyze:
    st.info("Dibuja en el lienzo de la izquierda. Puedes activar la cuadrícula para guiarte, "
            "y luego presiona **Analizar** para que la IA describa tu boceto.")
