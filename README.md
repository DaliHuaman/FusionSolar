# Indicadores de inversores FusionSolar

Cada 30 minutos, GitHub entra a FusionSolar, lee de los 10 inversores (LC, VES y PL) la
**resistencia de aislamiento**, el **estado de ajuste de potencia activa**, la **temperatura interna** y la **hora de inicio**, y los agrega a un Excel.
También lee las **temperaturas de las 5 BESS** (Control › cada BESS › Detalles), calcula la **disponibilidad diaria** de cada planta y publica un dashboard web con todo.

## Qué hay en el repositorio (y para qué sirve)

| Archivo | Para qué sirve |
|---|---|
| `.github/workflows/actualizar.yml` | **El programador**: ejecuta todo cada 30 min en GitHub |
| `.github/workflows/disponibilidad.yml` | Cada día a las **00:00 de Lima** lee las horas operativas del día anterior (Disponibilidad) |
| `fusionsolar/disponibilidad.py` | Fórmula de disponibilidad, lectura del Informe de inversores y `disponibilidad.csv` |
| `run.py` | El código principal que se ejecuta (orquesta todo) |
| `fusionsolar/scraper.py` | Entra al portal y lee los valores (scraping) |
| `fusionsolar/excel_indicadores.py` y `store.py` | Escriben las lecturas en el Excel |
| `fusionsolar/sitedata.py` | Prepara los datos del dashboard web |
| `config/inversores.json` | Lista de inversores, sus **umbrales** (valor inicial) y la configuración de umbrales y alertas |
| `fusionsolar/umbrales.py` | Lee del portal el umbral de cada inversor cada hora y lo guarda |
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

## Temperatura interna y hora de inicio del inversor

Salen de *Real-Time Device Data* del portal («Temperatura interna» y «Fecha y hora de inicio del inversor») y se guardan en cada corrida:

- **Historial**: dos columnas nuevas al FINAL de `historico.csv` y de la hoja `Historico` del Excel: `Inicio_Inversor` y `Temp_Interna_C`. El historial anterior no se pierde: al primer guardado el CSV se reescribe con las columnas nuevas (vacías en las filas viejas) y el Excel se reconstruye solo desde el CSV.
- **Dashboard**: cada tarjeta muestra la temperatura y la hora de inicio actuales; debajo de «Estado de ajuste de potencia activa» hay una gráfica de temperatura interna en el tiempo y otra con la hora del día a la que arrancó cada inversor (un punto por arranque).
- Si el portal devuelve `--` (por ejemplo de noche), esa lectura queda vacía y la gráfica deja un hueco.

## Dashboard: umbral en el tiempo y zoom

- La línea punteada de umbral usa el umbral guardado en cada lectura (`Umbral_MOhm`), así que **salta cuando el umbral cambia** en el portal en vez de mostrar solo el último valor. Las tablas de mínimo diario y «Datos del Excel» también comparan contra el umbral vigente en ese momento.
- **Zoom en todas las gráficas** (chartjs-plugin-zoom): elegir *Horizontal*, *Vertical* o *Ambos* y arrastrar para seleccionar el rango; `Ctrl + rueda` acerca/aleja, `Mayús + arrastrar` desplaza, doble clic o «⟲ Restablecer» vuelve a la vista completa. Funciona junto con los rangos 24 h / 7 días / 30 días / Todo. El zoom se conserva cuando llegan datos nuevos y se reinicia al cambiar de planta o de rango.

## Disponibilidad de las plantas

Mide cuántas horas al día está cada inversor conectado a la red. **Solo las horas se leen de FusionSolar**; la disponibilidad es una fórmula, la misma de la hoja `Disponibilidad.xlsx`:

```
disponibilidad = SI(horas >= 12; 1; horas / 12)      # 12 h = horas de sol de referencia (config: disponibilidad.horas_referencia)
```

- **De dónde salen las horas**: FusionSolar › *Informes › Informe de inversores*, granularidad *Por día*, columna **«Duración de conexión a la red (h)»**. El recolector llama al mismo servicio REST que usa esa pantalla (≈0,3 s por día); si fallara, escribe la fecha en la pantalla, pulsa *Buscar* y lee la tabla.
- **Cuándo**: `disponibilidad.yml` corre **todos los días a las 00:07 de Lima** (05:07 UTC; Lima no tiene horario de verano) y guarda las horas de **ayer**. También repasa los últimos `releer_dias` (3) por si el portal completa datos tardíos. Como GitHub puede demorar los `schedule`, conviene crear en cron-job.org un trabajo diario a las 00:00 (zona America/Lima) con la misma llamada `workflow_dispatch` que usa `actualizar.yml`, apuntando a `disponibilidad.yml`.
- **Historial**: la primera ejecución recorre todo desde `inicio_busqueda` (2025-01-01) hasta ayer y descubre sola la primera fecha con datos (Logiscity desde feb 2025, Villa el Salvador desde sep 2025 y PL desde oct 2026); tarda unos 3 minutos. Si un día falla, la revisión no avanza y la corrida siguiente lo recupera. Para recorrer un rango a mano: *Actions › Disponibilidad diaria › Run workflow* (`desde` / `hasta`) o `python run.py --disponibilidad --desde 2025-01-01`.
- **Qué se guarda**: `disponibilidad.csv` y `disponibilidad_estado.json` en la rama `data` (una fila por día e inversor: `Fecha, Planta, Grupo, Inversor, Horas, Leido`).
- **Día sin conexión**: si el inversor aparece ese día en el informe con rendimiento pero **sin** «Duración de conexión a la red», se toma como **0 h** (0 %). Si el portal no trae nada del inversor (antes de su puesta en marcha o sin comunicación), el día queda **sin dato** y no se grafica.
- **Excel**: hoja `Disponibilidad` (tercera, tras `BESS`) con el formato de Maestro: una fila por día desde `disponibilidad.excel_desde` (2026-10-01), por inversor «Horas operativas» y «Disponibilidad» (la fórmula `=IF(B4>=12,1,B4/12)`, formato 0,00 %). Se reconstruye entera en cada corrida desde el CSV.
- **Dashboard**: botón **Disponibilidad** junto a BESS: gráfico de barras por planta (promedio de sus inversores) **por día, mes o año** con el zoom de las demás gráficas, tarjetas con el último día y el mes, y la tabla diaria. En «Datos del Excel» hay una pestaña nueva **Disponibilidad** (igual que la hoja del archivo).

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
- Estados: **ALARMA** (valor < umbral), **ADVERTENCIA** (hasta 5 % sobre el umbral, `margen_advertencia`), **OK**, **SIN_DATO**.
- Para probar en tu PC: instalar Python, `pip install -r requirements.txt`, `python -m playwright install chromium`,
  definir `FUSIONSOLAR_USER`, `FUSIONSOLAR_PASSWORD` y `EXCEL_DIR` (carpeta donde quieres el Excel) y ejecutar `python run.py`.
- Nunca escribas usuario/contraseña en archivos del repositorio (es público).

## Mantenimiento (lo que conviene saber)

- **Tamaño**: el Excel crece ~30 KB por día. Al llegar a 200.000 filas de la hoja *Historico* (~14 meses) se crea solo `Indicadores_FusionSolar_parte2.xlsx`; habrá que actualizar el enlace de descarga/Power BI.
- **Cambio de diseño del Excel**: si el archivo existente tiene un diseño antiguo, se reconstruye solo desde `historico.csv` (no se pierde nada).
- **Datos `--` del portal**: a veces FusionSolar devuelve `--` en un inversor; el recolector reintenta una vez en la misma corrida y, si persiste, guarda SIN_DATO.
- **Token de cron-job.org**: si caduca o se revoca, las ejecuciones dejan de dispararse (se ven en rojo en cron-job.org). Crear otro token con permiso *Actions: Read and write* y cambiar la cabecera.
- **Contraseña del portal**: si cambia, actualizar los secretos `FUSIONSOLAR_USER` / `FUSIONSOLAR_PASSWORD`.
