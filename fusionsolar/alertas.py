"""Aviso por correo cuando la resistencia de aislamiento de un inversor llega a <= X % sobre su umbral (o baja de el).

Anti-spam: un aviso por inversor al entrar en la condicion; se repite solo si persiste tras `reenviar_cada_horas`, o si
se recupera y vuelve a caer (con `enfriamiento_horas` minimas desde el ultimo aviso, para no avisar por cada oscilacion
alrededor del limite). Varios inversores de la misma corrida salen juntos en un solo correo.

Secretos (variables de entorno, en GitHub Actions = Secrets): SMTP_USER, SMTP_PASSWORD (y opcionales SMTP_HOST, SMTP_PORT,
ALERTA_REMITENTE, ALERTA_DESTINATARIOS que reemplaza a config). Sin ellos no se envia nada ni se pierde el aviso.
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

from .common import FMT_TS, ROOT, archivo_datos, guardar_json, leer_json

log = logging.getLogger("fusionsolar.alertas")
ARCHIVO = "alertas_estado.json"
POR_DEFECTO = {
    "activo": True,
    "porcentaje_sobre_umbral": 5,
    "destinatarios": [],
    "reenviar_cada_horas": 24,
    "enfriamiento_horas": 2,
    "dashboard_url": "",
    "plantilla": "config/alerta_correo.txt",
}
_SECCIONES = ("ASUNTO", "CABECERA", "INVERSOR", "PIE")
PLANTILLA_RESPALDO = {
    "ASUNTO": "[FusionSolar] Aislamiento cerca del umbral: {inversores}",
    "CABECERA": "La resistencia de aislamiento esta cerca del umbral (<= {porcentaje} %). Lectura del {fecha_hora}:",
    "INVERSOR": "- {planta} {inversor}: {resistencia} MΩ (umbral {umbral} MΩ) -> {estado}",
    "PIE": "Dashboard: {dashboard}",
}


class _Seguro(dict):
    def __missing__(self, k):          # {marcador_mal_escrito} queda tal cual en vez de lanzar KeyError
        return "{" + k + "}"


def _cfg(cfg: dict) -> dict:
    return POR_DEFECTO | (cfg.get("alertas") or {})


def destinatarios(cfg: dict) -> list[str]:
    env = os.environ.get("ALERTA_DESTINATARIOS", "")
    lista = re.split(r"[,;\s]+", env) if env.strip() else _cfg(cfg)["destinatarios"]
    return [d for d in lista if d and "@" in d]


def cargar_plantilla(cfg: dict) -> dict[str, str]:
    """Lee las secciones ### ASUNTO / CABECERA / INVERSOR / PIE; lo que falte o falle usa el texto de respaldo."""
    out = dict(PLANTILLA_RESPALDO)
    try:
        ruta = ROOT / _cfg(cfg)["plantilla"]
        actual, partes = None, {}
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            if linea.startswith("### "):
                actual = linea[4:].strip().upper()
                partes[actual] = []
            elif actual and not linea.startswith("# "):
                partes[actual].append(linea)
        for k in _SECCIONES:
            texto = "\n".join(partes.get(k, [])).strip("\n")
            if texto.strip():
                out[k] = texto
    except Exception as e:
        log.warning("no se pudo leer la plantilla del correo (%s); se usa el texto de respaldo", e)
    return out


def evaluar(cfg: dict, filas: list[dict], estado: dict, ahora: datetime) -> tuple[list[dict], dict]:
    """Decide a quien avisar. Devuelve (avisos_a_enviar, nuevo_estado); el estado de los avisados se confirma al enviar."""
    c = _cfg(cfg)
    pct = float(c["porcentaje_sobre_umbral"])
    reenvio, enfria = timedelta(hours=c["reenviar_cada_horas"]), timedelta(hours=c["enfriamiento_horas"])
    nuevo = {k: dict(v) for k, v in estado.items()}
    avisos = []
    for f in filas:
        try:
            r = float(f.get("Resistencia_MOhm"))
        except (TypeError, ValueError):
            continue                                   # sin dato: no cambia el estado de la alerta
        inv = next((i for i in cfg["inversores"] if i["planta"] == f["Planta"] and i["nombre"] == f["Inversor"]), None)
        if not inv:
            continue
        umbral = float(inv["umbral"])
        limite = umbral * (1 + pct / 100)
        st = nuevo.setdefault(inv["id"], {"en_alerta": False})
        if r > limite:                                 # fuera de la condicion (o recuperado)
            st["en_alerta"] = False
            continue
        ult = _ts(st.get("ultimo_envio"))
        if st.get("en_alerta"):
            enviar = ult is None or ahora - ult >= reenvio         # persiste: recordatorio espaciado
            motivo = "Sigue en esa condicion (recordatorio)"
        else:
            enviar = ult is None or ahora - ult >= enfria          # entra, o vuelve a caer tras recuperarse
            motivo = "Nueva alerta" if ult is None else "Volvio a caer tras recuperarse"
        st["en_alerta"] = True
        if enviar:
            avisos.append({"id": inv["id"], "planta": inv["planta"], "inversor": inv["nombre"], "r": r, "umbral": umbral,
                           "limite": limite, "sobre": (r - umbral) / umbral * 100, "motivo": motivo})
    return avisos, nuevo


def _ts(txt: str | None) -> datetime | None:
    try:
        return datetime.strptime(txt, FMT_TS) if txt else None
    except ValueError:
        return None


def _fmt(v: float) -> str:
    return f"{v:.3f}".replace(".", ",")


def componer(cfg: dict, avisos: list[dict], ahora: datetime) -> tuple[str, str]:
    c, plantilla = _cfg(cfg), cargar_plantilla(cfg)
    base = _Seguro(cantidad=len(avisos), porcentaje=f"{c['porcentaje_sobre_umbral']:g}", fecha_hora=ahora.strftime("%Y-%m-%d %H:%M"),
                   inversores=", ".join(f"{a['planta']} {a['inversor']}" for a in avisos), dashboard=c["dashboard_url"])
    bloques = []
    for a in avisos:
        estado = "POR DEBAJO DEL UMBRAL" if a["r"] < a["umbral"] else "cerca del umbral"
        bloques.append(plantilla["INVERSOR"].format_map(_Seguro(
            base, planta=a["planta"], inversor=a["inversor"], id=a["id"], resistencia=_fmt(a["r"]), umbral=_fmt(a["umbral"]),
            limite=_fmt(a["limite"]), sobre_umbral_pct=f"{a['sobre']:.1f}".replace(".", ","), estado=estado, motivo=a["motivo"])))
    asunto = " ".join(plantilla["ASUNTO"].format_map(base).split())
    cuerpo = "\n".join([plantilla["CABECERA"].format_map(base), *bloques, plantilla["PIE"].format_map(base)]).strip() + "\n"
    return asunto, cuerpo


def enviar_correo(asunto: str, cuerpo: str, para: list[str]) -> None:
    """SMTP con SSL (465) o STARTTLS (587). Lanza si falta configuracion o el servidor rechaza el envio."""
    usuario, clave = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
    if not usuario or not clave:
        raise RuntimeError("faltan los secretos SMTP_USER / SMTP_PASSWORD")
    host, puerto = os.environ.get("SMTP_HOST") or "smtp.gmail.com", int(os.environ.get("SMTP_PORT") or 465)
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = asunto, os.environ.get("ALERTA_REMITENTE") or usuario, ", ".join(para)
    msg.set_content(cuerpo)
    ctx = ssl.create_default_context()
    if puerto == 465:
        with smtplib.SMTP_SSL(host, puerto, context=ctx, timeout=30) as s:
            s.login(usuario, clave)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, puerto, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(usuario, clave)
            s.send_message(msg)


def procesar(cfg: dict, filas: list[dict], ahora: datetime) -> dict:
    """Evalua las lecturas de esta corrida y envia el aviso si corresponde. Nunca lanza."""
    try:
        if not _cfg(cfg)["activo"] or not filas:
            return {}
        ruta = archivo_datos(ARCHIVO)
        estado = leer_json(ruta, {})
        avisos, nuevo = evaluar(cfg, filas, estado, ahora)
        a_guardar = {k: v for k, v in nuevo.items()}
        enviados = 0
        if avisos:
            para = destinatarios(cfg)
            try:
                if not para:
                    raise RuntimeError("no hay destinatarios (config alertas.destinatarios o ALERTA_DESTINATARIOS)")
                asunto, cuerpo = componer(cfg, avisos, ahora)
                enviar_correo(asunto, cuerpo, para)
                enviados = len(avisos)
                for a in avisos:
                    a_guardar[a["id"]]["ultimo_envio"] = ahora.strftime(FMT_TS)
                log.info("Aviso enviado (%s inversores) a %s", enviados, ", ".join(para))
            except Exception as e:                      # sin enviar no se marca nada: se reintenta en la proxima corrida
                log.warning("No se pudo enviar el aviso (%s); se reintentara en la proxima corrida", e)
                for a in avisos:                        # que no cuente como "ya en alerta avisada"
                    a_guardar[a["id"]]["en_alerta"] = estado.get(a["id"], {}).get("en_alerta", False)
        if a_guardar != estado:
            guardar_json(ruta, a_guardar)
        return {"pendientes": len(avisos), "enviados": enviados}
    except Exception as e:
        log.exception("fallo en el modulo de alertas: %s", e)
        return {"error": str(e)}


def enviar_prueba(cfg: dict, ahora: datetime) -> None:
    """Correo de prueba con datos de ejemplo (python run.py --probar-correo). No toca el estado de las alertas."""
    ejemplo = [{"id": "VES-Inv2", "planta": "VES", "inversor": "Inv2", "r": 0.038, "umbral": 0.037, "limite": 0.03885,
                "sobre": 2.7, "motivo": "Nueva alerta"}]
    asunto, cuerpo = componer(cfg, ejemplo, ahora)
    para = destinatarios(cfg)
    if not para:
        raise SystemExit("No hay destinatarios (config alertas.destinatarios o ALERTA_DESTINATARIOS).")
    enviar_correo("[PRUEBA] " + asunto, "(Correo de prueba con datos de ejemplo)\n\n" + cuerpo, para)
    log.info("Correo de prueba enviado a %s", ", ".join(para))
