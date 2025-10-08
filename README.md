# Cold Chain IoT — Simulación Guiada (100% Web)

Demostración didáctica **sin archivos ni procesos externos**. Todo corre en un único `streamlit_app.py`:
- Generación de datos sintéticos (3 dispositivos).
- Validación de esquema (rango/patrones/coherencia) y “cuarentena” simulada.
- Detección de incidentes (umbral + **histeresis** + **replay**).
- Integridad por **epochs** con **árbol de Merkle** (verificación localizada).
- Botones para **provocar fallos** (picos, replay, tamper) y aprender capa a capa.

## 🚀 Cómo desplegar (Streamlit Community Cloud)
1. Sube estos archivos a **GitHub** (este repo).
2. Entra a https://share.streamlit.io → **New app**.
3. Selecciona tu cuenta → este repositorio → rama `main` → archivo `streamlit_app.py`.
4. Pulsa **Deploy**. Obtendrás una **URL pública**.

## 🧭 Uso en la app
1. Sidebar: ajusta parámetros (nº de mensajes, Hz, picos, drift, tasa de inválidos).
2. Botón **1)** Genera datos.
3. Botón **2)** Valida esquema (ver % inválidos).
4. Botón **3)** Procesa (umbral+replay+histeresis).
5. Botón **4)** Construye/Verifica epochs (Merkle).
6. Experimentos: **Inyectar pico**, **Simular replay**, **Romper integridad** y repetir pasos 3–4.

## 📦 Requisitos
La propia plataforma instala desde `requirements.txt`.

## 🔎 Qué explica
- Por qué **validar** al principio.
- Cómo la **histeresis** evita “alertitis”.
- Qué es **replay** (timestamps no monótonos).
- Cómo **Merkle** localiza manipulaciones por epoch.

## ⚠️ Notas
- No usa archivos locales ni necesita terminal.
- Ideal para clase: todo ocurre con **botones** y en memoria.
