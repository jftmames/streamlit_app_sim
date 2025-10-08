# streamlit_app_sim.py
import streamlit as st
import pandas as pd
import numpy as np
import json, hashlib, math, random
from datetime import datetime, timedelta, timezone

st.set_page_config(page_title="Cold Chain IoT — Simulación guiada", layout="wide")

# --------------------------
# Utilidades de simulación
# --------------------------
def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        return "0"*64
    lvl = [hashlib.sha256(l.encode()).hexdigest() for l in leaves]
    while len(lvl) > 1:
        if len(lvl) % 2 == 1:
            lvl.append(lvl[-1])
        nxt = []
        for i in range(0, len(lvl), 2):
            nxt.append(hashlib.sha256((lvl[i]+lvl[i+1]).encode()).hexdigest())
        lvl = nxt
    return lvl[0]

def generate_stream(n=120, freq_hz=1.0, with_spikes=False, drift_sec=0.0, attacker_rate=0.0, seed=42):
    """Genera telemetría sintética multi-dispositivo en memoria (publisher)."""
    rng = random.Random(seed)
    rows = []
    devices = [
        ("truck-001","shp-101","dev-a"),
        ("truck-002","shp-202","dev-b"),
        ("attacker","xxx","dev-x")
    ]
    t0 = datetime.now(timezone.utc)
    for i in range(n):
        for (truck, shp, dev) in devices:
            # pérdida aleatoria leve para simular huecos
            if rng.random() < 0.03:
                continue
            base_temp = 5.0 + 0.2*math.sin(i/20.0) + rng.uniform(-0.1, 0.1)
            base_hum  = 75.0 + 0.8*math.sin(i/18.0) + rng.uniform(-0.3, 0.3)
            lat = 40.4168 + rng.uniform(-0.0005,0.0005) + i*1e-5
            lon = -3.7038 + rng.uniform(-0.0005,0.0005) - i*1e-5

            temp = base_temp
            hum  = base_hum
            # picos en dev-b si with_spikes
            if with_spikes and dev=="dev-b" and (i % 35 == 0):
                temp += 2.0; hum += 1.0

            ts = t0 + timedelta(seconds=i/freq_hz)
            if dev=="dev-b":
                ts += timedelta(seconds=drift_sec)

            msg = {
                "device_id": dev,
                "ts": ts.isoformat(),
                "temperature_c": round(temp,2),
                "humidity": round(hum,2),
                "gps": {"lat": round(lat,6),"lon": round(lon,6)},
                "truck_id": truck,
                "shipment_id": shp
            }

            valid = True
            reason = None
            # ataques de schema en dev-x
            if dev=="dev-x" and rng.random() < attacker_rate:
                msg["device_id"] = "ATTACKER!!"
                msg.pop("gps", None)
                valid = False
                reason = "schema-invalid"

            rows.append({**msg, "_valid": valid, "_reason": reason})
    df = pd.DataFrame(rows)
    return df.sort_values("ts").reset_index(drop=True)

def validate_schema(row):
    # versión didáctica (no jsonschema): rangos+patrones principales.
    import re
    try:
        ok = True; reason=None
        if not isinstance(row["temperature_c"], (int,float)): ok=False; reason="temp-type"
        if not (0 <= row["temperature_c"] < 12.01): ok=False; reason="temp-range"
        if not (60 <= row["humidity"] <= 95): ok=False; reason="hum-range"
        if not isinstance(row["gps"], dict): ok=False; reason="gps-missing"
        else:
            lat = row["gps"].get("lat", None); lon = row["gps"].get("lon", None)
            if lat is None or lon is None: ok=False; reason="gps-keys"
            elif not (-90 <= lat <= 90 and -180 <= lon <= 180): ok=False; reason="gps-range"
        pat = re.compile(r"^[a-z0-9\-]{3,32}$")
        if not pat.match(str(row["device_id"])): ok=False; reason="device-pattern"
        if not pat.match(str(row["truck_id"])): ok=False; reason="truck-pattern"
        if not re.match(r"^shp-\d{3,6}$", str(row["shipment_id"])): ok=False; reason="shipment-pattern"
        # coherencia: si T>10 ⇒ HR>=70
        if row["temperature_c"] > 10 and row["humidity"] < 70: ok=False; reason="coherence"
        return ok, reason
    except Exception as e:
        return False, f"exception:{e}"

def detect_alerts(df, k=5, thresh=8.0, hysteresis=0.5):
    """Emula lógica de consumer: ventana K + histeresis + replay simple (monotonicidad)."""
    alerts = []
    state = {}
    for idx, r in df.iterrows():
        dev = r["device_id"]
        state.setdefault(dev, {"over":0, "alert":False, "last_ts":None})
        ts = pd.to_datetime(r["ts"])
        # replay
        if state[dev]["last_ts"] is not None and ts <= state[dev]["last_ts"]:
            alerts.append({"ts": r["ts"], "device_id": dev, "type": "replay", "reason": "non-monotonic ts"})
        state[dev]["last_ts"] = ts

        # umbral con histeresis
        th_on = thresh
        th_off = thresh - hysteresis
        t = r["temperature_c"]
        if state[dev]["alert"]:
            if t <= th_off:
                state[dev]["alert"] = False
        else:
            state[dev]["over"] = state[dev]["over"] + 1 if t >= th_on else 0
            if state[dev]["over"] >= k:
                state[dev]["alert"] = True
                alerts.append({"ts": r["ts"], "device_id": dev, "type": "threshold",
                               "reason": f"T>={th_on} for {k} readings"})
    return pd.DataFrame(alerts)

def build_epochs_from_df(df, epoch_seconds=60):
    """Construye epochs y Merkle desde un DataFrame (sin archivos)."""
    if df.empty: 
        return pd.DataFrame(columns=["epoch_id","start_ts","end_ts","count","merkle_root","prev_root"])
    dfx = df.copy()
    dfx["ts_dt"] = pd.to_datetime(dfx["ts"])
    dfx = dfx.sort_values("ts_dt")
    epochs = []
    prev_root = "0"*64
    start = dfx["ts_dt"].iloc[0]
    bucket = []

    def flush(ep_start, items, prev):
        leaves = [canon({k:v for k,v in r.items() if not k.startswith("_")}) for r in items]
        root = merkle_root(leaves)
        return {
            "epoch_id": ep_start.isoformat(),
            "start_ts": items[0]["ts"],
            "end_ts": items[-1]["ts"],
            "count": len(items),
            "merkle_root": root,
            "prev_root": prev
        }, root

    for _, r in dfx.iterrows():
        if (r["ts_dt"] - start).total_seconds() < epoch_seconds:
            bucket.append(r)
        else:
            if bucket:
                ep, prev_root = flush(start, bucket, prev_root)
                epochs.append(ep)
            start = r["ts_dt"]; bucket = [r]
    if bucket:
        ep, prev_root = flush(start, bucket, prev_root)
        epochs.append(ep)
    return pd.DataFrame(epochs)

def verify_epochs(df, epochs_df):
    """Recalcula y compara merkle+encadenamiento."""
    if epochs_df.empty: 
        return "OK", None, None
    prev = "0"*64
    for _, ep in epochs_df.iterrows():
        start = pd.to_datetime(ep["start_ts"]); end = pd.to_datetime(ep["end_ts"])
        bucket = df[(pd.to_datetime(df["ts"])>=start) & (pd.to_datetime(df["ts"])<=end)]
        leaves = [canon({k:v for k,v in r.items() if not k.startswith("_")}) for _,r in bucket.iterrows()]
        calc = merkle_root(leaves) if len(leaves)>0 else "0"*64
        if calc != ep["merkle_root"]:
            return "FAIL", ep["epoch_id"], "merkle_mismatch"
        if ep["prev_root"] != prev:
            return "FAIL", ep["epoch_id"], "prev_root_chain"
        prev = ep["merkle_root"]
    return "OK", None, None

# --------------------------
# UI
# --------------------------
st.title("Cold Chain IoT — Simulación guiada (end-to-end, sin archivos)")

with st.sidebar:
    st.header("Controles de simulación")
    n_msgs = st.slider("Mensajes totales por dispositivo", 60, 600, 180, 20)
    freq = st.slider("Frecuencia (Hz)", 0.2, 5.0, 1.0, 0.2)
    spikes = st.checkbox("Inyectar picos en dev-b", value=True)
    drift = st.slider("Desfase de reloj dev-b (segundos)", 0.0, 10.0, 3.0, 0.5)
    badrate = st.slider("Tasa de mensajes inválidos dev-x", 0.0, 0.6, 0.2, 0.05)
    st.caption("Dev-a normal, dev-b con picos/drift, dev-x atacante (schema inválido).")
    st.divider()
    th = st.number_input("Umbral T (°C)", value=8.0, step=0.5)
    k = st.number_input("Consecutivas K", value=5, step=1)
    hyst = st.number_input("Histeresis", value=0.5, step=0.1)

# Estado de sesión
if "df" not in st.session_state:
    st.session_state.df = pd.DataFrame()
if "epochs" not in st.session_state:
    st.session_state.epochs = pd.DataFrame()

colA, colB, colC, colD = st.columns(4)
if colA.button("1) Generar datos (Publisher)"):
    st.session_state.df = generate_stream(n=n_msgs, freq_hz=freq, with_spikes=spikes, drift_sec=drift, attacker_rate=badrate)
    st.session_state.epochs = pd.DataFrame()
if colB.button("2) Validar (Schema) y separar cuarentena"):
    if st.session_state.df.empty:
        st.warning("Genera datos primero.")
    else:
        val = st.session_state.df.apply(validate_schema, axis=1, result_type="expand")
        st.session_state.df["_valid2"] = val[0]
        st.session_state.df["_reason2"] = val[1]
if colC.button("3) Procesar (Consumer): umbral+replay+histeresis"):
    if st.session_state.df.empty:
        st.warning("Genera datos primero.")
    else:
        df_ok = st.session_state.df[(st.session_state.df.get("_valid2", True)==True)]
        st.session_state.alerts = detect_alerts(df_ok, k=int(k), thresh=float(th), hysteresis=float(hyst))
if colD.button("4) Construir epochs (Merkle) y verificar"):
    if st.session_state.df.empty:
        st.warning("Genera/valida/procesa primero.")
    else:
        df_ok = st.session_state.df[(st.session_state.df.get("_valid2", True)==True)]
        st.session_state.epochs = build_epochs_from_df(df_ok, epoch_seconds=60)
        status, ep, reason = verify_epochs(df_ok, st.session_state.epochs)
        st.session_state.verify = (status, ep, reason)

st.divider()

# Panel 1: Datos y validación
st.subheader("A) Datos simulados y validación de esquema")
if st.session_state.df.empty:
    st.info("Pulsa **1) Generar datos** para ver el stream sintético (3 dispositivos).")
else:
    df = st.session_state.df.copy()
    col1, col2, col3 = st.columns([2,1,1])
    col1.dataframe(df.head(10))
    if "_valid2" in df.columns:
        invalid_pct = 100.0 * (len(df[df["_valid2"]==False]) / max(len(df),1))
        col2.metric("Mensajes totales", f"{len(df)}")
        col3.metric("% inválidos (schema)", f"{invalid_pct:.1f}%")
        st.caption("Las filas inválidas simulan cuarentena. No paran el sistema.")
    else:
        st.caption("Aún no has validado. Pulsa **2) Validar (Schema)**.")

# Panel 2: Series y alertas
st.subheader("B) Series por dispositivo y alertas (umbral + histeresis + replay)")
if st.session_state.df.empty:
    st.info("Genera y valida. Luego pulsa **3) Procesar**.")
else:
    df_ok = st.session_state.df[(st.session_state.df.get("_valid2", True)==True)].copy()
    df_ok["ts"] = pd.to_datetime(df_ok["ts"])
    devices = sorted(df_ok["device_id"].unique().tolist())
    sel = st.selectbox("Dispositivo", ["(todos)"]+devices, index=0)
    plot_df = df_ok if sel=="(todos)" else df_ok[df_ok["device_id"]==sel]
    st.line_chart(plot_df.set_index("ts")[["temperature_c","humidity"]])
    if "alerts" in st.session_state and not st.session_state.alerts.empty:
        st.dataframe(st.session_state.alerts.sort_values("ts", ascending=False))
    else:
        st.caption("Aún no hay alertas registradas (o no has pulsado **3) Procesar**).")

# Panel 3: Integridad por epochs/Merkle
st.subheader("C) Integridad: epochs + árbol de Merkle")
if "epochs" in st.session_state and not st.session_state.epochs.empty:
    e = st.session_state.epochs
    st.dataframe(e.tail(5))
    if "verify" in st.session_state:
        status, ep, reason = st.session_state.verify
        if status=="OK":
            st.success("Verificación OK")
        else:
            st.error(f"FAIL en epoch {ep}: {reason}")
else:
    st.caption("Pulsa **4) Construir epochs** para generar raíces Merkle y verificar.")

# Panel 4: Experimentos guiados
st.subheader("D) Experimentos guiados (provocar fallos)")
c1, c2, c3 = st.columns(3)
if c1.button("Inyectar pico (dev-b)"):
    if not st.session_state.df.empty:
        df = st.session_state.df
        mask = (df["device_id"]=="dev-b")
        pick = df[mask].sample(max(1,len(df[mask])//10), random_state=0).index
        st.session_state.df.loc[pick, "temperature_c"] += 3.0
        st.success("Picos inyectados. Reprocesa alertas (paso 3).")
if c2.button("Simular replay (ordenar mal ts en dev-a)"):
    if not st.session_state.df.empty:
        df = st.session_state.df
        mask = (df["device_id"]=="dev-a")
        if mask.sum()>5:
            # intercambia dos timestamps para romper monotonicidad
            idxs = df[mask].sample(2, random_state=1).index.tolist()
            st.session_state.df.loc[idxs, ["ts"]] = st.session_state.df.loc[idxs[::-1], ["ts"]].values
            st.warning("Replay simulado. Reprocesa alertas (paso 3).")
if c3.button("Romper integridad (modificar dato antiguo)"):
    if not st.session_state.df.empty:
        df = st.session_state.df
        old = df.sample(1, random_state=2).index[0]
        st.session_state.df.loc[old, "temperature_c"] = float(st.session_state.df.loc[old, "temperature_c"]) + 5.5
        st.info("Dato manipulado. Reconstruye/verifica epochs (paso 4) y debería fallar.")
