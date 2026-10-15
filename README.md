# Indicadores de inversores FusionSolar

Cada 30 minutos, GitHub entra a FusionSolar, lee de los 10 inversores (LC, VES y PL) la
**resistencia de aislamiento** y el **estado de ajuste de potencia activa**, y los agrega a un Excel.
También publica un dashboard web con el mismo dato.

## Qué hay en el repositorio (y para qué sirve)

| Archivo | Para qué sirve |
|---|---|
| `.github/workflows/actualizar.yml` | **El programador**: ejecuta todo cada 30 min en GitHub |
| `run.py` | El código principal que se ejecuta (orquesta todo) |
| `fusionsolar/scraper.py` | Entra al portal y lee los valores (scraping) |
| `fusionsolar/excel_indicadores.py` y `store.py` | Escriben las lecturas en el Excel |
| `fusionsolar/sitedata.py` | Prepara los datos del dashboard web |
| `config/inversores.json` | Lista de inversores y sus **umbrales** |
| `plantilla/Indicadores_FusionSolar.xlsx` | Tu plantilla de Excel (punto de partida) |
| `docs/` | El dashboard web |
| `requirements.txt` | Librerías de Python que necesita |

## Dónde queda el Excel que se va actualizando

**No** está en la rama principal. Se crea en la rama **`data`** después de la primera ejecución:

- Ver: repo → botón de ramas → `data` → `Indicadores_FusionSolar.xlsx`
- Descargar / enlace directo (para Power BI):
  `https://raw.githubusercontent.com/DaliHuaman/FusionSolar/data/Indicadores_FusionSolar.xlsx`

Tiene dos hojas: **Indicadores** (tu plantilla, una fila por corrida, con una columna vacía separando LC | VES | PL-A | PL-B y los umbrales en Z:AA) e **Historico** (una fila por lectura e inversor, ideal para Power BI).

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
