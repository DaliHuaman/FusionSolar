# Seguridad del repositorio

Este repositorio es **público**. Regla de oro: en el código, la configuración, el README, los tests y los registros de Actions **no va ningún correo corporativo, clave, token ni dato personal**.

## Dónde va cada dato sensible
| Dato | Dónde se guarda |
|---|---|
| Usuario y clave de FusionSolar | Secrets: `FUSIONSOLAR_USER`, `FUSIONSOLAR_PASSWORD` |
| Cuenta y clave de aplicación del correo remitente | Secrets: `SMTP_USER`, `SMTP_PASSWORD` |
| Destinatarios de las alertas (Para) | Secret: `ALERTA_DESTINATARIOS` (correos separados por coma o por líneas) |
| Copia (CC) de las alertas | Secret: `ALERTA_COPIA` |
| Opciones no sensibles (`SMTP_HOST`, `SMTP_PORT`, `ALERTA_REMITENTE`) | Variables de Actions |

Los Secrets se cargan en *Settings → Secrets and variables → Actions → Secrets*. Los valores de los Secrets se enmascaran en los registros; los de las *Variables* **no**, y en un repo público los registros los puede ver cualquiera. Por eso los correos van siempre en Secrets. El código además no escribe direcciones en los registros (solo cantidades).

## Escaneo automático
- **gitleaks** (`.github/workflows/seguridad.yml`): en cada push, en cada PR y los lunes busca claves y tokens en los archivos actuales y en todo el historial, y falla si un archivo actual contiene un correo `@kg.com.pe` (regla propia en `.gitleaks.toml`). Probar en local: `gitleaks detect --no-git --source . --config .gitleaks.toml --redact`.
- **Secret scanning y Push protection de GitHub** (se activan a mano, una sola vez): *Settings → Code security* (en algunas cuentas *Advanced Security*) → activar **Secret scanning** y **Push protection**. Son gratuitos en repositorios públicos. La protección bloquea un `git push` que contenga una clave reconocida antes de que llegue al repositorio.

## Si algo sensible llega al repo
1. Rotar de inmediato la clave o el token afectado (cambiarlo es lo que lo invalida; borrar el commit no basta).
2. Quitarlo del código y moverlo a un Secret.
3. Si estaba en el historial, limpiar con `git filter-repo` (reescribe el historial y cambia los SHA; obliga a clonar de nuevo) y pedir a GitHub Support que purgue las vistas en caché.

## Cuidado con
- Los datos de la rama `data` (Excel y CSV) son visibles mientras el repo sea público.
- Los registros de Actions de un repo público son públicos.
