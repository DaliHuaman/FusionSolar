"""Genera data.json para el dashboard web a partir del CSV maestro."""
from __future__ import annotations

import calendar
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import alarmas_historicas as ah
from . import disponibilidad as disp
from . import energia_exportada as ee
from .common import CAMPOS_BESS, STATE_DIR, leer_json

SITE_DIR = STATE_DIR / "site"
DIAS_SERIE = 45          # resolucion completa (cada lectura) en el dashboard
MAX_ALARMAS = 200
DIAS_SERIE_BESS = 90     # temperaturas BESS: 6 valores x 5 equipos x 48 lecturas/dia (~12 KB/dia); 90 dias cubre todo el historico disponible


def _bess(cfg: dict, filas: list[dict], ahora: datetime) -> dict:
    """Bloque "bess" de data.json. serie[id] = [[epoch, max_grupo, agua_bat_sum, agua_bat_ret, agua_pot_sum, agua_pot_ret, exterior], ...]"""
    bcfg = cfg.get("bess") or {}
    out: dict = {"activo": bool(bcfg.get("activo")), "planta": bcfg.get("planta"),
                 "campos": list(CAMPOS_BESS), "equipos": [{"id": e["id"], "nombre": e["nombre"]} for e in bcfg.get("equipos", [])],
                 "ultima": {}, "serie": {}}
    corte = (ahora - timedelta(days=DIAS_SERIE_BESS)).strftime("%Y-%m-%d %H:%M:%S")
    cols = list(CAMPOS_BESS.values())
    num = lambda v: None if v in ("", None) else round(float(v), 1)
    por_id: dict[str, list[dict]] = {}
    for f in sorted(filas, key=lambda f: f["Fecha_Hora"]):
        por_id.setdefault(f["BESS"], []).append(f)
    for bid, lst in por_id.items():
        ult = next((f for f in reversed(lst) if any(num(f.get(c)) is not None for c in cols)), None)
        if ult:
            out["ultima"][bid] = {"t": ult["Fecha_Hora"], **{k: num(ult.get(c)) for k, c in CAMPOS_BESS.items()}}
        out["serie"][bid] = [
            [calendar.timegm(datetime.strptime(f["Fecha_Hora"], "%Y-%m-%d %H:%M:%S").timetuple()), *[num(f.get(c)) for c in cols]]
            for f in lst if f["Fecha_Hora"] >= corte and any(num(f.get(c)) is not None for c in cols)
        ]
    return out


def _carga_descarga(cfg: dict, filas: list[dict]) -> dict:
    """Bloque "bess.cd" de data.json: dias[k] = [fecha, {id_BESS: [hfp..., hp...]}], el más reciente primero.

    Por periodo 6 valores: [°C Min, °C Máx, Salto °C, Tramo, Fuera de Rango, tipo] (Salto / Tramo / Fuera = número o «normal»;
    tipo = «alto» (rojo) / «bajo» (azul) / null). null = sin dato."""
    def val(v, campo):
        if v in ("", None):
            return None
        if campo == "Fuera_Tipo":
            return v
        try:
            x = float(v)
        except (TypeError, ValueError):
            return v                                             # «normal» / «Sin datos»
        return int(x) if campo == "Tramo" else round(x, 1)
    por_dia: dict[str, dict] = {}
    for f in filas:
        por_dia.setdefault(f["Fecha"], {})[f["BESS"]] = [val(f.get(f"{g}_{c}"), c)
                                                         for g in ("HFP", "HP") for c in ("Min", "Max", "Salto", "Tramo", "Fuera", "Fuera_Tipo")]
    return {"dias": [[d, por_dia[d]] for d in sorted(por_dia, reverse=True)]}


def _disponibilidad(cfg: dict, filas: list[dict]) -> dict:
    """Bloque "disponibilidad" de data.json: horas operativas por día e inversor, en arreglos alineados desde `d0`.

    h[id_inversor][k] = horas del día d0 + k (null = sin dato). El dashboard calcula con ellas la disponibilidad
    diaria, mensual y anual (la fórmula del Excel: 1 si horas >= horas_ref, si no horas / horas_ref).
    r[id_inversor][k] = rendimiento (kWh) del mismo día (null = sin dato). pnom[id_inversor] = potencia activa máxima (kW, fija en config/inversores.json: `potencia_activa_max_kw`).
    """
    dcfg = cfg.get("disponibilidad") or {}
    out: dict = {"activo": bool(dcfg.get("activo")), "horas_ref": dcfg.get("horas_referencia", disp.HORAS_REF),
                 "excel_desde": dcfg.get("excel_desde"), "d0": None, "hasta": None, "revisado": None, "h": {}, "r": {},
                 "pnom": {i["id"]: i.get("potencia_activa_max_kw") for i in cfg["inversores"] if i.get("potencia_activa_max_kw")}}
    if not out["activo"]:
        return out
    out["revisado"] = leer_json(disp.ruta_estado(), {}).get("revisado_hasta")
    por = disp.por_inversor(filas)
    rend = disp.por_inversor(filas, "Rendimiento_kWh")
    fechas = sorted({d for m in por.values() for d in m})
    if not fechas:
        return out
    d0, d1 = date.fromisoformat(fechas[0]), date.fromisoformat(fechas[-1])
    out["d0"], out["hasta"] = fechas[0], fechas[-1]
    for inv in cfg["inversores"]:
        m = por.get(inv["id"])
        if m:
            out["h"][inv["id"]] = [m.get((d0 + timedelta(days=k)).isoformat()) for k in range((d1 - d0).days + 1)]
        mr = rend.get(inv["id"])
        if mr:
            out["r"][inv["id"]] = [mr.get((d0 + timedelta(days=k)).isoformat()) for k in range((d1 - d0).days + 1)]
    return out


def _energia(cfg: dict, filas: list[dict]) -> dict:
    """Bloque «energia_exportada» de data.json: filas[código] = [[«AAAA-MM-DD HH:MM», kWh], ...] (solo > 0, lo más reciente primero)."""
    c = ee.config(cfg)
    est = leer_json(ee.ruta_estado(), {})
    return {"activo": bool(c.get("activo", True)), "plantas": ee.plantas(cfg), "revisado": est.get("ultima_revision_ok") or est.get("ultima_revision"),
            "filas": {cod: [[fh[:16], kwh] for fh, kwh in lst] for cod, lst in ee.por_planta(filas).items()}}


def _alarmas_meta(filas: list[dict]) -> dict:
    """Bloque «alarmas_hist» de data.json: solo lo mínimo (cuántas hay y desde cuándo); las filas van aparte en alarmas_historicas.json,
    que el dashboard descarga únicamente al abrir esa pestaña. `v` cambia cuando hay alarmas nuevas (rompe la caché de ese archivo)."""
    n = {c: 0 for c in ah.HOJAS}
    ult: dict[str, str] = {}
    prim: dict[str, str] = {}
    for f in filas:
        c = f["Planta"]
        n[c] = n.get(c, 0) + 1
        o = f[ah.C_FOCURRE][:10]
        ult[c] = max(ult.get(c, o), o)
        prim[c] = min(prim.get(c, o), o)
    cap = max((f.get("Capturado", "") for f in filas), default="")
    return {"hojas": ah.HOJAS, "n": n, "desde": prim, "hasta": ult, "v": f"{sum(n.values())}-{re.sub(r'[^0-9]', '', cap)}"}


def construir_alarmas(filas: list[dict], ahora: datetime) -> dict:
    """alarmas_historicas.json: filas compactas por planta (lo más reciente primero) con los textos repetidos en diccionarios.

    fila = [gravedad, tipo de dispositivo, dispositivo, SN, causa, ID de alarma, nombre, tipo de eliminación, ocurrió, eliminada]
    donde gravedad, tipo, dispositivo, nombre y tipo de eliminación son índices de `dic`. Así 10 000 alarmas pesan ~1 MB sin comprimir
    (~150 KB con la compresión de GitHub Pages) y el navegador las pagina sin dibujarlas todas.
    """
    dic: dict[str, list[str]] = {k: [] for k in ("grav", "tipo", "disp", "nombre", "elim")}
    pos: dict[str, dict[str, int]] = {k: {} for k in dic}

    def i(k: str, v: str) -> int:
        d = pos[k]
        if v not in d:
            d[v] = len(dic[k])
            dic[k].append(v)
        return d[v]

    plantas = {c: [] for c in ah.HOJAS}
    nombres: dict[str, str] = {}
    for c, lst in ah.por_planta(filas).items():
        for f in lst:
            nombres.setdefault(c, f.get(ah.C_PLANTA, ""))
            plantas.setdefault(c, []).append([
                i("grav", f[ah.C_GRAVEDAD]), i("tipo", f[ah.C_TIPO]), i("disp", f[ah.C_DISP]), f[ah.C_SN], f[ah.C_CAUSA], f[ah.C_ID],
                i("nombre", f[ah.C_NOMBRE]), i("elim", f[ah.C_ELIM]), f[ah.C_FOCURRE], f[ah.C_FELIM]])
    return {"generado": ahora.strftime("%Y-%m-%d %H:%M:%S"), "meta": _alarmas_meta(filas), "columnas": ah.COLUMNAS_HOJA,
            "planta": nombres, "dic": dic, "filas": plantas}


def _num(x):
    return None if pd.isna(x) else float(x)


def construir(cfg: dict, filas: list[dict], no_encontrados: list[str], ahora: datetime,
              info_corrida: dict | None = None, filas_bess: list[dict] | None = None,
              filas_disp: list[dict] | None = None, filas_cd: list[dict] | None = None,
              filas_alarmas: list[dict] | None = None, filas_energia: list[dict] | None = None) -> dict:
    invs = cfg["inversores"]
    out: dict = {
        "generado": ahora.strftime("%Y-%m-%d %H:%M:%S"),
        "zona_horaria": cfg["zona_horaria"],
        "intervalo_min": cfg["intervalo_minutos"],
        "margen_advertencia": cfg["margen_advertencia"],
        "plantas": cfg["plantas"],
        "inversores": [{k: i[k] for k in ("id", "planta", "grupo", "nombre", "umbral")} for i in invs],
        "no_encontrados": no_encontrados,
        "corrida": info_corrida or {},
        "ultima": {}, "serie": {}, "inicio": {}, "diario": {}, "alarmas": [], "eventos_potencia": [],
        "bess": {**_bess(cfg, filas_bess or [], ahora), "cd": _carga_descarga(cfg, filas_cd or [])},
        "disponibilidad": _disponibilidad(cfg, filas_disp or []),
        "alarmas_hist": _alarmas_meta(filas_alarmas or []),
        "energia_exportada": _energia(cfg, filas_energia or []),
    }
    df = pd.DataFrame(filas)
    if df.empty:
        return out

    df["ts"] = pd.to_datetime(df["Fecha_Hora"])
    df["id"] = df["Planta"] + "-" + df["Inversor"]
    df["r"] = pd.to_numeric(df["Resistencia_MOhm"], errors="coerce")
    df["p"] = pd.to_numeric(df["Potencia_Activa_%"], errors="coerce")
    df["um"] = pd.to_numeric(df["Umbral_MOhm"], errors="coerce")
    for col in ("Temp_Interna_C", "Inicio_Inversor"):          # un historial anterior a estas columnas no las trae
        if col not in df:
            df[col] = ""
    df["tint"] = pd.to_numeric(df["Temp_Interna_C"], errors="coerce")
    df["ini"] = pd.to_datetime(df["Inicio_Inversor"], errors="coerce")
    df = df.sort_values("ts")

    corte = ahora - timedelta(days=DIAS_SERIE)
    for inv in invs:
        d = df[df["id"] == inv["id"]]
        if d.empty:
            continue
        u = d.iloc[-1]
        out["ultima"][inv["id"]] = {
            "t": u["ts"].strftime("%Y-%m-%d %H:%M:%S"),
            "r": _num(u["r"]), "estado": u["Estado_Aislamiento"],
            "p_txt": u["Estado_Potencia_Activa"], "p": _num(u["p"]),
            "inv": u["Estado_Inversor"], "fuente": u["Fuente"],
        }
        con_t = d[d["tint"].notna()]
        con_i = d[d["ini"].notna()]
        if not con_t.empty:                                  # ultimo valor conocido (aunque la ultima corrida no lo trajo)
            out["ultima"][inv["id"]]["temp"] = _num(con_t.iloc[-1]["tint"])
            out["ultima"][inv["id"]]["t_temp"] = con_t.iloc[-1]["ts"].strftime("%Y-%m-%d %H:%M:%S")
        if not con_i.empty:
            out["ultima"][inv["id"]]["inicio"] = con_i.iloc[-1]["ini"].strftime("%Y-%m-%d %H:%M:%S")
            # cada arranque (uno por dia) una sola vez: el dashboard dibuja la hora del dia de cada inicio
            out["inicio"][inv["id"]] = sorted({calendar.timegm(t.timetuple()) for t in con_i["ini"]})
        rec = d[d["ts"] >= corte]
        # [t, resistencia, potencia %, umbral vigente en ese momento, temperatura interna]
        out["serie"][inv["id"]] = [
            [calendar.timegm(row.ts.timetuple()), _num(row.r), _num(row.p), _num(row.um), _num(row.tint)]
            for row in rec.itertuples()
        ]
        diario = []
        for dia, x in d.assign(dia=d["ts"].dt.strftime("%Y-%m-%d")).groupby("dia"):
            con = x["r"].dropna()
            diario.append([
                dia,
                None if con.empty else round(float(con.min()), 3),
                None if con.empty else round(float(con.mean()), 3),
                None if con.empty else round(float(con.max()), 3),
                int((x["Estado_Aislamiento"] == "ALARMA").sum()),
                int(len(x)),
                None if x["p"].dropna().empty else round(float(x["p"].min()), 1),
                None if x["um"].dropna().empty else float(x["um"].dropna().iloc[-1]),     # umbral vigente ese dia
            ])
        out["diario"][inv["id"]] = diario

    ev = df[df["Estado_Aislamiento"].isin(["ALARMA", "ADVERTENCIA"])].tail(MAX_ALARMAS).iloc[::-1]
    out["alarmas"] = [
        {"t": r.ts.strftime("%Y-%m-%d %H:%M"), "id": r.id, "r": _num(r.r), "um": _num(r.um),
         "estado": r.Estado_Aislamiento} for r in ev.itertuples()
    ]
    lim = df[df["p"].notna() & (df["p"] < 100)].tail(MAX_ALARMAS).iloc[::-1]
    out["eventos_potencia"] = [
        {"t": r.ts.strftime("%Y-%m-%d %H:%M"), "id": r.id, "p": _num(r.p), "txt": r.Estado_Potencia_Activa}
        for r in lim.itertuples()
    ]
    return out


def escribir_alarmas(data: dict, destino: Path | None = None) -> Path:
    """Escribe alarmas_historicas.json junto a data.json (de forma atómica)."""
    return escribir(data, destino or (SITE_DIR / "data" / "alarmas_historicas.json"))


def escribir(data: dict, destino: Path | None = None) -> Path:
    destino = destino or (SITE_DIR / "data" / "data.json")
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(destino)
    return destino
