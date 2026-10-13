# Seguimiento de aislamiento de inversores — FusionSolar

Monitorea **dos indicadores** de los inversores de las plantas **LC, PL y VES**:

1. **Resistencia de aislamiento** (MΩ), comparada con el umbral de cada inversor.
2. **Estado de ajuste de potencia activa** (texto completo y el valor `P=xx %` como número).

Cada 30 minutos: extrae del portal FusionSolar → agrega al **Excel histórico** (OneDrive) → regenera el **dashboard público** (GitHub Pages).

```
 PC Kallpa (Programador de tareas, cada 30 min)
   run.py ──► Playwright (login → Control → árbol → cada inversor)
          ├─► state/historico.csv      (respaldo local, base del dashboard)
          ├─► Excel en OneDrive        (una fila por lectura e inversor; cola si está abierto)
          └─► data.json ─► rama gh-pages ─► https://dalihuaman.github.io/fusionsolar/
```

## Umbrales (de tu Excel manual)

| Planta | Grupo | Inversores | Umbral (MΩ) |
|---|---|---|---|
| LC | Planta 1 | Inv1 / Inv2 | 1.000 / 1.100 |
| PL | Planta A | InvA.1 / A.2 / A.3 | 1.000 / 1.100 / 1.100 |
| PL | Planta B | InvB.1 / B.2 / B.3 | 0.037 |
| VES | Planta 1 | Inv1 / Inv2 | 0.037 |

Estados: **ALARMA** (valor < umbral), **ADVERTENCIA** (hasta 10 % por encima del umbral; `margen_advertencia` en `config/inversores.json`), **OK**, **SIN_DATO** (inversor apagado/`--`).

## Instalación (una sola vez, en la PC de Kallpa)

Requisitos: Windows, Python 3.10+ y Git.

1. Clonar el repo y ejecutar `scripts\instalar.bat` (crea el entorno, instala dependencias y el navegador de Playwright).
2. Editar **`.env`** (se crea solo; no se sube a git): usuario/clave de FusionSolar, `EXCEL_DIR` (ya apunta a tu carpeta `...\FusionSolar\Indicadores`) y `GITHUB_TOKEN`.
3. Probar: `scripts\probar.bat` (abre el navegador visible y **no** publica). Debe aparecer el Excel en la carpeta de OneDrive.
4. Crear la tarea de las 30 min: en PowerShell `.\scripts\instalar_tarea.ps1`.
5. **Activar GitHub Pages** (una vez): en el repo → *Settings → Pages → Source: Deploy from a branch → Branch `gh-pages` / `(root)`*. La rama `gh-pages` la crea la primera corrida con publicación.
   Enlace público: **https://dalihuaman.github.io/fusionsolar/**

> **Token de GitHub**: crear un *fine-grained personal access token* con permiso **Contents: Read and write** solo sobre este repositorio. Si la PC ya tiene Git Credential Manager con acceso al repo, se puede dejar vacío.
> **Repo público/privado**: GitHub Pages gratuito exige repo público (los datos del dashboard serán visibles para quien tenga el enlace, como se pidió).

## Uso

```
python run.py                  # corrida normal (la de la tarea programada)
python run.py --visible        # ver el navegador para depurar
python run.py --sin-publicar   # no sube el dashboard
python run.py --solo-sitio     # regenera data.json desde el histórico y publica
python run.py --descubrir      # lista el árbol de plantas/inversores que ve el usuario
python -m fusionsolar.importar_manual "Medicion_manual.xlsx" --excel   # importa el Excel manual antiguo
```

Registros: `state\logs\collector.log`. Si falla un login o una lectura se guardan capturas en `state\debug\`.

## Excel histórico

`Historico_Indicadores_Inversores.xlsx` → hoja **Historico** (tabla `tHistorico`, filtros, colores por estado), **Umbrales** y **Resumen** (última lectura de cada inversor).
Una fila por lectura/inversor: 10 inversores × 48 lecturas/día ≈ 480 filas/día, es decir ~6 años antes del límite de Excel; al llegar a 1.000.000 filas se crea automáticamente `..._parte2.xlsx`.
Si el Excel está abierto o OneDrive lo bloquea, las filas quedan en cola (`state/pendientes_excel.csv`) y se agregan en la siguiente corrida; el dashboard no se ve afectado.

## Qué se verificó contra el portal real

- Login sin captcha con el usuario `DaliHuaman`, navegación `Control` → árbol → inversor, y lectura de `Resistencia de aislamiento` y `Estado de ajuste de potencia activa` (LC Inv1 = 1,745 MΩ, VES Inv1 = 0,188 MΩ, `P=100.0 %`).
- **Inversores de PL (Planta A y B):** con el usuario `DaliHuaman` la planta *BSF Portada Lurin* aparece **sin dispositivos** (ni en la vista Empresa ni en Dispositivo), así que esos 6 inversores no se pueden leer todavía. El recolector los busca por nombre dentro de las plantas *Portada Lurin* / *Planta Solar y Bess* (`buscar` en `config/inversores.json`); cuando el usuario tenga acceso empezarán a leerse solos. Si los nombres reales difieren de `InvA.1`, ajustar el regex `dispositivo` o fijar su `"ne": "NE=..."` (se ve con `python run.py --descubrir`). Mientras tanto aparecen como "Sin dato" en el dashboard.

## Seguridad

- Las credenciales **solo** viven en `.env` (ignorado por git). Como se compartieron por chat, se recomienda **cambiar la contraseña** del portal.
- Se recomienda un usuario del portal de solo lectura para el recolector.
