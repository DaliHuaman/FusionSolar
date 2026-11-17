"""Aviso por correo cuando la resistencia de aislamiento de un inversor llega a <= X % sobre su umbral (o baja de el).

Anti-spam: un aviso por inversor al entrar en la condicion; se repite solo si persiste tras `reenviar_cada_horas`, o si
se recupera y vuelve a caer (con `enfriamiento_horas` minimas desde el ultimo aviso, para no avisar por cada oscilacion
alrededor del limite). Varios inversores de la misma corrida salen juntos en un solo correo.

Secretos (variables de entorno, en GitHub Actions = Secrets): SMTP_USER, SMTP_PASSWORD (y opcionales SMTP_HOST, SMTP_PORT,
ALERTA_REMITENTE, ALERTA_DESTINATARIOS y ALERTA_COPIA que reemplazan a config). Sin ellos no se envia nada ni se pierde el aviso.
"""
from __future__ import annotations

import html as html_lib
import logging
import os
import re
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage

from . import umbrales
from .common import FMT_TS, ROOT, archivo_datos, guardar_json, leer_json

log = logging.getLogger("fusionsolar.alertas")
ARCHIVO = "alertas_estado.json"
POR_DEFECTO = {
    "activo": True,
    "porcentaje_sobre_umbral": 5,
    "destinatarios": [],
    "copia": [],
    "reenviar_cada_horas": 24,
    "enfriamiento_horas": 2,
    "dashboard_url": "",
    "plantilla": "config/alerta_correo.txt",
}
_SECCIONES = ("ASUNTO", "CUERPO")
PLANTILLA_RESPALDO = {
    "ASUNTO": "FusionSolar - Aislamiento cerca del umbral: {inversores}",
    "CUERPO": "Estimados,\n\nLa resistencia de aislamiento de {n_inversores} está a {porcentaje} % o menos por encima de su "
              "umbral. Lectura del {fecha_hora} (hora de Lima).\n\n{recuadro}\n\nDashboard: {dashboard}\n",
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


def copia(cfg: dict) -> list[str]:
    """Destinatarios en copia (CC); se excluyen los que ya van en 'Para'."""
    env = os.environ.get("ALERTA_COPIA", "")
    lista = re.split(r"[,;\s]+", env) if env.strip() else _cfg(cfg)["copia"]
    para = {d.lower() for d in destinatarios(cfg)}
    return [d for d in lista if d and "@" in d and d.lower() not in para]


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


def _recuadro(avisos: list[dict]) -> str:
    """Version texto plano (fallback legible, sin tabla ASCII): una linea por inversor."""
    out = []
    for a in avisos:
        dif = f"{a['sobre']:+.1f} %".replace(".", ",").replace("-", "−")
        estado = "Bajo el umbral" if a["r"] < a["umbral"] else "Cerca del umbral"
        out.append(f"• {a['planta']} · {a['inversor']}: {_fmt(a['r'])} MΩ (umbral {_fmt(a['umbral'])} MΩ, {dif}) - {estado}")
    return "\n".join(out)


# Paleta Kondu (misma del dashboard, tema claro)
K = {"brand": "#270d44", "brand2": "#3d1a66", "acc": "#a3195f", "hi": "#e11d73", "bg": "#f6f4f9", "panel": "#ffffff",
     "borde": "#e6e1ec", "fg": "#1a1325", "fg2": "#4a4357", "fg3": "#6b6479", "dim": "#d9cde8",
     "warn": "#8a5a00", "warn_bg": "#fff1cf", "bad": "#c4271b", "bad_bg": "#fde6e3"}
FUENTE = "Inter,'Segoe UI',Roboto,Arial,sans-serif"
_MARCA = "\x00TABLA\x00"


def _tabla_html(avisos: list[dict]) -> str:
    """Tabla HTML con estilos inline y atributos bgcolor/width (compatible con Outlook de escritorio)."""
    def th(t, al):
        return (f'<td align="{al}" bgcolor="{K["brand"]}" style="padding:9px 12px;background:{K["brand"]};color:#ffffff;'
                f'font:600 12px {FUENTE};text-align:{al};white-space:nowrap">{t}</td>')
    filas = ['<tr>' + th("Inversor", "left") + th("Resistencia", "right") + th("Umbral", "right") + th("Dif.", "right")
             + th("Estado", "center") + '</tr>']
    for i, a in enumerate(avisos):
        bajo = a["r"] < a["umbral"]
        fg, bg, txt = (K["bad"], K["bad_bg"], "Bajo") if bajo else (K["warn"], K["warn_bg"], "Cerca")
        fondo = K["panel"] if i % 2 == 0 else K["bg"]
        dif = f"{a['sobre']:+.1f} %".replace(".", ",").replace("-", "−")
        base = (f'padding:10px 12px;border-bottom:1px solid {K["borde"]};font:14px {FUENTE};color:{K["fg"]};white-space:nowrap')
        filas.append(
            f'<tr bgcolor="{fondo}" style="background:{fondo}">'
            f'<td align="left" style="{base};font-weight:600">{html_lib.escape(a["planta"])} · {html_lib.escape(a["inversor"])}</td>'
            f'<td align="right" style="{base}">{_fmt(a["r"])} MΩ</td>'
            f'<td align="right" style="{base};color:{K["fg2"]}">{_fmt(a["umbral"])} MΩ</td>'
            f'<td align="right" style="{base};color:{fg};font-weight:600">{dif}</td>'
            f'<td align="center" style="{base}"><span style="display:inline-block;padding:2px 10px;border-radius:999px;'
            f'background:{bg};color:{fg};font:600 12px {FUENTE}">{txt}</span></td></tr>')
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="border:1px solid {K["borde"]};border-collapse:separate;border-radius:10px;overflow:hidden">'
            + "".join(filas) + '</table>')


def _parrafo(txt: str, url: str, muted: bool = False) -> str:
    esc = html_lib.escape(txt).replace("\n", "<br>")
    u = html_lib.escape(url) if url else ""
    estilo = (f'font:13px/1.5 {FUENTE};color:{K["fg3"]}' if muted else f'font:15px/1.55 {FUENTE};color:{K["fg"]}')
    if u and u in esc:
        antes, _, despues = esc.partition(u)
        antes = antes.rstrip().rstrip(":").rstrip() + ":" if antes.strip() else ""
        boton = (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:6px 0 2px"><tr>'
                 f'<td bgcolor="{K["acc"]}" style="background:{K["acc"]};border-radius:8px">'
                 f'<a href="{u}" style="display:inline-block;padding:10px 20px;font:600 14px {FUENTE};color:#ffffff;'
                 f'text-decoration:none">Abrir dashboard</a></td></tr></table>'
                 f'<div style="font:12px {FUENTE};color:{K["fg3"]};margin:4px 0 18px">{u}</div>')
        pre = f'<p style="margin:0 0 6px;{estilo}">{antes}</p>' if antes else ""
        pos = f'<p style="margin:8px 0 0;{estilo}">{despues.strip()}</p>' if despues.strip() else ""
        return pre + boton + pos
    return f'<p style="margin:0 0 16px;{estilo}">{esc}</p>'


def _html_correo(cuerpo: str, avisos: list[dict], url: str, asunto: str) -> str:
    """Correo HTML: cabecera Kondu, parrafos de la plantilla, tabla de inversores y pie. `cuerpo` lleva _MARCA donde va la tabla."""
    parrafos = [p.strip("\n") for p in re.split(r"\n\s*\n", cuerpo.strip()) if p.strip()]
    bloques = []
    for i, p in enumerate(parrafos):
        if p.strip() == _MARCA:
            bloques.append(f'<div style="margin:2px 0 20px">{_tabla_html(avisos)}</div>')
        else:
            bloques.append(_parrafo(p, url, muted=(i == len(parrafos) - 1 and len(parrafos) > 2 and not (url and url in p))))
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><meta name="supported-color-schemes" content="light only">
<title>{html_lib.escape(asunto)}</title></head>
<body style="margin:0;padding:0;background:{K['bg']}" bgcolor="{K['bg']}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{K['bg']}" style="background:{K['bg']}"><tr><td align="center" style="padding:20px 10px">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" border="0" style="width:100%;max-width:640px;border-radius:14px;overflow:hidden;border:1px solid {K['borde']}">
<tr><td bgcolor="{K['brand']}" style="background:{K['brand']};padding:20px 24px">
<div style="font:700 22px {FUENTE};color:#ffffff;letter-spacing:-.5px">kondu</div>
<div style="font:700 17px {FUENTE};color:#ffffff;margin-top:10px">Aislamiento cerca del umbral</div>
<div style="font:13px {FUENTE};color:{K['dim']};margin-top:2px">Monitoreo de las plantas de BSF · FusionSolar</div></td></tr>
<tr><td height="4" bgcolor="{K['hi']}" style="height:4px;line-height:4px;font-size:0;background:{K['hi']};background-image:linear-gradient(90deg,#e11d73,#f37828)">&nbsp;</td></tr>
<tr><td bgcolor="{K['panel']}" style="background:{K['panel']};padding:24px">{"".join(bloques)}</td></tr>
<tr><td bgcolor="{K['bg']}" style="background:{K['bg']};padding:12px 24px;border-top:1px solid {K['borde']};font:12px {FUENTE};color:{K['fg3']}">Aviso automático · Kondu · FusionSolar</td></tr>
</table></td></tr></table></body></html>"""


def _resumen(avisos: list[dict]) -> str:
    """'PL (InvB.2, InvB.3), VES (Inv2)' en el orden de aparicion."""
    por_planta: dict[str, list[str]] = {}
    for a in avisos:
        por_planta.setdefault(a["planta"], []).append(a["inversor"])
    return ", ".join(f"{p} ({', '.join(v)})" for p, v in por_planta.items())


def componer(cfg: dict, avisos: list[dict], ahora: datetime) -> tuple[str, str, str]:
    """(asunto, cuerpo_texto, cuerpo_html). Texto plano legible como respaldo; HTML con tablas e inline styles (Outlook)."""
    c, plantilla = _cfg(cfg), cargar_plantilla(cfg)
    datos = _Seguro(cantidad=len(avisos), n_inversores=f"{len(avisos)} inversor" + ("es" if len(avisos) != 1 else ""), inversores=_resumen(avisos), porcentaje=f"{c['porcentaje_sobre_umbral']:g}",
                    fecha_hora=ahora.strftime("%d/%m/%Y %H:%M"), recuadro=_recuadro(avisos), dashboard=c["dashboard_url"])
    asunto = " ".join(plantilla["ASUNTO"].format_map(datos).split())
    cuerpo = plantilla["CUERPO"].format_map(datos).strip() + "\n"
    html = _html_correo(plantilla["CUERPO"].format_map(datos | {"recuadro": _MARCA}).strip(), avisos, c["dashboard_url"], asunto)
    return asunto, cuerpo, html


def enviar_correo(asunto: str, cuerpo: str, para: list[str], html: str | None = None, cc: list[str] | None = None) -> None:
    """SMTP con SSL (465) o STARTTLS (587). Lanza si falta configuracion o el servidor rechaza el envio."""
    usuario, clave = os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", "")
    if not usuario or not clave:
        raise RuntimeError("faltan los secretos SMTP_USER / SMTP_PASSWORD")
    host, puerto = os.environ.get("SMTP_HOST") or "smtp.gmail.com", int(os.environ.get("SMTP_PORT") or 465)
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = asunto, os.environ.get("ALERTA_REMITENTE") or usuario, ", ".join(para)
    if cc:
        msg["Cc"] = ", ".join(cc)                       # send_message entrega a Para + Cc
    msg.set_content(cuerpo)
    if html:
        msg.add_alternative(html, subtype="html")
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
                asunto, cuerpo, html = componer(cfg, avisos, ahora)
                enviar_correo(asunto, cuerpo, para, html, copia(cfg))
                enviados = len(avisos)
                for a in avisos:
                    a_guardar[a["id"]]["ultimo_envio"] = ahora.strftime(FMT_TS)
                log.info("Aviso enviado (%s inversores) a %s (copia: %s)", enviados, ", ".join(para), ", ".join(copia(cfg)) or "-")
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
    # Umbral REAL de cada inversor (el mismo que usa evaluar): el leido del portal (umbrales.json) o el del JSON.
    umbrales.aplicar(cfg)
    ejemplo = []
    for iid, r_rel in (("VES-Inv2", 0.02), ("PL-InvB.2", 0.03)):
        inv = next((i for i in cfg["inversores"] if i["id"] == iid), None)
        if not inv:
            continue
        u = float(inv["umbral"])
        r = round(u * (1 + r_rel), 3)                  # lectura de ejemplo: unos % sobre el umbral real
        ejemplo.append({"id": iid, "planta": inv["planta"], "inversor": inv["nombre"], "r": r, "umbral": u,
                        "limite": u * (1 + float(_cfg(cfg)["porcentaje_sobre_umbral"]) / 100),
                        "sobre": (r - u) / u * 100, "motivo": "Nueva alerta"})
    asunto, cuerpo, html = componer(cfg, ejemplo, ahora)
    para = destinatarios(cfg)
    if not para:
        raise SystemExit("No hay destinatarios (config alertas.destinatarios o ALERTA_DESTINATARIOS).")
    banner = ('<tr><td bgcolor="#fff1cf" style="background:#fff1cf;padding:6px 24px;font:12px Arial,sans-serif;color:#8a5a00">'
              'Correo de prueba con datos de ejemplo</td></tr>')
    html = html.replace('<tr><td height="4"', banner + '<tr><td height="4"', 1)
    enviar_correo("[PRUEBA] " + asunto, "(Correo de prueba con datos de ejemplo)\n\n" + cuerpo, para, html, copia(cfg))
    log.info("Correo de prueba enviado a %s (copia: %s)", ", ".join(para), ", ".join(copia(cfg)) or "-")
