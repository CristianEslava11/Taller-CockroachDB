#!/usr/bin/env bash
# Inicialización del clúster (se ejecuta en el contenedor "init" de docker-compose).
#   1. cockroach init (idempotente)
#   2. espera a que los 5 nodos estén vivos
#   3. aplica la licencia si COCKROACH_LICENSE está definida
#   4. si la base uptc no existe: esquema, sharding horizontal, sharding vertical y datos
#   5. espera a que cada fragmento quede en su nodo (db/validacion/01_ubicacion.sql sin fallas)
set -euo pipefail

HOST="${COCKROACH_HOST:-duitama-academico:26257}"
NODOS_ESPERADOS=5
TIEMPO_CONVERGENCIA=300

log() { echo "[init] $*"; }
sql() { cockroach sql --insecure --host="$HOST" "$@"; }

log "Inicializando el clúster a través de $HOST..."
inicializado=false
for _ in $(seq 1 90); do
    if salida=$(cockroach init --insecure --host="$HOST" 2>&1); then
        log "Clúster inicializado."
        inicializado=true
        break
    elif grep -qi "already been initialized" <<<"$salida"; then
        log "El clúster ya estaba inicializado."
        inicializado=true
        break
    fi
    sleep 2
done
if [ "$inicializado" != true ]; then
    log "ERROR: no se pudo inicializar el clúster: $salida"
    exit 1
fi

log "Esperando $NODOS_ESPERADOS nodos vivos..."
for _ in $(seq 1 90); do
    vivos=$(cockroach node status --insecure --host="$HOST" --format=tsv 2>/dev/null \
            | awk -F'\t' 'NR > 1 && $NF == "true"' | wc -l || true)
    [ "$vivos" -ge "$NODOS_ESPERADOS" ] && break
    sleep 2
done
log "Nodos vivos: $vivos"
if [ "$vivos" -lt "$NODOS_ESPERADOS" ]; then
    log "ERROR: solo hay $vivos nodos vivos."
    exit 1
fi

if [ -n "${COCKROACH_LICENSE:-}" ]; then
    log "Aplicando licencia Enterprise..."
    sql -e "SET CLUSTER SETTING enterprise.license = '${COCKROACH_LICENSE}';"
else
    log "Sin COCKROACH_LICENSE: el clúster funciona 7 días desde su creación (ver docs/contrato.md)."
fi

existe=$(sql --format=tsv -e "SELECT count(*) FROM [SHOW DATABASES] WHERE database_name = 'uptc';" | tail -n 1)
if [ "$existe" = "0" ]; then
    for archivo in 01_schema 02_horizontal 03_vertical 04_seed; do
        log "Ejecutando db/$archivo.sql..."
        sql < "/db/$archivo.sql" > /dev/null
    done
else
    log "La base uptc ya existe: no se recrea el esquema ni se recargan datos."
fi

log "Esperando a que los fragmentos queden en su nodo (máx. ${TIEMPO_CONVERGENCIA}s)..."
inicio=$(date +%s)
while true; do
    salida=$(sql -d uptc --format=tsv < /db/validacion/01_ubicacion.sql 2>/dev/null || true)
    total=$(awk -F'\t' 'NR > 1' <<<"$salida" | wc -l)
    malos=$(awk -F'\t' 'NR > 1 && $NF != "t"' <<<"$salida" | wc -l)
    if [ "$total" -gt 0 ] && [ "$malos" -eq 0 ]; then
        log "Ubicación correcta en $total rangos ($(( $(date +%s) - inicio ))s)."
        break
    fi
    if [ $(( $(date +%s) - inicio )) -ge "$TIEMPO_CONVERGENCIA" ]; then
        log "ERROR: tras ${TIEMPO_CONVERGENCIA}s hay $malos rangos fuera de su nodo:"
        awk -F'\t' 'NR > 1 && $NF != "t" { print "  " $2 }' <<<"$salida"
        exit 1
    fi
    sleep 3
done

log "Resumen por sede:"
sql -d uptc -e "SELECT sede, count(*) AS estudiantes FROM estudiantes_academicos GROUP BY sede ORDER BY sede;"
log "Listo. SQL: localhost:26257-26261 | DB Console: http://localhost:8080"
