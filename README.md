# Indicadores de inversores FusionSolar

Cada 30 minutos, GitHub entra a FusionSolar, lee de los 10 inversores (LC, VES y PL) la
**resistencia de aislamiento**, el **estado de ajuste de potencia activa**, la **temperatura interna** y la **hora de inicio**, y los agrega a un Excel.
También lee las **temperaturas de las 5 BESS** (Control › cada BESS › Detalles), calcula la **disponibilidad diaria** de cada planta y publica un dashboard web con todo.

## Qué hay en el repositorio (y para qué sirve)

| Archivo | Para qué sirve |
|---|---|
| `.github/workflows/actualizar.yml` | **El programador**: ejecuta todo cada 30 min en GitHub |
| `.github/workflows/disponibilidad.yml` | Cada día a las **12:00 de Lima** lee las horas operativas del día anterior (Disponibilidad) |
| `.github/workflows/alarmas_historicas.yml` | Cada **domingo 12:00 de Lima** exporta las *Alarmas históricas* del portal (LC, PL y VES) y las acumula |
| `.github/workflows/energia_exportada.yml` | Cada **hora al minuto 15 (Lima)** lee la *energía exportada a la red* de cada planta y avisa por correo |
| `fusionsolar/energia_exportada.py` y `config/alerta_energia.txt` | Histórico de energía exportada (`energia_exportada.csv`, solo horas > 0), relleno inicial y texto del aviso |
| `fusionsolar/alarmas_historicas.py` | Lectura del Excel exportado, acumulado sin pisar nada (`alarmas_historicas.csv`) y relleno inicial |
| `fusionsolar/disponibilidad.py` | Fórmula de disponibilidad, lectura del Informe de inversores y `disponibilidad.csv` |
| `run.py` | El código principal que se ejecuta (orquesta todo) |
| `fusionsolar/scraper.py` | Entra al portal y lee los valores (scraping) |
| `fusionsolar/bess_cd.py` | Hoja «BESS carga-descarga»: °C Min / Máx, Salto °C, Tramo y Fuera de Rango de cada BESS (HFP / HP) |
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

## Alarmas históricas (acumuladas para siempre)

FusionSolar **borra periódicamente** las alarmas históricas por límite de almacenamiento. Para conservarlas todas, cada domingo a las 12:00 (Lima) el workflow `alarmas_historicas.yml` entra a *Alarmas › Alarmas históricas* de cada planta, pulsa **Exportar** (el mismo Excel que se baja a mano) y **suma** lo nuevo a `alarmas_historicas.csv` (rama `data`):

- **Excel**: tres hojas, `Alarma Histórica PL`, `Alarma Histórica LC` y `Alarma Histórica VES`, con el encabezado morado/amarillo de Kondu. Mismas columnas que el portal, pero **«Fecha y hora en que ha ocurrido» va antes de «Fecha y hora de eliminación»**. Lo más reciente arriba, con filtros.
- **Dashboard**: en *Datos del Excel* hay una pestaña por hoja, con búsqueda, filtro de gravedad, filtro por mes y paginación (50 filas). Las filas van en `data/alarmas_historicas.json`, que el navegador solo descarga al abrir esas pestañas (textos repetidos en diccionario; ~1 MB sin comprimir por cada 10 000 alarmas).
- **Nunca se pierde ni se pisa una alarma**: clave = planta + SN + ID de alarma + causa + fecha y hora de ocurrencia. Una alarma ya guardada no se reemplaza (solo se rellenan celdas vacías). Antes de reescribir el CSV queda una copia (`alarmas_historicas.csv.bak`) y nunca se escribe un resultado con menos filas.
- **Sin alarmas repetidas**: el portal a veces exporta la misma alarma en dos filas (solo cambia la hora de eliminación). Mismo lote → se guarda una sola por clave (la de eliminación más tardía); contra lo ya guardado manda lo guardado.
- **Archivos bajados a mano**: se dejan en `alarmas_manuales/` (`PL_mayores.csv`, `PL_menores.csv`, `LC.xlsx`, `VES.xlsx`) y se suman con *Actions → Importar alarmas históricas manuales* (`python run.py --importar-alarmas alarmas_manuales`). Se completan SN, tipo de dispositivo y nombre de alarma con lo ya conocido de la planta; «Tipo de eliminación» queda vacío porque esos archivos no lo traen. Repetible: la segunda vez agrega 0.
- **Primera corrida (relleno)**: recorre el histórico hacia atrás en ventanas de 28 días (el portal solo admite 1 mes en «Hora de incidencia») hasta `inicio_busqueda` (2025-01-01). Si se corta, la siguiente continúa donde quedó (`alarmas_historicas_estado.json`). Para forzarla: *Actions → Alarmas históricas FusionSolar → Run workflow*.
- **Cada semana**: repasa desde 45 días antes de la última revisión (una alarma entra al histórico cuando se elimina, así que una que ocurrió antes y se eliminó esta semana también se captura) y agrega lo nuevo.
- **Integridad**: tras Buscar, el portal muestra «Total: N»; el Excel exportado debe traer exactamente N filas dentro de la ventana pedida. Si no cuadra, esa ventana se repite y, si sigue sin cuadrar, no se da por revisada y el workflow termina en error (se relanza solo). Una ventana con 5 000 filas o más se parte en dos.
- **Errores**: 3 intentos dentro del job (60 s y 120 s de espera) y, si aun así falla, `reintentar.yml` relanza el workflow una vez. Lo ya guardado se conserva y la revisión no avanza sobre lo que falló. Para diagnosticar, lanzarlo a mano con *depurar* (adjunta capturas del portal; el repo es público, no dejarlo activado).
- **Configuración**: `alarmas_historicas` en `config/inversores.json` (`plantas`, `inicio_busqueda`, `ventana_dias`, `solape_dias`, `max_filas_ventana`, `verificar_total`, `presupuesto_segundos`). Rango manual: `python run.py --alarmas-historicas --desde 2026-01-01 --hasta 2026-03-31`.
- El Excel y el dashboard muestran lo nuevo en la siguiente corrida de 30 min (igual que *Disponibilidad*). Pruebas sin portal: `python -m unittest discover -s tests -v`.

## Energía exportada a la red (cada hora)

Registra las horas en que una planta exportó energía a la red y avisa por correo.

- **De dónde sale**: FusionSolar › *Informes › Informe de plantas* › (planta) › granularidad **Por día** › columna **«Energía exportada (kWh)»** (una fila por hora, «Período estadístico»). El portal publica cada hora con ~2 h de retraso.
- **Cuándo**: `energia_exportada.yml` corre **cada hora al minuto 15** (14:15, 15:15… hora de Lima) y relee hoy y ayer (`dias_releer`). Como GitHub puede demorar los `schedule`, conviene crear en cron-job.org un trabajo cada hora al minuto 15 (America/Lima) con la llamada `workflow_dispatch` apuntando a `energia_exportada.yml`.
- **Qué se guarda**: `energia_exportada.csv` (rama `data`) con **solo las horas con energía exportada > 0** (`Planta, Fecha_Hora, Energia_kWh, Capturado, Alertado`). Una hora ya guardada no se duplica; si el portal corrige su valor se actualiza.
- **Relleno inicial**: las fechas ya detectadas (`energia_exportada.fechas_relleno` en `config/inversores.json`: LC 2, VES 27 y PL 5) se consultan una sola vez (avance en `energia_exportada_estado.json`). Para recorrer un rango a mano: *Actions › Energía exportada FusionSolar › Run workflow* (`desde` / `hasta`), sin aviso por correo. Con *prueba* solo deja en el log lo que lee.
- **Aviso por correo**: cada hora nueva con energía exportada se avisa **una sola vez** (las de una misma corrida en un solo correo, con planta, fecha y hora y kWh, mismo diseño Kondu que el de aislamiento). Para: `energia_exportada.destinatarios` (gestiondesoluciones@kondu.pe y Marcelo.Moreno@kg.com.pe); copia: `energia_exportada.copia`. Solo avisa horas de las últimas `alertar_horas` (48), así el relleno no dispara correos. Texto editable en `config/alerta_energia.txt`.
- **Excel**: hoja **«Energía Exportada kWh»**: por planta dos columnas, «Fecha y hora» y «Energía exportada (kWh)», encabezado morado/amarillo Kondu, lo más reciente arriba.
- **Dashboard**: tras «Mínimo diario de resistencia», sección **«Histórico de Energía exportada a la red por planta (kWh)»** con una pestaña por planta (en la vista de una planta, solo esa), totales y filtro por mes; y pestaña «Energía Exportada kWh» en *Datos del Excel*. El Excel y el dashboard toman lo nuevo en la siguiente corrida de 30 min.

## Dashboard: filtro por mes

En todas las tablas (Datos por día, Datos del Excel, mínimo diario, alarmas y limitación de potencia) basta pasar el mouse por el encabezado de la fecha (o tocarlo) para elegir un mes; «Todos los meses» quita el filtro.

## Dashboard: umbral en el tiempo y zoom

- La línea punteada de umbral usa el umbral guardado en cada lectura (`Umbral_MOhm`), así que **salta cuando el umbral cambia** en el portal en vez de mostrar solo el último valor. Las tablas de mínimo diario y «Datos del Excel» también comparan contra el umbral vigente en ese momento.
- **Zoom en todas las gráficas** (chartjs-plugin-zoom): elegir *Horizontal*, *Vertical* o *Ambos* y arrastrar para seleccionar el rango; `Ctrl + rueda` acerca/aleja, `Mayús + arrastrar` desplaza, doble clic o «⟲ Restablecer» vuelve a la vista completa. Funciona junto con los rangos 24 h / 7 días / 30 días / Todo. El zoom se conserva cuando llegan datos nuevos y se reinicia al cambiar de planta o de rango.

## Disponibilidad de las plantas

Mide cuántas horas al día está cada inversor conectado a la red. **Solo las horas se leen de FusionSolar**; la disponibilidad es una fórmula, la misma de la hoja `Disponibilidad.xlsx`:

```
disponibilidad = SI(horas >= 12; 1; horas / 12)      # 12 h = horas de sol de referencia (config: disponibilidad.horas_referencia)
```

- **De dónde salen las horas**: FusionSolar › *Informes › Informe de inversores*, granularidad *Por día*, columna **«Duración de conexión a la red (h)»**. El recolector llama al mismo servicio REST que usa esa pantalla (≈0,3 s por día); si fallara, escribe la fecha en la pantalla, pulsa *Buscar* y lee la tabla.
- **Cuándo**: `disponibilidad.yml` corre **todos los días a las 12:07 de Lima** (17:07 UTC; Lima no tiene horario de verano) y guarda las horas de **ayer**. También repasa los últimos `releer_dias` (3) por si el portal completa datos tardíos. Como GitHub puede demorar los `schedule`, conviene crear en cron-job.org un trabajo diario a las 12:00 (zona America/Lima) con la misma llamada `workflow_dispatch` que usa `actualizar.yml`, apuntando a `disponibilidad.yml`.
- **Historial**: la primera ejecución recorre todo desde `inicio_busqueda` (2025-01-01) hasta ayer y descubre sola la primera fecha con datos (Logiscity desde feb 2025, Villa el Salvador desde sep 2025 y PL desde oct 2026); tarda unos 3 minutos. Si un día falla, la revisión no avanza y la corrida siguiente lo recupera. Para recorrer un rango a mano: *Actions › Disponibilidad diaria › Run workflow* (`desde` / `hasta`) o `python run.py --disponibilidad --desde 2025-01-01`.
- **Qué se guarda**: `disponibilidad.csv` y `disponibilidad_estado.json` en la rama `data` (una fila por día e inversor: `Fecha, Planta, Grupo, Inversor, Horas, Leido, Rendimiento_kWh`).
- **Rendimiento (kWh)**: en la misma consulta de las horas se guarda la columna «Rendimiento» del Informe de inversores (energía producida por el inversor ese día). Si el portal no lo trae, la celda queda vacía. La primera corrida después de agregarlo vuelve a recorrer una sola vez el historial desde `primer_dato` para completarlo (`rendimiento_hasta` en `disponibilidad_estado.json` guarda el avance).
- **Potencia activa máxima**: junto al nombre de cada inversor («InvA.1 - 125 kW») va la *Potencia activa máxima (kW)* configurada en FusionSolar (*Control › inversor › Configuración*). Es un valor **fijo** en `config/inversores.json` (`potencia_activa_max_kw`; leído del portal el 21/11/2026: 125 kW en LC, VES y PL A.1–A.3, 165 kW en PL B.1–B.3). Si cambia en el portal, se vuelve a leer con `python run.py --potencia-maxima` (solo muestra los valores, no guarda nada) y se edita ahí. Un inversor sin ese valor se muestra solo con su nombre. (Antes se mostraba la potencia nominal, que la corrida de 30 min sigue leyendo de *Detalles* y anotando en `potencia_nominal.json` solo como registro.)
- **Día sin conexión**: si el inversor aparece ese día en el informe con rendimiento pero **sin** «Duración de conexión a la red», se toma como **0 h** (0 %). Si el portal no trae nada del inversor (antes de su puesta en marcha o sin comunicación), el día queda **sin dato** y no se grafica.
- **Excel**: hoja `Disponibilidad` (tercera, tras `BESS`) con el formato de Maestro: una fila por día desde `disponibilidad.excel_desde` (vacío = todo el histórico, desde feb 2025), **la más reciente arriba**, por inversor (con su potencia activa máxima) «Horas operativas», «Rendimiento (kWh)» y «Disponibilidad» (la fórmula `=IF(B4>=12,1,B4/12)`, formato 0,00 %). Se reconstruye entera en cada corrida desde el CSV, así que nunca duplica filas; el histórico ya está en `disponibilidad.csv` (para volver a leerlo del portal: `python run.py --disponibilidad --desde 2025-01-01`).
- **Dashboard**: botón **Disponibilidad** junto a BESS: gráfico de barras por planta (promedio de sus inversores) **por día, mes o año** con el zoom de las demás gráficas, tarjetas con el último día y el mes, y la tabla diaria. En «Datos del Excel» hay una pestaña nueva **Disponibilidad** (igual que la hoja del archivo). Colores de la disponibilidad en las tablas: 0 ≤ d < 70 % rojo · 70 ≤ d < 98,5 % amarillo · 98,5 ≤ d ≤ 100 % sin resaltar (blanco).

## BESS carga-descarga

Hoja `BESS carga-descarga` del Excel (y pestaña del mismo nombre en «Datos del Excel» del dashboard): una fila por día, la más reciente arriba, y por BESS dos grupos, **HFP** (fuera de punta) y **HP** (hora punta), con `°C Min`, `°C Máx`, `Salto °C`, `Tramo` y, solo en HP, `Fuera de Rango` (metodología del Excel «Lógica de Temperaturas»).

- **De dónde salen los datos**: de las lecturas de 30 min que ya se guardan en `historico_bess.csv` (campo «Temperatura más alta del grupo»); no hay otra consulta al portal, así que no puede fallar por login o red. Se toma, para cada horario, la lectura más cercana (tolerancia ±10 min).
- **Puntos**: HP = 18:00, 18:30 … 20:30 → 6 puntos (T1…T6) y 5 tramos incrementales. HFP = 00:00 … 17:30 → 36 puntos (T1…T36) y 35 tramos. Tramo k = T(k+1) − T(k) (1 = T1→T2).
- **°C Min / °C Máx**: en **HP**, la temperatura del primer punto (T1) y del último (T6), que no siempre son la mínima y la máxima; si falta esa lectura se usa la primera / última disponible. En **HFP**, la temperatura mínima y la máxima del periodo, en cualquier punto (no necesariamente T1 ni T36).
- **Salto °C**: el tramo de mayor valor absoluto, de subida o de bajada (con su signo), si es de 3 °C o más; si no, `normal`. Si hay dos saltos se escoge el de mayor valor absoluto.
- **Tramo**: número del tramo donde ocurrió ese salto; `normal` si el salto es normal.
- **Fuera de Rango** (**solo HP**; en HFP no aplica): temperatura de un punto intermedio (ni T1 ni T6) que queda fuera del rango entre °C Min y °C Máx: la más alta en **rojo** si queda por encima, la más baja en **azul** si queda por debajo; `normal` si ninguna sale. Si hay una por encima y otra por debajo se muestra la que más se aleja del rango (empate: la de arriba).
- **Cuándo**: `disponibilidad.yml` corre **todos los días a las 06:07 de Lima** (11:07 UTC; Lima no tiene horario de verano) y guarda las horas de **ayer**. También repasa los últimos `releer_dias` (3) por si el portal completa datos tardíos. Como GitHub puede demorar los `schedule`, conviene crear en cron-job.org un trabajo diario a las 06:00 (zona America/Lima) con la misma llamada `workflow_dispatch` que usa `actualizar.yml`, apuntando a `disponibilidad.yml`.
- **Historial**: la primera ejecución recorre todo desde `inicio_busqueda` (2025-01-01) hasta ayer y descubre sola la primera fecha con datos (Logiscity desde feb 2025, Villa el Salvador desde sep 2025 y PL desde oct 2026); tarda unos 3 minutos. Si un día falla, la revisión no avanza y la corrida siguiente lo recupera. Para recorrer un rango a mano: *Actions › Disponibilidad diaria › Run workflow* (`desde` / `hasta`) o `python run.py --disponibilidad --desde 2025-01-01`.
- **Qué se guarda**: `disponibilidad.csv` y `disponibilidad_estado.json` en la rama `data` (una fila por día e inversor: `Fecha, Planta, Grupo, Inversor, Horas, Leido, Rendimiento_kWh`).
- **Rendimiento (kWh)**: en la misma consulta de las horas se guarda la columna «Rendimiento» del Informe de inversores (energía producida por el inversor ese día). Si el portal no lo trae, la celda queda vacía. La primera corrida después de agregarlo vuelve a recorrer una sola vez el historial desde `primer_dato` para completarlo (`rendimiento_hasta` en `disponibilidad_estado.json` guarda el avance).
- **Potencia nominal**: junto al nombre de cada inversor («Inv1 - 115 kW»). Se lee en la corrida de 30 min de *Control › inversor › Detalles › Potencia nominal del inversor* y se guarda en `potencia_nominal.json` (rama `data`); mientras no haya lectura se usa `potencia_nominal_kw` de `config/inversores.json`.
- **Día sin conexión**: si el inversor aparece ese día en el informe con rendimiento pero **sin** «Duración de conexión a la red», se toma como **0 h** (0 %). Si el portal no trae nada del inversor (antes de su puesta en marcha o sin comunicación), el día queda **sin dato** y no se grafica.
- **Excel**: hoja `Disponibilidad` (tercera, tras `BESS`) con el formato de Maestro: una fila por día desde `disponibilidad.excel_desde` (vacío = todo el histórico, desde feb 2025), **la más reciente arriba**, por inversor (con su potencia nominal) «Horas operativas», «Rendimiento (kWh)» y «Disponibilidad» (la fórmula `=IF(B4>=12,1,B4/12)`, formato 0,00 %). Se reconstruye entera en cada corrida desde el CSV, así que nunca duplica filas; el histórico ya está en `disponibilidad.csv` (para volver a leerlo del portal: `python run.py --disponibilidad --desde 2025-01-01`).
- **Dashboard**: botón **Disponibilidad** junto a BESS: gráfico de barras por planta (promedio de sus inversores) **por día, mes o año** con el zoom de las demás gráficas, tarjetas con el último día y el mes, y la tabla diaria. En «Datos del Excel» hay una pestaña nueva **Disponibilidad** (igual que la hoja del archivo). Colores de la disponibilidad en las tablas: 0 ≤ d < 70 % rojo · 70 ≤ d < 98,5 % amarillo · 98,5 ≤ d ≤ 100 % sin resaltar (blanco).

## BESS carga-descarga

Hoja `BESS carga-descarga` del Excel (y pestaña del mismo nombre en «Datos del Excel» del dashboard): una fila por día, la más reciente arriba, y por BESS dos grupos, **HFP** (fuera de punta) y **HP** (hora punta), con `°C Min`, `°C Máx`, `Salto °C`, `Tramo` y, solo en HP, `Fuera de Rango` (metodología del Excel «Lógica de Temperaturas»).

- **De dónde salen los datos**: de las lecturas de 30 min que ya se guardan en `historico_bess.csv` (campo «Temperatura más alta del grupo»); no hay otra consulta al portal, así que no puede fallar por login o red. Se toma, para cada horario, la lectura más cercana (tolerancia ±10 min).
- **Puntos**: HP = 18:00, 18:30 … 20:30 → 6 puntos (T1…T6) y 5 tramos incrementales. HFP = 00:00 … 17:30 → 36 puntos (T1…T36) y 35 tramos. Tramo k = T(k+1) − T(k) (1 = T1→T2).
- **°C Min / °C Máx**: en **HP**, la temperatura del primer punto (T1) y del último (T6), que no siempre son la mínima y la máxima; si falta esa lectura se usa la primera / última disponible. En **HFP**, la temperatura mínima y la máxima del periodo, en cualquier punto (no necesariamente T1 ni T36).
- **Salto °C**: el tramo de mayor valor absoluto, de subida o de bajada (con su signo), si es de 3 °C o más; si no, `normal`. Si hay dos saltos se escoge el de mayor valor absoluto.
- **Tramo**: número del tramo donde ocurrió ese salto; `normal` si el salto es normal.
- **Fuera de Rango**: temperatura de un punto intermedio (ni el primero ni el último) que queda fuera del rango entre °C Min y °C Máx: la más alta en **rojo** si queda por encima, la más baja en **azul** si queda por debajo; `normal` si ninguna sale. Si hay una por encima y otra por debajo se muestra la que más se aleja del rango (empate: la de arriba).
- **Cuándo**: cada día se cierra y se agrega a las 12:00 (Lima) del día siguiente, en la primera corrida de 30 min después de esa hora; los últimos 7 días se recalculan por si llegaron lecturas tardías. Datos faltantes no rompen nada: el tramo que los necesita se omite. Es idempotente (no duplica filas) y no se acota a 90 días: queda en `bess_carga_descarga.csv` (rama `data`). Cada fila guarda su metodología (`Metodo`): si cambia, la siguiente corrida recalcula una vez todos los días que tengan lecturas en el historial.
- **Configuración**: `bess.carga_descarga` en `config/inversores.json` (`activo`, `hora_extraccion`, `tolerancia_min`, `salto_c`, `recalcular_dias`).

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
- **Destinatarios**: `alertas.destinatarios` (gestiondesoluciones@kondu.pe y Marcelo.Moreno@kg.com.pe) **más** la variable de Actions `ALERTA_DESTINATARIOS` si existe (separados por coma; se suman sin repetir).
- **Copia (CC)**: `alertas.copia` o, si existe, la variable `ALERTA_COPIA`. Los que ya estén en "Para" no se repiten.
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
