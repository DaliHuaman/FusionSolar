#!/usr/bin/env bash
# Reintenta un comando ante fallos pasajeros (red, DNS, portal lento).
# Uso: bash .github/scripts/reintentar.sh <intentos> <espera_inicial_seg> comando [args...]
# La espera se duplica en cada intento. Devuelve el código del último intento.
n=$1; espera=$2; shift 2
rc=1
for ((i = 1; i <= n; i++)); do
  "$@" && exit 0
  rc=$?
  if ((i < n)); then
    echo "::warning::Intento $i/$n falló (código $rc): $*. Reintento en ${espera}s."
    sleep "$espera"
    espera=$((espera * 2))
  fi
done
echo "::error::Falló tras $n intentos (código $rc): $*"
exit "$rc"
