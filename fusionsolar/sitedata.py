"""Genera data.json para el dashboard web a partir del CSV maestro."""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from . import disponibilidad as disp
from .common import CAMPOS_BESS, STATE_DIR, leer_json

SITE_DIR = STATE_DIR / "site"
DIAS_SERIE = 45          # resolucion completa (cada lectura) en el dashboard
MAX_ALARMAS = 200
DIAS_SERIE_BESS = 7      # temperaturas BESS: 6 valores x 5 equipos x 48 lecturas/dia; 7 dias mantienen data.json liviano


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


def _disponibilidad(cfg: dict, filas: list[dict]) -> dict:
    """Bloque "disponibilidad" de data.json: horas operativas por día e inversor, en arreglos alineados desde `d0`.

    h[id_inversor][k] = horas del día d0 + k (null = sin dato). El dashboard calcula con ellas la disponibilidad
    diaria, mensual y anual (la fórmula del Excel: 1 si horas >= horas_ref, si no horas / horas_ref).
    """
    dcfg = cfg.get("disponibilidad") or {}
    out: dict = {"activo": bool(dcfg.get("activo")), "horas_ref": dcfg.get("horas_referencia", disp.HORAS_REF),
                 "excel_desde": dcfg.get("excel_desde"), "d0": None, "hasta": None, "revisado": None, "h": {}}
    if not out["activo"]:
        return out
    out["revisado"] = leer_json(disp.ruta_estado(), {}).get("revisado_hasta")
    por = disp.por_inversor(filas)
    fechas = sorted({d for m in por.values() for d in m})
    if not fechas:
        return out
    d0, d1 = date.fromisoformat(fechas[0]), date.fromisoformat(fechas[-1])
    out["d0"], out["hasta"] = fechas[0], fechas[-1]
    for inv in cfg["inversores"]:
        m = por.get(inv["id"])
        if m:
            out["h"][inv["id"]] = [m.get((d0 + timedelta(days=k)).isoformat()) for k in range((d1 - d0).days + 1)]
    return out


def _num(x):
    return None if pd.isna(x) else float(x)


def construir(cfg: dict, filas: list[dict], no_encontrados: list[str], ahora: datetime,
              info_corrida: dict | None = None, filas_bess: list[dict] | None = None,
              filas_disp: list[dict] | None = None) -> dict:
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
        "bess": _bess(cfg, filas_bess or [], ahora),
        "disponibilidad": _disponibilidad(cfg, filas_disp or []),
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


def escribir(data: dict, destino: Path | None = None) -> Path:
    destino = destino or (SITE_DIR / "data" / "data.json")
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(destino)
    return destino
