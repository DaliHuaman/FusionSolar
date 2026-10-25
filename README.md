# Indicadores de inversores FusionSolar

Cada 30 minutos, GitHub entra a FusionSolar, lee de los 10 inversores (LC, VES y PL) la
**resistencia de aislamiento** y el **estado de ajuste de potencia activa**, y los agrega a un Excel.
También lee las **temperaturas de las 5 BESS** (Control › cada BESS › Detalles) y publica un dashboard web con todo.

## Qué hay en el repositorio (y para qué sirve)

| Archivo | Para qué sirve |
|---|---|
| `.github/workflows/actualizar.yml` | **El programador**: ejecuta todo cada 30 min en GitHub |
| `run.py` | El código principal que se ejecuta (orquesta todo) |
| `fusionsolar/scraper.py` | Entra al portal y lee los valores (scraping) |
| `fusionsolar/excel_indicadores.py` y `store.py` | Escriben las lecturas en el Excel |
| `fusionsolar/sitedata.py` | Prepara los datos del dashboard web |
| `config/inversores.json` | Lista de inversores, sus **umbrales** (valor inicial) y la configuración de umbrales y alertas |
| `fusionsolar/umbrales.py` | Lee del portal el umbral de cada inversor cada 8 h y lo guarda |
| `fusionsolar/alertas.py` y `config/alerta_correo.txt` | Aviso por correo y texto editable del mensaje |
| `plantilla/Indicadores_FusionSolar.xlsx` | Tu plantilla de Excel (punto de partida) |
| `docs/` | El dashboard web |
| `requirements.txt` | Librerías de Python que necesita |

## Dónde queda el Excel que se va actualizando

**No** está en la rama principal. Se crea en la rama **`data`** después de la primera ejecución:

- Ver: repo → botón de ramas → `data` → `Indicadores_FusionSolar.xlsx`
- Descargar / enlace directo (para Power BI):
  `https://raw.githubusercontent.com/DaliHuaman/FusionSolar/data/Indicadores_FusionSolar.xlsx`

Tiene dos hojas: **Indicadores** (tu plantilla, una fila por corrida, con una columna vacía separando LC | VES | PL-A | PL-B y los umbrales en Z:AA) e **Historico** (una fila por lectura e inversor, ideal para Power BI).

## Temperaturas BESS

Por cada BESS (BESS-01 a BESS-05 de la planta PL) se guarda: *Temperatura más alta del grupo* y las 5 de *Información de enfriamiento* (agua suministrada y de retorno lado batería, agua suministrada y de retorno lado potencia, y exterior).

- **Excel**: segunda hoja `BESS`, una fila por corrida con un bloque de 6 columnas (°C) por BESS.
- **Dashboard**: apartado «BESS · Temperaturas» con una tarjeta por BESS y una comparación de la temperatura más alta.
- **Historial acotado**: `historico_bess.csv` (rama `data`) y la hoja `BESS` conservan solo los últimos `bess.dias_historial` días (90 por defecto); el dashboard muestra 7.
- **Sin riesgo para los inversores**: la lectura BESS ocurre después de los inversores, en la misma sesión de login, con un tope de 4 minutos y sin lanzar excepciones. Si falla, los inversores se guardan igual y el dashboard avisa qué BESS no se leyó. Para apagarlo: `"activo": false` en `config/inversores.json`.

## Umbrales de aislamiento (se leen del portal)

El umbral de cada inversor se lee de FusionSolar (inversor › Control › Configuración › Parámetros de protección › *Umbral de protección de resistencia de aislamiento*), **solo lectura**: nunca se pulsa Guardar.

- Se lee en la primera corrida de cada ventana de `umbrales.horas` (00:00, 08:00 y 16:00 hora de Lima) y se guarda en `umbrales.json` (rama `data`). Las demás corridas reutilizan ese archivo y no abren la pestaña Configuración.
- Si el umbral cambió en el portal, se actualiza solo (el cambio queda en `cambios` dentro de `umbrales.json`). Si la lectura falla, se conserva el valor anterior y se reintenta pasados `reintento_minutos`; `presupuesto_segundos` acota el tiempo y un error aquí nunca impide leer las resistencias.
- Los valores de `config/inversores.json` son el valor inicial y el respaldo.

## Alertas por correo

Se envía un correo cuando la resistencia de un inversor es **≤ `porcentaje_sobre_umbral` % por encima de su umbral, o menor** (`alertas` en `config/inversores.json`, 5 % por defecto).

- **Sin spam**: un aviso por inversor al entrar en la condición; se repite solo si persiste tras `reenviar_cada_horas` (24), o si se recupera y vuelve a caer (con `enfriamiento_horas`, 2, mínimas desde el último aviso). Los inversores de una misma corrida salen en un solo correo. Si el envío falla, se reintenta en la siguiente corrida.
- **Destinatarios**: `alertas.destinatarios` o, si existe, la variable de Actions `ALERTA_DESTINATARIOS` (separados por coma; útil para no dejar el correo en un repo público).
- **Texto**: `config/alerta_correo.txt` (asunto y cuerpo con `{marcadores}`; el recuadro con inversor, resistencia, umbral, diferencia y estado se genera solo). Se envía en texto plano y HTML monoespaciado para que el recuadro quede alineado.
- **Secretos** (*Settings → Secrets and variables → Actions*): `SMTP_USER` (cuenta Gmail remitente) y `SMTP_PASSWORD` (contraseña de aplicación de 16 caracteres). Opcionales como *Variables*: `SMTP_HOST`, `SMTP_PORT`, `ALERTA_REMITENTE`, `ALERTA_DESTINATARIOS`. Sin secretos no se envía nada y el resto sigue igual.
- **Probar**: *Actions → Run workflow* marcando «probar_correo», o `python run.py --probar-correo`.

## Puesta en marcha (una sola vez)

1. **Secretos**: repo → *Settings → Secrets and variables → Actions → New repository secret*.
   Crear `FUSIONSOLAR_USER` y `FUSIONSOLAR_PASSWORD` con el usuario y la contraseña del portal.
2. **Pages** (dashboard web): *Settings → Pages → Source: GitHub Actions*.
3. **Primera ejecución**: pestaña *Actions → "Actualizar indicadores FusionSolar" → Run workflow*.
   Tarda unos 3–5 minutos. Si sale en verde, ya existe la rama `data` con el Excel.
4. Desde ahí se ejecuta solo cada 30 min.

## Notas

- GitHub puede retrasar unos minutos las ejecuciones programadas y las desactiva si el repo pasa 60 días sin actividad.
- Estados: **ALARMA** (valor < umbral), **ADVERTENCIA** (hasta 10 % sobre el umbral), **OK**, **SIN_DATO**.
- Para probar en tu PC: instalar Python, `pip install -r requirements.txt`, `python -m playwright install chromium`,
  definir `FUSIONSOLAR_USER`, `FUSIONSOLAR_PASSWORD` y `EXCEL_DIR` (carpeta donde quieres el Excel) y ejecutar `python run.py`.
- Nunca escribas usuario/contraseña en archivos del repositorio (es público).

## Mantenimiento (lo que conviene saber)

- **Tamaño**: el Excel crece ~30 KB por día. Al llegar a 200.000 filas de la hoja *Historico* (~14 meses) se crea solo `Indicadores_FusionSolar_parte2.xlsx`; habrá que actualizar el enlace de descarga/Power BI.
- **Cambio de diseño del Excel**: si el archivo existente tiene un diseño antiguo, se reconstruye solo desde `historico.csv` (no se pierde nada).
- **Datos `--` del portal**: a veces FusionSolar devuelve `--` en un inversor; el recolector reintenta una vez en la misma corrida y, si persiste, guarda SIN_DATO.
- **Token de cron-job.org**: si caduca o se revoca, las ejecuciones dejan de dispararse (se ven en rojo en cron-job.org). Crear otro token con permiso *Actions: Read and write* y cambiar la cabecera.
- **Contraseña del portal**: si cambia, actualizar los secretos `FUSIONSOLAR_USER` / `FUSIONSOLAR_PASSWORD`.
