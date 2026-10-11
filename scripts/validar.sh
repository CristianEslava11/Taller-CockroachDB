#!/usr/bin/env bash
# Validación del sharding desde SQL (sin la app). Ejecutar desde la raíz del proyecto:
#   bash scripts/validar.sh              # pruebas 1-6 (apaga y reinicia tunja-central)
#   bash scripts/validar.sh --sin-fallos # solo pruebas 1-4
#
# 1. Cada fragmento está exclusivamente en el nodo previsto.
# 2. La fragmentación horizontal cubre los datos sin solapamientos.
# 3. Los fragmentos verticales reconstruyen los registros originales.
# 4. Una transacción fallida no deja medio estudiante registrado.
# 5. Con tunja-central apagado: lo académico responde y lo de contacto falla por tiempo límite.
# 6. Al reiniciar tunja-central, los contactos vuelven a estar disponibles.
set -uo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash en Windows: no reescribir rutas de los comandos docker
cd "$(dirname "$0")/.."

CLIENTE=duitama-academico   # el cliente SQL corre en un nodo que nunca se apaga en las pruebas
CENTRAL=tunja-central
FALLAS=0
CON_FALLOS=true
[ "${1:-}" = "--sin-fallos" ] && CON_FALLOS=false

# IDs fijos de los registros de prueba (se borran al empezar para que el script sea repetible).
ID_RECONSTRUCCION=00000000-0000-4000-8000-0000000000a1
ID_ATOMICIDAD=00000000-0000-4000-8000-0000000000a2
ID_CAIDA=00000000-0000-4000-8000-0000000000a3

sql() { docker exec -i "$CLIENTE" cockroach sql --insecure -d uptc --format=tsv "$@"; }
ok() { echo "  [OK]    $*"; }
falla() { echo "  [FALLA] $*"; FALLAS=$((FALLAS + 1)); }
titulo() { echo; echo "== $*"; }

# Ejecuta un archivo de db/validacion (columnas: prueba, detalle, ok) e imprime cada fila.
ejecutar_validacion() {
    local salida detalle resultado
    if ! salida=$(sql < "$1" 2>&1); then
        falla "$1 no se pudo ejecutar: $(tail -n 2 <<<"$salida" | tr '\n' ' ')"
        return
    fi
    while IFS=$'\t' read -r _ detalle resultado; do
        detalle=${detalle#\"}; detalle=${detalle%\"}; detalle=${detalle//\"\"/\"}
        if [ "$resultado" = "t" ]; then ok "$detalle"; else falla "$detalle"; fi
    done < <(tail -n +2 <<<"$salida")
}

limpiar_registros_prueba() {
    sql -e "SET statement_timeout = '15s';
            DELETE FROM estudiantes_academicos WHERE id IN
            ('$ID_RECONSTRUCCION', '$ID_ATOMICIDAD', '$ID_CAIDA');" > /dev/null 2>&1 \
        || { echo "No se pudieron limpiar los registros de prueba (¿tunja-central apagado?)."; exit 1; }
}

contar() { sql -e "SET statement_timeout = '${2:-10s}'; $1" 2>&1 | tail -n 1; }

if ! docker exec "$CLIENTE" true 2>/dev/null; then
    echo "El contenedor $CLIENTE no está corriendo. Ejecuta primero: docker compose up -d"
    exit 1
fi
limpiar_registros_prueba

titulo "Prueba 1: ubicación exclusiva de cada fragmento"
ejecutar_validacion db/validacion/01_ubicacion.sql

titulo "Prueba 2: fragmentación horizontal (cobertura y sin solapamiento)"
ejecutar_validacion db/validacion/02_horizontal.sql

titulo "Prueba 3: fragmentación vertical (completitud, disyunción, reconstrucción)"
ejecutar_validacion db/validacion/03_vertical.sql

# Reconstrucción campo por campo: se registra un estudiante conocido en ambos fragmentos
# (una transacción) y el JOIN debe devolver exactamente los valores originales.
ORIGINAL=$(printf '%s\t' sogamoso "$ID_RECONSTRUCCION" "Prueba Reconstruccion" "Ingenieria de Minas" 7 \
           "prueba.reconstruccion@uptc.edu.co" 3109876543 "Carrera 11 # 20-30, Sogamoso")
ORIGINAL=${ORIGINAL%$'\t'}
sql -e "BEGIN;
        INSERT INTO estudiantes_academicos (sede, id, nombre, programa, semestre)
        VALUES ('sogamoso', '$ID_RECONSTRUCCION', 'Prueba Reconstruccion', 'Ingenieria de Minas', 7);
        INSERT INTO estudiantes_contacto (sede, id, correo, telefono, direccion)
        VALUES ('sogamoso', '$ID_RECONSTRUCCION', 'prueba.reconstruccion@uptc.edu.co', '3109876543',
                'Carrera 11 # 20-30, Sogamoso');
        COMMIT;" > /dev/null
RECONSTRUIDO=$(sql -e "SELECT a.sede, a.id, a.nombre, a.programa, a.semestre, c.correo, c.telefono, c.direccion
                       FROM estudiantes_academicos AS a
                       JOIN estudiantes_contacto AS c USING (sede, id)
                       WHERE a.sede = 'sogamoso' AND a.id = '$ID_RECONSTRUCCION';" | tail -n +2)
if [ "$RECONSTRUIDO" = "$ORIGINAL" ]; then
    ok "reconstruccion campo por campo: el JOIN devuelve exactamente el registro original"
else
    falla "reconstruccion campo por campo: esperado [$ORIGINAL] obtenido [$RECONSTRUIDO]"
fi
sql -e "DELETE FROM estudiantes_academicos WHERE sede = 'sogamoso' AND id = '$ID_RECONSTRUCCION';" > /dev/null

titulo "Prueba 4: una transacción fallida no deja medio estudiante"
# El segundo INSERT viola ck_contacto_telefono, así que la transacción completa se revierte.
salida=$(sql -e "BEGIN;
                 INSERT INTO estudiantes_academicos (sede, id, nombre, programa, semestre)
                 VALUES ('tunja', '$ID_ATOMICIDAD', 'Prueba Atomicidad', 'Medicina', 1);
                 INSERT INTO estudiantes_contacto (sede, id, correo, telefono, direccion)
                 VALUES ('tunja', '$ID_ATOMICIDAD', 'prueba.atomicidad@uptc.edu.co', '12345', 'Calle 1 # 2-3');
                 COMMIT;" 2>&1)
if grep -q "ck_contacto_telefono" <<<"$salida"; then
    ok "el INSERT de contacto fue rechazado (ck_contacto_telefono)"
else
    falla "se esperaba un error de ck_contacto_telefono: $(tail -n 2 <<<"$salida" | tr '\n' ' ')"
fi
n=$(contar "SELECT count(*) FROM estudiantes_academicos WHERE sede = 'tunja' AND id = '$ID_ATOMICIDAD';")
if [ "$n" = "0" ]; then ok "la parte academica tampoco quedo guardada (filas=0)"
else falla "quedo medio estudiante registrado (filas academicas=$n)"; fi

if [ "$CON_FALLOS" = true ]; then
    # Pase lo que pase, tunja-central se vuelve a encender al salir.
    trap 'docker compose start "$CENTRAL" > /dev/null 2>&1' EXIT

    total_academicos=$(contar "SELECT count(*) FROM estudiantes_academicos;")
    total_contactos=$(contar "SELECT count(*) FROM estudiantes_contacto;")

    titulo "Prueba 5: tunja-central apagado"
    docker compose stop "$CENTRAL" > /dev/null 2>&1
    echo "  (tunja-central detenido)"

    n=$(contar "SELECT count(*) FROM estudiantes_academicos;" 15s)
    if [ "$n" = "$total_academicos" ]; then ok "consulta academica de todas las sedes responde ($n estudiantes)"
    else falla "consulta academica: esperado $total_academicos, obtenido [$n]"; fi

    salida=$(sql -e "SET statement_timeout = '5s'; SELECT count(*) FROM estudiantes_contacto;" 2>&1)
    if grep -q "57014" <<<"$salida"; then ok "consulta de contacto falla por tiempo limite (SQLSTATE 57014)"
    else falla "se esperaba tiempo limite en contacto: $(tail -n 2 <<<"$salida" | tr '\n' ' ')"; fi

    salida=$(sql -e "SET statement_timeout = '5s'; SELECT a.nombre, c.correo
                     FROM estudiantes_academicos AS a JOIN estudiantes_contacto AS c USING (sede, id)
                     WHERE a.sede = 'duitama';" 2>&1)
    if grep -q "57014" <<<"$salida"; then ok "reconstruccion (JOIN) falla por tiempo limite: falta el fragmento vertical"
    else falla "se esperaba tiempo limite en el JOIN: $(tail -n 2 <<<"$salida" | tr '\n' ' ')"; fi

    salida=$(sql -e "SET statement_timeout = '5s';
                     BEGIN;
                     INSERT INTO estudiantes_academicos (sede, id, nombre, programa, semestre)
                     VALUES ('duitama', '$ID_CAIDA', 'Prueba Caida Central', 'Diseno Industrial', 1);
                     INSERT INTO estudiantes_contacto (sede, id, correo, telefono, direccion)
                     VALUES ('duitama', '$ID_CAIDA', 'prueba.caida@uptc.edu.co', '3001234567', 'Calle 1 # 2-3');
                     COMMIT;" 2>&1)
    if grep -q "57014" <<<"$salida"; then ok "registro de estudiante falla por tiempo limite"
    else falla "se esperaba tiempo limite al registrar: $(tail -n 2 <<<"$salida" | tr '\n' ' ')"; fi
    n=$(contar "SELECT count(*) FROM estudiantes_academicos WHERE sede = 'duitama' AND id = '$ID_CAIDA';" 15s)
    if [ "$n" = "0" ]; then ok "el registro fallido no dejo la parte academica (filas=0)"
    else falla "quedo medio estudiante tras la caida (filas academicas=[$n])"; fi

    echo "  Ubicacion segun los metadatos del cluster (responde aunque el nodo este apagado):"
    ejecutar_validacion db/validacion/01_ubicacion.sql

    titulo "Prueba 6: reinicio de tunja-central"
    docker compose start "$CENTRAL" > /dev/null 2>&1
    inicio=$(date +%s)
    n=""
    while [ $(( $(date +%s) - inicio )) -lt 120 ]; do
        n=$(contar "SELECT count(*) FROM estudiantes_contacto;" 3s)
        [[ "$n" =~ ^[0-9]+$ ]] && break
        sleep 2
    done
    if [ "$n" = "$total_contactos" ]; then
        ok "contactos disponibles de nuevo ($n) a los $(( $(date +%s) - inicio ))s"
    else
        falla "contactos tras el reinicio: esperado $total_contactos, obtenido [$n]"
    fi
    ejecutar_validacion db/validacion/03_vertical.sql
    trap - EXIT
fi

limpiar_registros_prueba
echo
if [ "$FALLAS" -eq 0 ]; then echo "RESULTADO: todas las pruebas pasaron."
else echo "RESULTADO: $FALLAS prueba(s) fallaron."; fi
exit $(( FALLAS > 0 ))
