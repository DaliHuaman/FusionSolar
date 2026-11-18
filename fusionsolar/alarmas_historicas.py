"""Alarmas históricas de FusionSolar (Alarmas > Alarmas históricas > Exportar), conservadas para siempre.

El portal BORRA periódicamente las alarmas históricas por límite de almacenamiento. Para no perder ninguna, una vez por semana se
exporta del portal el histórico de cada planta (LC, PL y VES) y se AGREGA a `alarmas_historicas.csv` (rama «data»):

  · Acumulativo y sin pisar: lo ya guardado es la fuente de verdad. Una alarma nueva se agrega; una que ya estaba se ignora (solo se
    rellenan celdas que estuvieran vacías). Nunca se borra ni se reemplaza una fila.
  · Clave estable por alarma: planta + número SN + ID de la alarma + identificador de la causa + fecha y hora en que ocurrió.
  · Ventanas con solape: cada semana se vuelve a consultar desde `solape_dias` antes de la última revisión (una alarma entra al
    histórico cuando se elimina, así que una que ocurrió hace días y recién se eliminó también se captura).
  · Primera vez (relleno): se recorre el histórico hacia atrás, por ventanas de `ventana_dias` (máx. 28: el portal solo deja 1 mes en «Hora de incidencia»), hasta `inicio_busqueda` (las ventanas sin
    alarmas son una sola consulta, sin exportar, y no cortan el recorrido: así no se queda fuera historia por un periodo tranquilo). Si la
    corrida se corta, la siguiente continúa donde quedó (`alarmas_historicas_estado.json`).
  · Integridad: tras Buscar el portal muestra «Total: N»; el Excel exportado debe traer exactamente N filas y todas dentro de la
    ventana pedida. Si no cuadra, esa ventana se repite y, si sigue sin cuadrar, no se da por revisada (se reintenta la próxima vez).
    Una ventana que llegue a `max_filas_ventana` se parte en dos por si el portal limita la exportación.

El Excel (hojas «Alarma Histórica PL / LC / VES») y la vista previa del dashboard se arman SIEMPRE desde este CSV, sin tocar el portal.
"""
from __future__ import annotations

import csv
import logging
import os
import re
import shutil
import tempfile
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

from .common import FMT_TS, archivo_datos, guardar_json, leer_json

log = logging.getLogger("fusionsolar.alarmas_historicas")

# Columnas de la hoja y del CSV. Se invierten las dos últimas respecto al portal: «ocurrió» va ANTES de «eliminación».
C_GRAVEDAD, C_PLANTA, C_TIPO, C_DISP, C_SN = ("Gravedad de la alarma", "Nombre de la planta", "Tipo de dispositivo",
                                              "Nombre del dispositivo", "Número SN")
C_CAUSA, C_ID, C_NOMBRE, C_ELIM, C_FELIM, C_FOCURRE = ("Identificador de la causa", "ID de la alarma", "Nombre de la alarma",
                                                       "Tipo de eliminación", "Fecha y hora de eliminación",
                                                       "Fecha y hora en que ha ocurrido")
COLUMNAS_HOJA = [C_GRAVEDAD, C_PLANTA, C_TIPO, C_DISP, C_SN, C_CAUSA, C_ID, C_NOMBRE, C_ELIM, C_FOCURRE, C_FELIM]
COLUMNAS_FECHA = (C_FOCURRE, C_FELIM)
# CSV: código de planta (LC / PL / VES), las 11 columnas de la hoja y cuándo se guardó por primera vez (auditoría)
COLUMNAS_CSV = ["Planta", *COLUMNAS_HOJA, "Capturado"]
HOJAS = {"PL": "Alarma Histórica PL", "LC": "Alarma Histórica LC", "VES": "Alarma Histórica VES"}   # orden de las hojas / pestañas
MAX_VENTANA_DIAS = 28    # el filtro «Hora de incidencia» del portal admite como máximo 1 mes entre inicio y fin; 28 días cabe en cualquier mes
REQUERIDAS = (C_SN, C_ID, C_FOCURRE)       # sin estas columnas no se puede identificar una alarma: el archivo se rechaza


class ErrorFormato(RuntimeError):
    """El archivo exportado no tiene el formato esperado (no se guarda nada)."""


def _norm(s) -> str:
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


_CANON = {_norm(c): c for c in COLUMNAS_HOJA}


# ---------------------------------------------------------------------------------------------- lectura del Excel exportado
def _texto(v) -> str:
    """Celda -> texto limpio. Fechas a 'AAAA-MM-DD HH:MM:SS'; números enteros sin '.0'."""
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime(FMT_TS)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day).strftime(FMT_TS)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return re.sub(r"\s+", " ", str(v).replace("\xa0", " ")).strip()


def _fecha(v) -> str:
    """Normaliza una fecha (texto o datetime) a 'AAAA-MM-DD HH:MM:SS'; vacío si no se entiende."""
    t = _texto(v)
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", t)
    if not m:
        return ""
    a, mo, d, h, mi, s = (int(x) if x else 0 for x in m.groups())
    try:
        return datetime(a, mo, d, h, mi, s).strftime(FMT_TS)
    except ValueError:
        return ""


def _filas_archivo(path: Path) -> list[list]:
    if path.suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8-sig") as f:
            return [list(r) for r in csv.reader(f)]
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def leer_exportacion(path: Path, codigo_planta: str) -> list[dict]:
    """Lee el Excel de «Alarmas históricas» exportado del portal -> filas con las columnas del CSV (sin «Capturado»).

    Las columnas se localizan por su nombre (da igual el orden en que las exporte el portal). Lanza ErrorFormato si falta alguna de
    las necesarias para identificar la alarma. Las filas vacías se ignoran.
    """
    filas = _filas_archivo(path)
    if not filas:
        return []
    cab = [_CANON.get(_norm(c)) for c in filas[0]]
    faltan = [c for c in REQUERIDAS if c not in cab]
    if faltan:
        raise ErrorFormato(f"{path.name}: faltan las columnas {', '.join(faltan)} (encabezados: {[str(c) for c in filas[0]]})")
    out = []
    for r in filas[1:]:
        if not any(x not in (None, "") for x in r):
            continue
        d = {c: "" for c in COLUMNAS_HOJA}
        for nombre, v in zip(cab, r):
            if nombre in d and not d[nombre]:
                d[nombre] = _fecha(v) if nombre in COLUMNAS_FECHA else _texto(v)
        if not d[C_FOCURRE]:
            raise ErrorFormato(f"{path.name}: fila sin fecha y hora de ocurrencia: {r}")
        d["Planta"] = codigo_planta
        out.append(d)
    return out


def clave(f: dict) -> tuple[str, str, str, str, str]:
    return (f["Planta"], f.get(C_SN) or f.get(C_DISP, ""), f.get(C_ID, ""), f.get(C_CAUSA, ""), f[C_FOCURRE])


# ---------------------------------------------------------------------------------------------- archivos
def ruta_csv() -> Path:
    return archivo_datos("alarmas_historicas.csv")


def ruta_estado() -> Path:
    return archivo_datos("alarmas_historicas_estado.json")


def leer_filas() -> list[dict]:
    p = ruta_csv()
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def combinar(existentes: list[dict], nuevas: list[dict], ahora: datetime | None = None) -> tuple[list[dict], int]:
    """Suma `nuevas` a `existentes` SIN pisar nada. Devuelve (todas ordenadas, cuántas filas realmente nuevas).

    Una fila con la misma clave que una guardada se descarta (solo completa celdas que estuvieran vacías). Orden: cronológico por
    fecha de ocurrencia (así el CSV solo crece por el final).
    """
    capturado = (ahora or datetime.now()).strftime(FMT_TS)
    todas: dict[tuple, dict] = {}
    for f in existentes:
        todas.setdefault(clave(f), {c: f.get(c, "") or "" for c in COLUMNAS_CSV})
    n = 0
    for f in nuevas:
        k = clave(f)
        if k in todas:
            previa = todas[k]
            for c in COLUMNAS_HOJA:
                if not previa[c] and f.get(c):
                    previa[c] = f[c]
            continue
        todas[k] = {**{c: f.get(c, "") or "" for c in COLUMNAS_CSV}, "Capturado": f.get("Capturado") or capturado}
        n += 1
    orden = sorted(todas.values(), key=lambda f: (f[C_FOCURRE], f[C_FELIM], f["Planta"], f[C_SN], f[C_ID], f[C_CAUSA]))
    return orden, n


def guardar_filas(nuevas: list[dict], ahora: datetime | None = None) -> tuple[list[dict], int]:
    """Combina con lo guardado y reescribe el CSV de forma atómica. Nunca puede dejar menos filas que antes (si pasara, no escribe).

    Antes de reemplazar el archivo deja una copia del anterior (`alarmas_historicas.csv.bak`): la rama «data» no guarda historial
    de commits, así que esa copia es la red de seguridad ante cualquier error.
    """
    previas = leer_filas()
    todas, n = combinar(previas, nuevas, ahora)
    if len(todas) < len(previas):                      # imposible con `combinar`; defensa por si alguien lo cambia
        raise RuntimeError("alarmas_historicas: el resultado tiene menos filas que lo guardado; no se escribe")
    if n == 0 and len(todas) == len(previas) and ruta_csv().exists():
        return todas, 0
    p = ruta_csv()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNAS_CSV)
        w.writeheader()
        w.writerows(todas)
    if p.exists():
        shutil.copyfile(p, p.with_name(p.name + ".bak"))
    os.replace(tmp, p)
    return todas, n


def por_planta(filas: list[dict]) -> dict[str, list[dict]]:
    """{código: filas} con lo más reciente primero (como se muestra en la hoja y en el dashboard)."""
    out: dict[str, list[dict]] = {c: [] for c in HOJAS}
    for f in sorted(filas, key=lambda f: (f[C_FOCURRE], f[C_FELIM]), reverse=True):
        out.setdefault(f["Planta"], []).append(f)
    return out


def _entero_o_texto(v: str):
    return int(v) if v.isdigit() and len(v) < 15 and not v.startswith("0") else v


def fila_hoja(f: dict) -> list:
    """Valores de una fila de la hoja de Excel (fechas como datetime; ID de alarma y causa como número cuando lo son)."""
    out = []
    for c in COLUMNAS_HOJA:
        v = f.get(c, "") or ""
        if c in COLUMNAS_FECHA:
            out.append(datetime.strptime(v, FMT_TS) if v else None)
        elif c in (C_ID, C_CAUSA):
            out.append(_entero_o_texto(v) if v else None)
        else:
            out.append(v or None)
    return out


# ---------------------------------------------------------------------------------------------- ventanas y corrida
def ventanas(d0: date, d1: date, dias: int) -> list[tuple[date, date]]:
    """Trozos consecutivos [ini, fin] (ambos días incluidos) de a lo más `dias` días entre d0 y d1."""
    out, d = [], d0
    while d <= d1:
        fin = min(d + timedelta(days=dias - 1), d1)
        out.append((d, fin))
        d = fin + timedelta(days=1)
    return out


def _dentro(filas: list[dict], d0: date, d1: date, holgura_dias: int = 1) -> bool:
    """¿Todas las filas ocurrieron dentro de la ventana (con 1 día de holgura por zona horaria)? Detecta un filtro de fechas no aplicado."""
    lo = (datetime(d0.year, d0.month, d0.day) - timedelta(days=holgura_dias)).strftime(FMT_TS)
    hi = (datetime(d1.year, d1.month, d1.day) + timedelta(days=1 + holgura_dias)).strftime(FMT_TS)
    return all(lo <= f[C_FOCURRE] < hi for f in filas)


def _ventana(sc, codigo: str, d0: date, d1: date, acfg: dict, carpeta: Path, ahora: datetime) -> tuple[int, int]:
    """Consulta una ventana y guarda lo que traiga. Devuelve (filas del portal, filas nuevas). Lanza ante cualquier duda."""
    max_filas = int(acfg.get("max_filas_ventana", 5000))
    verificar = bool(acfg.get("verificar_total", True))
    ultimo: Exception | None = None
    for intento in (1, 2):
        try:
            total = sc.buscar_alarmas(datetime.combine(d0, datetime.min.time()), datetime.combine(d1, datetime.max.time()).replace(microsecond=0))
            if total == 0:
                return 0, 0
            if total is not None and total >= max_filas and d0 < d1:
                mitad = d0 + timedelta(days=(d1 - d0).days // 2)         # exportación posiblemente truncada: se parte en dos
                log.info("%s %s..%s: %s filas (>= %s); se parte la ventana", codigo, d0, d1, total, max_filas)
                a = _ventana(sc, codigo, d0, mitad, acfg, carpeta, ahora)
                b = _ventana(sc, codigo, mitad + timedelta(days=1), d1, acfg, carpeta, ahora)
                return a[0] + b[0], a[1] + b[1]
            ruta = sc.exportar_alarmas(carpeta)
            filas = leer_exportacion(ruta, codigo)
            ruta.unlink(missing_ok=True)
            if not _dentro(filas, d0, d1):
                raise RuntimeError(f"el Excel exportado trae alarmas fuera de {d0}..{d1}: el filtro de fechas no se aplicó")
            if verificar and total is not None and len(filas) != total:
                raise RuntimeError(f"el portal indica Total={total} y el Excel exportado trae {len(filas)} filas")
            if total is None:
                log.warning("%s %s..%s: no se pudo leer el «Total» del portal; se guarda sin verificar el conteo", codigo, d0, d1)
            _, nuevas = guardar_filas(filas, ahora)
            log.info("%s %s..%s: %s alarma(s) en el portal, %s nueva(s) guardada(s)", codigo, d0, d1, len(filas), nuevas)
            return len(filas), nuevas
        except Exception as e:
            ultimo = e
            log.warning("%s %s..%s intento %s falló: %s", codigo, d0, d1, intento, e)
            try:
                sc.captura(f"alarmas_{codigo}_{d0}")
            except Exception:
                pass
    raise RuntimeError(f"{codigo} {d0}..{d1}: {ultimo}")


def actualizar(sc, cfg: dict, ahora: datetime, desde: date | None = None, hasta: date | None = None,
               presupuesto_s: float | None = None, plantas_nodos: dict[str, dict] | None = None) -> dict:
    """Trae del portal las alarmas históricas de cada planta y las suma al CSV. `sc` = Scraper con sesión abierta.

    `plantas_nodos` = {código: nodo del árbol} (ver scraper.emparejar_plantas). Nunca avanza la revisión de una planta por encima
    de una ventana que falló, y un fallo en una planta no impide procesar las demás. Devuelve un resumen; `error` si algo quedó pendiente.
    """
    acfg = cfg.get("alarmas_historicas") or {}
    ventana = max(1, min(MAX_VENTANA_DIAS, int(acfg.get("ventana_dias", MAX_VENTANA_DIAS))))
    solape = max(0, int(acfg.get("solape_dias", 45)))
    inicio = date.fromisoformat(acfg.get("inicio_busqueda", "2025-01-01"))
    presupuesto_s = presupuesto_s if presupuesto_s is not None else float(acfg.get("presupuesto_segundos", 1200))
    fin = hasta or ahora.date()
    limite = time.monotonic() + presupuesto_s
    estado = leer_json(ruta_estado(), {})
    est_pl: dict = estado.setdefault("plantas", {})
    info: dict = {"plantas": {}, "errores": {}}
    carpeta = Path(tempfile.mkdtemp(prefix="alarmas_"))
    codigos = acfg.get("plantas") or [p["codigo"] for p in cfg["plantas"]]
    try:
        for codigo in codigos:
            res = info["plantas"][codigo] = {"ventanas": 0, "portal": 0, "nuevas": 0}
            nodo = (plantas_nodos or {}).get(codigo)
            if not nodo:
                info["errores"][codigo] = "planta no visible en el portal"
                continue
            e = est_pl.setdefault(codigo, {})
            try:
                sc.abrir_alarmas_historicas(nodo["ne"])
                lista: list[tuple[date, date, str]] = []     # (ini, fin, fase)
                rev = date.fromisoformat(e["revisado_hasta"]) if e.get("revisado_hasta") else None
                if desde:                                    # rango manual: solo esas fechas, sin tocar el relleno
                    lista = [(a, b, "manual") for a, b in ventanas(desde, fin, ventana)]
                else:
                    if rev:                                  # semanal: desde el solape hasta hoy
                        ini = max(inicio, rev - timedelta(days=solape))
                        lista += [(a, b, "adelante") for a, b in ventanas(ini, fin, ventana)]
                    if not e.get("relleno_completo"):        # relleno hacia atrás (más nuevo primero)
                        tope = date.fromisoformat(e["relleno_desde"]) - timedelta(days=1) if e.get("relleno_desde") else fin
                        lista += [(a, b, "atras") for a, b in reversed(ventanas(inicio, tope, ventana))] if tope >= inicio else []
                for a, b, fase in lista:
                    if time.monotonic() > limite:
                        raise TimeoutError("presupuesto de tiempo agotado; la próxima corrida continúa donde quedó")
                    portal, nuevas = _ventana(sc, codigo, a, b, acfg, carpeta, ahora)
                    res["ventanas"] += 1
                    res["portal"] += portal
                    res["nuevas"] += nuevas
                    if fase == "adelante":
                        e["revisado_hasta"] = max(b, date.fromisoformat(e.get("revisado_hasta") or b.isoformat())).isoformat()
                    elif fase == "atras":
                        e["relleno_desde"] = a.isoformat()
                        if not e.get("revisado_hasta"):
                            e["revisado_hasta"] = fin.isoformat()   # la ventana más reciente ya cubre hasta hoy
                    guardar_json(ruta_estado(), estado)
                if not desde and not e.get("relleno_completo") and e.get("relleno_desde") and date.fromisoformat(e["relleno_desde"]) <= inicio:
                    e["relleno_completo"] = True
            except Exception as ex:
                log.exception("alarmas históricas %s: %s", codigo, ex)
                info["errores"][codigo] = str(ex)
            finally:
                guardar_json(ruta_estado(), estado)
        filas = leer_filas()
        for c in codigos:
            fs = [f for f in filas if f["Planta"] == c]
            if fs:
                est_pl.setdefault(c, {}).update(primer_dato=min(f[C_FOCURRE] for f in fs)[:10], ultimo_dato=max(f[C_FOCURRE] for f in fs)[:10],
                                               filas=len(fs))
        estado["ultima_corrida"] = ahora.strftime(FMT_TS)
        guardar_json(ruta_estado(), estado)
    finally:
        shutil.rmtree(carpeta, ignore_errors=True)
    if not info["errores"]:
        info.pop("errores")
    else:
        info["error"] = "; ".join(f"{k}: {v}" for k, v in info["errores"].items())
    log.info("Alarmas históricas: %s", {k: v for k, v in info.items() if k != "errores"})
    return info
