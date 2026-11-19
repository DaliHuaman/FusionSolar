"""Energía exportada a la red (kWh) por planta, hora a hora, con aviso por correo.

De dónde sale: FusionSolar › Informes › Informe de plantas › (planta) › granularidad «Por día» › columna «Energía exportada (kWh)»; una
fila por hora («Período estadístico» AAAA-MM-DD HH:00:00). El portal publica cada hora con ~2 h de retraso.

Cuándo: `energia_exportada.yml` corre cada hora al minuto 15 (hora de Lima) y relee el día de hoy y el de ayer (`dias_releer`).

Qué se guarda (`energia_exportada.csv`, rama «data»): SOLO las horas con energía exportada > 0 (una hora en 0 no entra al histórico).
Clave = planta + fecha y hora; si el portal corrige el valor de una hora ya guardada se actualiza, nunca se borra una fila.

Relleno inicial: las fechas de `fechas_relleno` (las que ya se habían detectado a mano) se consultan una sola vez; su avance queda en
`energia_exportada_estado.json`. También se puede recorrer un rango a mano (`--desde/--hasta`, todas las plantas).

Aviso por correo: cada hora NUEVA con energía exportada > 0 se avisa una sola vez (todas las de la corrida en un mismo correo), solo si es de
las últimas `alertar_horas` (así el relleno de fechas antiguas no dispara correos). Si el envío falla, se reintenta en la corrida siguiente.
Columna «Alertado» del CSV: fecha del aviso, «relleno» (histórico, sin aviso) o vacío (pendiente).
"""
from __future__ import annotations

import csv
import html as html_lib
import logging
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import alertas
from .common import FMT_TS, archivo_datos, guardar_json, leer_json

log = logging.getLogger("fusionsolar.energia_exportada")

COLUMNAS = ["Planta", "Fecha_Hora", "Energia_kWh", "Capturado", "Alertado"]
HOJA = "Energía Exportada kWh"
SIN_AVISO = "relleno"
POR_DEFECTO = {"activo": True, "plantas": [], "dias_releer": 2, "presupuesto_segundos": 900, "alertar_horas": 48,
               "destinatarios": [], "copia": [], "plantilla": "config/alerta_energia.txt", "fechas_relleno": {}}
PLANTILLA_RESPALDO = {
    "ASUNTO": "FusionSolar - Energía exportada a la red: {plantas}",
    "CUERPO": "Estimados,\n\nSe detectó energía exportada a la red en {n_registros} (total {total} kWh). Revisión del {fecha_hora} "
              "(hora de Lima).\n\n{recuadro}\n\nDashboard: {dashboard}\n",
}


def config(cfg: dict) -> dict:
    return POR_DEFECTO | (cfg.get("energia_exportada") or {})


def plantas(cfg: dict) -> list[dict]:
    """[{codigo, titulo}] en el orden de la hoja (LC, VES, PL); si no hay lista, las plantas de la configuración general."""
    lst = config(cfg)["plantas"]
    return lst or [{"codigo": p["codigo"], "titulo": p["nombre"]} for p in cfg["plantas"]]


# ---------------------------------------------------------------------------------------------- archivos
def ruta_csv() -> Path:
    return archivo_datos("energia_exportada.csv")


def ruta_estado() -> Path:
    return archivo_datos("energia_exportada_estado.json")


def leer_filas() -> list[dict]:
    p = ruta_csv()
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def escribir_filas(filas: list[dict]) -> None:
    orden = sorted(filas, key=lambda f: (f["Planta"], f["Fecha_Hora"]))
    p = ruta_csv()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS, extrasaction="ignore")
        w.writeheader()
        w.writerows(orden)
    os.replace(tmp, p)


def por_planta(filas: list[dict]) -> dict[str, list[tuple[str, float]]]:
    """{código: [(fecha_hora, kWh)]} con lo más reciente primero (orden de la hoja y del dashboard)."""
    out: dict[str, list[tuple[str, float]]] = {}
    for f in filas:
        try:
            v = float(f["Energia_kWh"])
        except (TypeError, ValueError):
            continue
        if v > 0:
            out.setdefault(f["Planta"], []).append((f["Fecha_Hora"], v))
    for lst in out.values():
        lst.sort(reverse=True)
    return out


# ---------------------------------------------------------------------------------------------- lógica (sin portal)
def fusionar(existentes: list[dict], lecturas: list[tuple[str, str, float | None]], ahora: datetime,
             sin_aviso: bool = False) -> tuple[list[dict], list[dict]]:
    """Mezcla lecturas [(planta, fecha_hora, kWh)] con lo guardado. Solo entran las horas con kWh > 0.

    Devuelve (todas, nuevas). Una hora ya guardada solo cambia su valor si el portal lo corrigió (sigue > 0); nunca se borra.
    `sin_aviso` (relleno / rango manual): las filas nuevas quedan marcadas como «relleno» y no generan correo.
    """
    todas = {(f["Planta"], f["Fecha_Hora"]): dict(f) for f in existentes}
    nuevas = []
    for planta, fh, kwh in lecturas:
        if kwh is None or kwh <= 0:
            continue
        clave = (planta, fh)
        valor = round(float(kwh), 3)
        if clave in todas:
            try:
                previo = float(todas[clave]["Energia_kWh"])
            except (TypeError, ValueError):
                previo = None
            if previo != valor:
                log.info("Energía exportada %s %s corregida en el portal: %s → %s kWh", planta, fh, previo, valor)
                todas[clave]["Energia_kWh"] = valor
            continue
        fila = {"Planta": planta, "Fecha_Hora": fh, "Energia_kWh": valor, "Capturado": ahora.strftime(FMT_TS),
                "Alertado": SIN_AVISO if sin_aviso else ""}
        todas[clave] = fila
        nuevas.append(fila)
    return list(todas.values()), nuevas


def pendientes_aviso(filas: list[dict], ahora: datetime, horas: float) -> list[dict]:
    """Filas aún no avisadas y recientes (de las últimas `horas`); las más antiguas se marcan como «relleno» por quien llama."""
    corte = (ahora - timedelta(hours=horas)).strftime(FMT_TS)
    return sorted((f for f in filas if not f.get("Alertado") and f["Fecha_Hora"] >= corte), key=lambda f: (f["Fecha_Hora"], f["Planta"]))


def _fmt(v: float) -> str:
    return f"{v:,.3f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_fh(fh: str) -> str:
    d = datetime.strptime(fh, FMT_TS)
    return d.strftime("%d/%m/%Y %H:%M")


def _tabla_html(avisos: list[dict], titulos: dict[str, str]) -> str:
    K, F = alertas.K, alertas.FUENTE

    def th(t, al):
        return (f'<td align="{al}" bgcolor="{K["brand"]}" style="padding:9px 12px;background:{K["brand"]};color:#ffffff;'
                f'font:600 12px {F};text-align:{al};white-space:nowrap">{t}</td>')
    filas = ['<tr>' + th("Planta", "left") + th("Fecha y hora", "center") + th("Energía exportada", "right") + '</tr>']
    for i, a in enumerate(avisos):
        fondo = K["panel"] if i % 2 == 0 else K["bg"]
        base = f'padding:10px 12px;border-bottom:1px solid {K["borde"]};font:14px {F};color:{K["fg"]}'
        filas.append(
            f'<tr bgcolor="{fondo}" style="background:{fondo}">'
            f'<td align="left" style="{base};font-weight:600">{html_lib.escape(titulos.get(a["Planta"], a["Planta"]))}</td>'
            f'<td align="center" style="{base};white-space:nowrap">{_fmt_fh(a["Fecha_Hora"])}</td>'
            f'<td align="right" style="{base};white-space:nowrap;color:{K["acc"]};font-weight:700">{_fmt(float(a["Energia_kWh"]))} kWh</td></tr>')
    total = sum(float(a["Energia_kWh"]) for a in avisos)
    if len(avisos) > 1:
        base = f'padding:10px 12px;font:600 14px {F};color:{K["fg"]};background:{K["bg"]}'
        filas.append(f'<tr bgcolor="{K["bg"]}"><td colspan="2" align="right" style="{base}">Total</td>'
                     f'<td align="right" style="{base};white-space:nowrap">{_fmt(total)} kWh</td></tr>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border:1px solid {K["borde"]};border-collapse:separate;border-radius:10px;overflow:hidden">'
            + "".join(filas) + '</table>')


def componer(cfg: dict, avisos: list[dict], ahora: datetime) -> tuple[str, str, str]:
    """(asunto, texto, html) del aviso. Mismo diseño Kondu que el aviso de aislamiento."""
    c = config(cfg)
    titulos = {p["codigo"]: p["titulo"] for p in plantas(cfg)}
    plantilla = alertas.cargar_plantilla(cfg, c["plantilla"], PLANTILLA_RESPALDO)
    suma: dict[str, float] = {}
    for a in avisos:
        suma[a["Planta"]] = suma.get(a["Planta"], 0) + float(a["Energia_kWh"])
    n = len(avisos)
    recuadro = "\n".join(f"• {titulos.get(a['Planta'], a['Planta'])} · {_fmt_fh(a['Fecha_Hora'])}: {_fmt(float(a['Energia_kWh']))} kWh"
                         for a in avisos)
    url = alertas._cfg(cfg)["dashboard_url"]
    datos = alertas._Seguro(plantas=", ".join(f"{p} ({_fmt(v)} kWh)" for p, v in suma.items()),
                            n_registros=f"{n} registro horario" if n == 1 else f"{n} registros horarios",
                            total=_fmt(sum(suma.values())), fecha_hora=ahora.strftime("%d/%m/%Y %H:%M"), recuadro=recuadro, dashboard=url)
    asunto = " ".join(plantilla["ASUNTO"].format_map(datos).split())
    texto = plantilla["CUERPO"].format_map(datos).strip() + "\n"
    html = alertas._html_correo(plantilla["CUERPO"].format_map(datos | {"recuadro": alertas._MARCA}).strip(), [], url, asunto,
                                titulo="Energía exportada a la red", tabla=_tabla_html(avisos, titulos))
    return asunto, texto, html


def destinatarios(cfg: dict) -> tuple[list[str], list[str]]:
    c = config(cfg)
    para = alertas.unir_correos(c["destinatarios"])
    cc = [d for d in alertas.unir_correos(c["copia"]) if d.lower() not in {x.lower() for x in para}]
    return para, cc


def avisar(cfg: dict, filas: list[dict], ahora: datetime, enviar=alertas.enviar_correo) -> dict:
    """Envía (en un solo correo) las horas con energía exportada aún no avisadas y marca «Alertado». Nunca lanza."""
    c = config(cfg)
    pend = pendientes_aviso(filas, ahora, float(c["alertar_horas"]))
    viejas = [f for f in filas if not f.get("Alertado") and f not in pend]
    for f in viejas:                                         # fuera de la ventana de aviso: ya no se avisan
        f["Alertado"] = SIN_AVISO
    if not pend:
        return {"pendientes": 0, "enviados": 0}
    para, cc = destinatarios(cfg)
    try:
        if not para:
            raise RuntimeError("no hay destinatarios (config energia_exportada.destinatarios)")
        asunto, texto, html = componer(cfg, pend, ahora)
        enviar(asunto, texto, para, html, cc)
    except Exception as e:
        log.warning("No se pudo enviar el aviso de energía exportada (%s); se reintentará en la próxima corrida", e)
        return {"pendientes": len(pend), "enviados": 0, "error": str(e)}
    for f in pend:
        f["Alertado"] = ahora.strftime(FMT_TS)
    log.info("Aviso de energía exportada enviado (%s horas) a %s (copia: %s)", len(pend), ", ".join(para), ", ".join(cc) or "-")
    return {"pendientes": len(pend), "enviados": len(pend)}


# ---------------------------------------------------------------------------------------------- corrida (con el portal)
def plan(cfg: dict, ahora: datetime, estado: dict, desde: date | None = None, hasta: date | None = None) -> list[tuple[str, date, bool]]:
    """Consultas de esta corrida: [(planta, día, es_relleno)], agrupadas por planta (así se elige cada planta una sola vez).

    · Siempre: hoy y los días anteriores hasta completar `dias_releer` (por defecto hoy y ayer), en todas las plantas.
    · Relleno: las `fechas_relleno` de cada planta que aún no se consultaron (estado «relleno_hecho»).
    · Rango manual (`desde`/`hasta`): todos los días del rango en todas las plantas (sin aviso).
    """
    c = config(cfg)
    cods = [p["codigo"] for p in plantas(cfg)]
    hoy = ahora.date()
    hecho = set(estado.get("relleno_hecho") or [])
    out: dict[str, dict[date, bool]] = {cod: {} for cod in cods}
    for cod in cods:
        for k in range(max(1, int(c["dias_releer"]))):
            out[cod][hoy - timedelta(days=k)] = False
        for d in (c["fechas_relleno"] or {}).get(cod, []):
            if f"{cod}|{d}" not in hecho:
                dd = date.fromisoformat(d)
                out[cod].setdefault(dd, True)
        if desde:
            d = desde
            while d <= (hasta or hoy):
                out[cod].setdefault(d, True)
                d += timedelta(days=1)
    # lo reciente primero (es lo que puede avisar); luego el relleno del más nuevo al más antiguo
    return [(cod, d, rel) for cod in cods for d, rel in sorted(out[cod].items(), key=lambda x: (x[1], -x[0].toordinal()))]


def actualizar(sc, cfg: dict, ahora: datetime, desde: date | None = None, hasta: date | None = None, prueba: bool = False,
               enviar=alertas.enviar_correo) -> dict:
    """Lee del Informe de plantas las horas pendientes, guarda las que tienen energía exportada > 0 y avisa por correo las nuevas.

    `sc` es un Scraper con el Informe de plantas ya abierto. `prueba`: solo lee y deja en el log lo que encontró (no guarda ni envía).
    """
    c = config(cfg)
    buscar = {p["codigo"]: p["buscar"] for p in cfg["plantas"]}
    estado = leer_json(ruta_estado(), {})
    consultas = plan(cfg, ahora, estado, desde, hasta)
    limite = time.monotonic() + float(c["presupuesto_segundos"])
    filas = leer_filas()
    hecho = set(estado.get("relleno_hecho") or [])
    info: dict = {"consultas": len(consultas), "dias": 0, "nuevas": 0, "errores": {}}
    planta_actual = None
    for cod, dia, relleno in consultas:
        if time.monotonic() > limite:
            log.warning("Presupuesto de tiempo agotado; la próxima corrida continúa con lo que falte")
            info["incompleto"] = True
            break
        try:
            if planta_actual != cod:
                sc.elegir_planta_informe(buscar[cod])
                planta_actual = cod
            horas = sc.energia_exportada_dia(dia)
        except Exception as e:
            log.exception("fallo leyendo la energía exportada de %s el %s: %s", cod, dia, e)
            info["errores"][f"{cod} {dia}"] = str(e)
            planta_actual = None                              # se vuelve a elegir la planta antes de la siguiente consulta
            continue
        info["dias"] += 1
        positivas = [(fh, v) for fh, v in horas if v and v > 0]
        log.info("%-4s %s: %s horas leídas, %s con energía exportada%s", cod, dia, len(horas), len(positivas),
                 (" → " + ", ".join(f"{fh[11:16]} {v:g} kWh" for fh, v in positivas)) if positivas else "")
        if prueba:
            continue
        filas, nuevas = fusionar(filas, [(cod, fh, v) for fh, v in horas], ahora, sin_aviso=relleno)
        info["nuevas"] += len(nuevas)
        if relleno and dia.isoformat() in (c["fechas_relleno"] or {}).get(cod, []):
            hecho.add(f"{cod}|{dia.isoformat()}")
    if getattr(sc, "urls_informe", None):
        log.info("Llamadas REST del Informe de plantas: %s", ", ".join(sc.urls_informe))
    if prueba:
        return info
    res = avisar(cfg, filas, ahora, enviar)
    if res.get("pendientes"):
        info["aviso"] = res
    escribir_filas(filas)
    estado["relleno_hecho"] = sorted(hecho)
    estado["ultima_revision"] = ahora.strftime(FMT_TS)
    if not info["errores"]:
        estado["ultima_revision_ok"] = ahora.strftime(FMT_TS)
    guardar_json(ruta_estado(), estado)
    info["total"] = len(filas)
    if not info["errores"]:
        info.pop("errores")
    return info
