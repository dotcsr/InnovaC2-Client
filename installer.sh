#!/bin/bash
set -euo pipefail

cat <<'BANNER'
    ____                                _________ 
   /  _/___  ____  ____ _   ______ _   / ____/__ \
   / // __ \/ __ \/ __ \ | / / __ `/  / /    __/ /
 _/ // / / / / / / /_/ / |/ / /_/ /  / /___ / __/ 
/___/_/ /_/_/ /_/\____/|___/\__,_/   \____//____/ 
                                                  
BANNER

# Instalador automático de innovaC2
# - Crea config.env por defecto si no existe
# - Valida variables críticas
# - Ejecuta operaciones git y pip como el usuario no-root detectado (logname / SUDO_USER)
# - Crea wrapper run_client.sh para evitar problemas de quoting en systemd
# - Crea unidades systemd para cliente y updater, y timer diario a las 09:00
# - Intenta desactivar Wayland / forzar Xorg para GDM/LightDM
# - Espera a que el usuario pulse una tecla antes de reiniciar para aplicar cambios

# ----------------------------
# Crear config.env por defecto si no existe
# ----------------------------
if [ ! -f "./config.env" ]; then
  cat > ./config.env <<'CFG'
# === CONFIGURACIÓN GENERAL ===
INSTALL_DIR="/opt/innovaC2"
REPO_URL="https://github.com/dotcsr/InnovaC2-Client.git"
PYTHON_BIN="python3"
CLIENT_SCRIPT="client.py"
UPDATER_SCRIPT="updater.py"
LOG_DIR="/var/log/innovaC2"

# === SERVICIOS SYSTEMD ===
CLIENT_SERVICE="innovaC2_client.service"
UPDATER_SERVICE="innovaC2_updater.service"
UPDATER_TIMER="innovaC2_updater.timer"

# === CONFIGURACIÓN DEL CLIENTE ===
CLIENT_IP="192.168.1.1"   # Ip del servidor
CLIENT_ID="Aula-4A"       # Id único para el dispositivo
CLIENT_NAME="Aula 4°A"    # Nombre del cliente
CLIENT_PORT="9000"        # Opcional (9000 por defecto)
CFG

  chmod 600 ./config.env
  echo "✅ Se creó ./config.env con valores por defecto. Revisa y modifica si es necesario."
fi

# ----------------------------
# Cargar configuración
# ----------------------------
# shellcheck disable=SC1091
source ./config.env

# ----------------------------
# Mensajes y manejo de errores
# ----------------------------
err() {
    echo "❌ ERROR: $*" >&2
}

info() {
    echo "ℹ️  $*"
}

trap 'err "Fallo en la línea $LINENO. Revisa los mensajes anteriores."; exit 1' ERR

# ----------------------------
# Comprobar ejecución como root
# ----------------------------
if [ "$EUID" -ne 0 ]; then
    err "Ejecuta este script con sudo: sudo ./install.sh"
    exit 1
fi

# ----------------------------
# Detectar usuario no-root para instalar (preferencia: logname)
# ----------------------------
CURRENT_USER="$(logname 2>/dev/null || echo "${SUDO_USER:-$(whoami)}")"
USER_HOME="$(eval echo "~$CURRENT_USER")"

info "Instalando para el usuario: $CURRENT_USER (home: $USER_HOME)"
info "Directorio de instalación previsto: ${INSTALL_DIR:-<no definido>}"

# ----------------------------
# Validar variables críticas del config.env
# ----------------------------
required_vars=(INSTALL_DIR REPO_URL CLIENT_SCRIPT CLIENT_IP CLIENT_ID CLIENT_NAME CLIENT_SERVICE UPDATER_SCRIPT UPDATER_SERVICE UPDATER_TIMER LOG_DIR)
missing=()
for v in "${required_vars[@]}"; do
    if [ -z "${!v:-}" ]; then
        missing+=("$v")
    fi
done
if [ "${#missing[@]}" -ne 0 ]; then
    err "Faltan variables obligatorias en config.env: ${missing[*]}"
    exit 1
fi

# ----------------------------
# Preparar sistema: instalar dependencias base
# ----------------------------
info "Actualizando lista de paquetes e instalando dependencias base..."
apt update -y
DEPS=(git python3 python3-pip python3-venv)
apt install -y "${DEPS[@]}"

# ----------------------------
# Preparar directorio de instalación
# ----------------------------
info "Creando/ajustando $INSTALL_DIR ..."
mkdir -p "$INSTALL_DIR"
chown "$CURRENT_USER":"$CURRENT_USER" "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR"

# ----------------------------
# Clonar o actualizar repo como el usuario objetivo
# ----------------------------
if [ -z "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
    info "Clonando repo $REPO_URL en $INSTALL_DIR (como $CURRENT_USER)..."
    sudo -u "$CURRENT_USER" -H git clone "$REPO_URL" "$INSTALL_DIR"
else
    if [ -d "$INSTALL_DIR/.git" ]; then
        info "Actualizando repo en $INSTALL_DIR (git pull) como $CURRENT_USER..."
        sudo -u "$CURRENT_USER" -H bash -c "cd '$INSTALL_DIR' && git pull --ff-only || true"
    else
        info "Directorio $INSTALL_DIR no vacío y no es un repo git; preservado."
    fi
fi

# ----------------------------
# Crear/actualizar entorno virtual e instalar requirements (como usuario objetivo)
# ----------------------------
info "Creando/actualizando entorno virtual y dependencias (como $CURRENT_USER)..."
sudo -u "$CURRENT_USER" -H bash -c "
set -euo pipefail
cd '$INSTALL_DIR'
if [ ! -d 'venv' ]; then
  python3 -m venv venv
fi
'venv/bin/python3' -m pip install --upgrade pip setuptools wheel
if [ -f requirements.txt ]; then
  'venv/bin/pip' install -r requirements.txt
fi
"

# ----------------------------
# Crear directorio de logs y ajustar permisos
# ----------------------------
info "Creando directorio de logs en $LOG_DIR ..."
mkdir -p "$LOG_DIR"
chown -R "$CURRENT_USER":"$CURRENT_USER" "$LOG_DIR"
chmod 750 "$LOG_DIR"

# ----------------------------
# Construir el wrapper de ejecución del cliente para evitar problemas de quoting en systemd
# ----------------------------
RUN_SH="$INSTALL_DIR/run_client.sh"
info "Creando wrapper de ejecución: $RUN_SH"
cat > "$RUN_SH" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

# Wrapper para ejecutar el cliente dentro del venv
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/CLIENT_SCRIPT_REPLACE" --ip "CLIENT_IP_REPLACE" --id "CLIENT_ID_REPLACE" --name "CLIENT_NAME_REPLACE" ${CLIENT_PORT_FRAGMENT}
EOF

# Preparar fragmento de puerto
if [ -n "${CLIENT_PORT:-}" ]; then
    CLIENT_PORT_FRAGMENT="--port \"${CLIENT_PORT}\""
else
    CLIENT_PORT_FRAGMENT=""
fi

# Reemplazos en el wrapper
sed -i "s|CLIENT_SCRIPT_REPLACE|${CLIENT_SCRIPT}|g" "$RUN_SH"
sed -i "s|CLIENT_IP_REPLACE|${CLIENT_IP}|g" "$RUN_SH"
sed -i "s|CLIENT_ID_REPLACE|${CLIENT_ID}|g" "$RUN_SH"
sed -i "s|CLIENT_NAME_REPLACE|${CLIENT_NAME}|g" "$RUN_SH"
escaped_fragment="$(printf '%s\n' "${CLIENT_PORT_FRAGMENT}" | sed -e 's/[\/&]/\\&/g')"
sed -i "s|\${CLIENT_PORT_FRAGMENT}|${escaped_fragment}|g" "$RUN_SH"

# Permisos del wrapper
chown "$CURRENT_USER":"$CURRENT_USER" "$RUN_SH"
chmod 750 "$RUN_SH"

# ----------------------------
# Crear servicio systemd para el cliente
# ----------------------------
info "Generando unidad systemd para el cliente: $CLIENT_SERVICE"
cat > "/etc/systemd/system/$CLIENT_SERVICE" <<EOF
[Unit]
Description=Cliente de innovaC2
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$RUN_SH
Restart=always
RestartSec=5
StartLimitBurst=6
StartLimitIntervalSec=60
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:$LOG_DIR/client.log
StandardError=append:$LOG_DIR/client_error.log

[Install]
WantedBy=multi-user.target
EOF

# ----------------------------
# Crear servicio y timer para el updater
# ----------------------------
info "Generando unidad systemd para el updater: $UPDATER_SERVICE"
cat > "/etc/systemd/system/$UPDATER_SERVICE" <<EOF
[Unit]
Description=Actualizador diario de innovaC2
After=network.target

[Service]
Type=simple
User=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/$UPDATER_SCRIPT
StandardOutput=append:$LOG_DIR/updater.log
StandardError=append:$LOG_DIR/updater_error.log
EOF

info "Generando timer systemd para el updater: $UPDATER_TIMER (09:00 diario)"
cat > "/etc/systemd/system/$UPDATER_TIMER" <<EOF
[Unit]
Description=Ejecución diaria del updater de innovaC2 a las 9:00 AM

[Timer]
OnCalendar=*-*-* 09:00:00
Persistent=true
Unit=$UPDATER_SERVICE

[Install]
WantedBy=timers.target
EOF

# ----------------------------
# Recargar systemd y habilitar servicios/timers
# ----------------------------
info "Recargando systemd y habilitando servicios..."
systemctl daemon-reload
systemctl enable --now "$CLIENT_SERVICE"
systemctl enable --now "$UPDATER_TIMER"

# Intentar iniciar/reiniciar servicio cliente ahora
info "Reiniciando servicio cliente para aplicar cambios..."
systemctl restart "$CLIENT_SERVICE" || {
    err "No se pudo iniciar/reiniciar $CLIENT_SERVICE; revisa los logs en $LOG_DIR"
}

# ----------------------------
# Intento de desactivar Wayland y forzar Xorg en GDM o configurar LightDM
# ----------------------------
info "Intentando desactivar Wayland / forzar Xorg en el gestor de sesiones si aplica..."

# Para GDM (Ubuntu por defecto)
if command -v gdm3 >/dev/null 2>&1 && [ -f /etc/gdm3/custom.conf ] ; then
    info "Configurando /etc/gdm3/custom.conf para desactivar Wayland..."
    if grep -q '^\s*WaylandEnable' /etc/gdm3/custom.conf 2>/dev/null; then
        sed -i 's/^\s*#\?\s*WaylandEnable\s*=.*/WaylandEnable=false/' /etc/gdm3/custom.conf
    else
        if grep -q '^\[daemon\]' /etc/gdm3/custom.conf; then
            sed -i '/^\[daemon\]/a WaylandEnable=false' /etc/gdm3/custom.conf
        else
            printf '\n[daemon]\nWaylandEnable=false\n' >> /etc/gdm3/custom.conf
        fi
    fi
    info "Wayland deshabilitado en /etc/gdm3/custom.conf (si GDM3 está en uso)."
elif command -v lightdm >/dev/null 2>&1 || [ -d /etc/lightdm ]; then
    info "Parece que LightDM podría estar instalado. Configurando archivo para forzar Xorg..."
    mkdir -p /etc/lightdm/lightdm.conf.d
    cat > /etc/lightdm/lightdm.conf.d/90-force-xorg.conf <<EOF
[Seat:*]
# Forzar Xorg (X server). Asegúrate de que Xorg esté instalado en el sistema.
xserver-command=/usr/bin/Xorg
EOF
    info "Creado /etc/lightdm/lightdm.conf.d/90-force-xorg.conf"
else
    info "No se detectó GDM3 ni LightDM. No se aplicó configuración para desactivar Wayland automáticamente."
fi

# ----------------------------
# Mensaje final y reinicio manual por tecla
# ----------------------------
info "Instalación completada: el cliente está habilitado para iniciarse en arranque y el updater está programado."
info "Los logs se escribirán en: $LOG_DIR"
info "Para aplicar la desactivación de Wayland y forzar Xorg se requiere reiniciar la laptop."
info "Puedes revisar el estado del servicio con: systemctl status $CLIENT_SERVICE"
echo
read -n 1 -s -r -p "🔁 Presiona cualquier tecla para reiniciar ahora..."
echo
info "Reiniciando ahora..."
systemctl reboot -i
