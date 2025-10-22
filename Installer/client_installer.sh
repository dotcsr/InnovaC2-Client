#!/bin/bash
set -euo pipefail

# installer.sh — Instalador / Desinstalador combinado para innovaC2
# Uso típico (one-liner): sudo ./installer.sh --install --id "Aula-5B" --name "Aula 5°B" --yes
# Ejecutar con sudo: sudo ./installer.sh

cat <<'BANNER'
    ____                                _________
   /  _/___  ____  ____ _   ______ _   / ____/__ \
   / // __ \/ __ \/ __ \ | / / __ `/  / /    __/ /
 _/ // / / / / / / /_/ / |/ / /_/ /  / /___ / __/
 /___/_/ /_/_/ /_/\____/|___/\__,_/   \____//____/
BANNER

# Mensajes
err() { echo "❌ ERROR: $*" >&2; }
info() { echo "ℹ️  $*"; }

# Defaults (se usan si no existe config.env o para el proceso de desinstalación)
DEFAULT_INSTALL_DIR="/opt/innovaC2"
DEFAULT_REPO_URL="https://github.com/dotcsr/InnovaC2-Client.git"
DEFAULT_PYTHON_BIN="python3"
DEFAULT_CLIENT_SCRIPT="client.py"
DEFAULT_UPDATER_SCRIPT="updater.py"
DEFAULT_LOG_DIR="/var/log/innovaC2"
DEFAULT_CLIENT_SERVICE="innovaC2_client.service"
DEFAULT_UPDATER_SERVICE="innovaC2_updater.service"
DEFAULT_UPDATER_TIMER="innovaC2_updater.timer"
DEFAULT_CLIENT_IP="10.66.40.99"
DEFAULT_CLIENT_ID="Aula-4A"
DEFAULT_CLIENT_NAME="Aula 4°A"
DEFAULT_CLIENT_PORT="9000"

# Rutas/archivos especiales que el instalador crea/modifica y que intentaremos revertir
GDM_CUSTOM="/etc/gdm3/custom.conf"
GDM_BACKUP="/etc/gdm3/custom.conf.innovaC2.bak"
LIGHTDM_CONF_DIR="/etc/lightdm/lightdm.conf.d"
LIGHTDM_CONF_FILE="$LIGHTDM_CONF_DIR/90-force-xorg.conf"

SYSTEMD_DIR="/etc/systemd/system"

# ---------------------------
# Parseo de argumentos CLI
# ---------------------------
MODE=""           # "1" instalar, "2" desinstalar
NONINTERACTIVE=0  # --yes
FORCE_REBOOT=0    # --reboot
CLIENT_ID=""
CLIENT_NAME=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --id)
      CLIENT_ID="$2"; shift 2;;
    --name)
      CLIENT_NAME="$2"; shift 2;;
    --yes|-y|--noninteractive)
      NONINTERACTIVE=1; shift;;
    --install)
      MODE="1"; shift;;
    --uninstall)
      MODE="2"; shift;;
    --reboot)
      FORCE_REBOOT=1; shift;;
    --help|-h)
      cat <<USAGE
Uso: installer.sh [--install|--uninstall] [--id ID] [--name NAME] [--yes] [--reboot]

Opciones:
  --install           Ejecuta instalación (equivale a elegir '1' en el menú).
  --uninstall         Ejecuta desinstalación completa (equivale a elegir '2').
  --id "ID"           ID del cliente (sobrescribe config.env si existe).
  --name "NOMBRE"     Nombre del cliente (sobrescribe config.env si existe).
  --yes, -y           Modo no interactivo: aceptar prompts por defecto.
  --reboot            Reiniciar automáticamente al final (usar con precaución).
  --help              Mostrar este mensaje.
USAGE
      exit 0;;
    *)
      # cualquier otro argumento se considera terminado el parseo
      break;;
  esac
done

# Comprobar root
if [ "$EUID" -ne 0 ]; then
    err "Ejecuta este script con sudo: sudo ./installer.sh"
    exit 1
fi

# ---------------------------
# Funciones auxiliares
# ---------------------------
create_default_config() {
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
CLIENT_IP="192.168.1.1"
CLIENT_ID="Aula-4A"
CLIENT_NAME="Aula 4°A"
CLIENT_PORT="9000"
CFG
    chmod 600 ./config.env
    info "✅ Se creó ./config.env con valores por defecto. Edítalo si es necesario."
    return 0
  fi
  return 1
}

# Devuelve 0 si la ruta parece segura para rm -rf (básico)
is_safe_to_rm() {
  local p="$1"
  # Normalizar: quitar trailing slash
  p="${p%/}"
  # Prohibir rutas demasiado genéricas o cruciales
  case "$p" in
    ""|"/"|"/bin"|"/boot"|"/dev"|"/etc"|"/home"|"/lib"|"/lib64"|"/proc"|"/root"|"/sbin"|"/sys"|"/usr")
      return 1
      ;;
    *)
      # rechazo si la ruta no contiene al menos un slash (precaución)
      if [[ "$p" != /* ]]; then
        return 1
      fi
      return 0
      ;;
  esac
}

# Escapar valor para escribirlo entre comillas simples en un archivo shell
single_quote_for_shell() {
  local s="$1"
  # reemplazar ' por '\''  -> forma segura para single-quote
  s="${s//\'/\'\\\'\'}"
  printf "'%s'" "$s"
}

# ---------------------------
# Flujo de instalación
# ---------------------------
install_flow() {
  info "Iniciando flujo de instalación..."

  # Si no existe config.env, y tenemos id+name por CLI, generar config mínimo
  if [ ! -f "./config.env" ]; then
    if [ -n "${CLIENT_ID:-}" ] && [ -n "${CLIENT_NAME:-}" ]; then
      info "No existe ./config.env pero se pasaron --id y --name: creando config.env temporal con valores mínimos."
      cat > ./config.env <<CFG
INSTALL_DIR="${DEFAULT_INSTALL_DIR}"
REPO_URL="${DEFAULT_REPO_URL}"
PYTHON_BIN="${DEFAULT_PYTHON_BIN}"
CLIENT_SCRIPT="${DEFAULT_CLIENT_SCRIPT}"
UPDATER_SCRIPT="${DEFAULT_UPDATER_SCRIPT}"
LOG_DIR="${DEFAULT_LOG_DIR}"
CLIENT_SERVICE="${DEFAULT_CLIENT_SERVICE}"
UPDATER_SERVICE="${DEFAULT_UPDATER_SERVICE}"
UPDATER_TIMER="${DEFAULT_UPDATER_TIMER}"
CLIENT_IP="${DEFAULT_CLIENT_IP}"
CLIENT_PORT="${DEFAULT_CLIENT_PORT}"
CLIENT_ID="${CLIENT_ID}"
CLIENT_NAME="${CLIENT_NAME}"
CFG
      chmod 600 ./config.env
      info "Se creó ./config.env temporal con CLIENT_ID/CLIENT_NAME provistos por CLI."
    else
      # comportamiento anterior: crear config.env de ejemplo y pedir edición (salir) salvo que sea modo no interactivo
      if create_default_config; then
        if [ "$NONINTERACTIVE" -eq 1 ]; then
          info "Modo no interactivo y config.env recién creado: continuando automáticamente."
        else
          echo "✍️  Edita ./config.env con los valores deseados y vuelve a ejecutar este script con sudo para continuar la instalación."
          exit 0
        fi
      fi
    fi
  fi

  # Cargar configuración si existe
  # shellcheck disable=SC1091
  source ./config.env

  # Assign defaults si faltan variables
  INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
  REPO_URL="${REPO_URL:-$DEFAULT_REPO_URL}"
  PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON_BIN}"
  CLIENT_SCRIPT="${CLIENT_SCRIPT:-$DEFAULT_CLIENT_SCRIPT}"
  UPDATER_SCRIPT="${UPDATER_SCRIPT:-$DEFAULT_UPDATER_SCRIPT}"
  LOG_DIR="${LOG_DIR:-$DEFAULT_LOG_DIR}"
  CLIENT_SERVICE="${CLIENT_SERVICE:-$DEFAULT_CLIENT_SERVICE}"
  UPDATER_SERVICE="${UPDATER_SERVICE:-$DEFAULT_UPDATER_SERVICE}"
  UPDATER_TIMER="${UPDATER_TIMER:-$DEFAULT_UPDATER_TIMER}"
  CLIENT_IP="${CLIENT_IP:-$DEFAULT_CLIENT_IP}"
  CLIENT_ID="${CLIENT_ID:-$DEFAULT_CLIENT_ID}"
  CLIENT_NAME="${CLIENT_NAME:-$DEFAULT_CLIENT_NAME}"
  CLIENT_PORT="${CLIENT_PORT:-$DEFAULT_CLIENT_PORT}"

  # Si el usuario pasó --id/--name por CLI, sobrescribir las variables cargadas
  if [ -n "${CLIENT_ID:-}" ] && [ -n "$CLIENT_ID" ]; then
    : # ya cargada desde config o CLI (CLI ya escrita si no existía config)
  fi
  if [ -n "${CLIENT_NAME:-}" ] && [ -n "$CLIENT_NAME" ]; then
    : # idem
  fi

  # Detectar usuario no-root (preferencia logname)
  CURRENT_USER="$(logname 2>/dev/null || echo "${SUDO_USER:-$(whoami)}")"
  USER_HOME="$(eval echo "~$CURRENT_USER")"
  TARGET_UID="$(id -u "$CURRENT_USER")"

  info "Instalando para el usuario: $CURRENT_USER (UID: $TARGET_UID, home: $USER_HOME)"
  info "Directorio de instalación previsto: $INSTALL_DIR"

  # Validar variables críticas mínimas
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

  # Detectar apt (solo Debian/Ubuntu compatibles)
  if ! command -v apt >/dev/null 2>&1; then
    err "Este instalador está pensado para distribuciones Debian/Ubuntu con 'apt'. Abortando."
    exit 1
  fi

  # Instalar dependencias base
  info "Instalando dependencias base (apt)..."
  apt update -y
  DEPS=(git python3 python3-pip python3-venv python3-tk)
  apt install -y "${DEPS[@]}"

  # Preparar directorio de instalación
  info "Creando/ajustando $INSTALL_DIR ..."
  mkdir -p "$INSTALL_DIR"
  chown "$CURRENT_USER":"$CURRENT_USER" "$INSTALL_DIR"
  chmod 755 "$INSTALL_DIR"

  # Clonar o actualizar repo como el usuario objetivo
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

  # Crear/actualizar venv e instalar requirements
  info "Creando/actualizando entorno virtual y dependencias (como $CURRENT_USER)..."
  sudo -u "$CURRENT_USER" -H bash -c "
set -euo pipefail
cd '$INSTALL_DIR'
if [ ! -d 'venv' ]; then
  python3 -m venv venv
fi
./venv/bin/python3 -m pip install --upgrade pip setuptools wheel
if [ -f requirements.txt ]; then
  ./venv/bin/pip install -r requirements.txt
fi
"

  # Crear directory de logs
  info "Creando directorio de logs en $LOG_DIR ..."
  mkdir -p "$LOG_DIR"
  chown -R "$CURRENT_USER":"$CURRENT_USER" "$LOG_DIR"
  chmod 750 "$LOG_DIR"

  # ------------------------------
  # Crear archivo de variables para el run wrapper (forma segura)
  # ------------------------------
  RUN_ENV_FILE="$INSTALL_DIR/run_client.env"
  info "Escribiendo variables seguras en: $RUN_ENV_FILE"
  {
    printf "CLIENT_SCRIPT=%s\n" "$(single_quote_for_shell "$CLIENT_SCRIPT")"
    printf "CLIENT_IP=%s\n" "$(single_quote_for_shell "$CLIENT_IP")"
    printf "CLIENT_ID=%s\n" "$(single_quote_for_shell "$CLIENT_ID")"
    printf "CLIENT_NAME=%s\n" "$(single_quote_for_shell "$CLIENT_NAME")"
    printf "CLIENT_PORT=%s\n" "$(single_quote_for_shell "$CLIENT_PORT")"
    printf "INSTALL_DIR=%s\n" "$(single_quote_for_shell "$INSTALL_DIR")"
    printf "LOG_DIR=%s\n" "$(single_quote_for_shell "$LOG_DIR")"
  } > "$RUN_ENV_FILE"
  chown "$CURRENT_USER":"$CURRENT_USER" "$RUN_ENV_FILE"
  chmod 600 "$RUN_ENV_FILE"

  # Construir wrapper run_client.sh (wrapper inteligente que detecta DISPLAY/DBUS)
  RUN_SH="$INSTALL_DIR/run_client.sh"
  info "Creando wrapper inteligente: $RUN_SH"
  cat > "$RUN_SH" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

# run_client.sh — wrapper inteligente para X11 + D-Bus
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Cargar variables generadas por el instalador
if [ -f "$SCRIPT_DIR/run_client.env" ]; then
  # shellcheck disable=SC1090
  source "$SCRIPT_DIR/run_client.env"
fi

# Detectar usuario que posee el proceso (si el script es ejecutado por systemd --user correrá como ese usuario)
USER_NAME="${SUDO_USER:-${USER:-$(whoami)}}"
USER_HOME="$(eval echo "~${USER_NAME}")"
USER_UID="$(id -u "${USER_NAME}" 2>/dev/null || echo "$UID")"

# 1) Exportar XDG_RUNTIME_DIR y DBUS_SESSION_BUS_ADDRESS si está disponible
if [ -d "/run/user/${USER_UID}" ]; then
  export XDG_RUNTIME_DIR="/run/user/${USER_UID}"
  export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"
fi

# 2) Intentar detectar DISPLAY:
if [ -z "${DISPLAY:-}" ]; then
  if command -v loginctl >/dev/null 2>&1; then
    session=$(loginctl list-sessions --no-legend | awk "\$3==\"${USER_NAME}\" {print \$1; exit}" || true)
    if [ -n "$session" ]; then
      display_val=$(loginctl show-session "$session" --property=Display --value 2>/dev/null || true)
      if [ -n "$display_val" ] && [ "$display_val" != "@" ]; then
        export DISPLAY=":${display_val}"
      fi
    fi
  fi
fi

# 3) fallback por procesos (Xorg / Xwayland) del usuario
if [ -z "${DISPLAY:-}" ]; then
  for pid in $(pgrep -u "$USER_UID" -f '(Xorg|Xwayland|gnome-session|kwin|plasmashell)' 2>/dev/null || true); do
    if [ -r "/proc/$pid/environ" ]; then
      envline=$(tr '\0' '\n' < /proc/$pid/environ | grep '^DISPLAY=' || true)
      if [ -n "$envline" ]; then
        export DISPLAY="${envline#DISPLAY=}"
        break
      fi
    fi
  done
fi

# 4) último recurso: forzar :0 (puesto que estás en X11)
if [ -z "${DISPLAY:-}" ]; then
  export DISPLAY=":0"
fi

# 5) XAUTHORITY: preferir /run/user/UID/.Xauthority o ~/.Xauthority
if [ -z "${XAUTHORITY:-}" ]; then
  if [ -f "/run/user/${USER_UID}/.Xauthority" ]; then
    export XAUTHORITY="/run/user/${USER_UID}/.Xauthority"
  elif [ -f "${USER_HOME}/.Xauthority" ]; then
    export XAUTHORITY="${USER_HOME}/.Xauthority"
  fi
fi

# Info (se mostrará en logs del servicio)
echo "ℹ️  run_client.sh: Usuario=${USER_NAME} UID=${USER_UID}"
echo "ℹ️  run_client.sh: XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-<no>}"
echo "ℹ️  run_client.sh: DBUS_SESSION_BUS_ADDRESS=${DBUS_SESSION_BUS_ADDRESS:-<no>}"
echo "ℹ️  run_client.sh: DISPLAY=${DISPLAY:-<no>}"
echo "ℹ️  run_client.sh: XAUTHORITY=${XAUTHORITY:-<no>}"

# Construir fragmento de puerto si existe
PORT_FRAGMENT=()
if [ -n "${CLIENT_PORT:-}" ] && [ "${CLIENT_PORT:-}" != "''" ]; then
  PORT_FRAGMENT=(--port "${CLIENT_PORT}")
fi

# Ejecutar el cliente dentro del venv
exec "$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/${CLIENT_SCRIPT}" --ip "${CLIENT_IP}" --id "${CLIENT_ID}" --name "${CLIENT_NAME}" "${PORT_FRAGMENT[@]}"
EOF

  chown "$CURRENT_USER":"$CURRENT_USER" "$RUN_SH"
  chmod 750 "$RUN_SH"

  # -- Crear unidad systemd --user para el cliente --
  USER_SYSTEMD_DIR="$USER_HOME/.config/systemd/user"
  USER_CLIENT_UNIT="$USER_SYSTEMD_DIR/$CLIENT_SERVICE"

  info "Creando unidad systemd --user para el cliente: $USER_CLIENT_UNIT"
  mkdir -p "$USER_SYSTEMD_DIR"
  cat > "$USER_CLIENT_UNIT" <<EOF
[Unit]
Description=Cliente de innovaC2 (user service)
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=$RUN_SH
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
StandardOutput=append:$LOG_DIR/client.log
StandardError=append:$LOG_DIR/client_error.log

[Install]
WantedBy=default.target
EOF

  # Ajustar permisos - la unidad debe ser propiedad del usuario
  chown -R "$CURRENT_USER":"$CURRENT_USER" "$USER_HOME/.config/systemd"
  chmod -R 700 "$USER_HOME/.config/systemd"

  # -- Crear/respaldar y escribir unidades systemd system-wide para updater --
  info "Generando unidad systemd para el updater: $UPDATER_SERVICE"
  if [ -f "$SYSTEMD_DIR/$UPDATER_SERVICE" ] && [ ! -f "$SYSTEMD_DIR/$UPDATER_SERVICE.innovaC2.bak" ]; then
      cp "$SYSTEMD_DIR/$UPDATER_SERVICE" "$SYSTEMD_DIR/$UPDATER_SERVICE.innovaC2.bak" || true
  fi
  cat > "$SYSTEMD_DIR/$UPDATER_SERVICE" <<EOF
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
  if [ -f "$SYSTEMD_DIR/$UPDATER_TIMER" ] && [ ! -f "$SYSTEMD_DIR/$UPDATER_TIMER.innovaC2.bak" ]; then
      cp "$SYSTEMD_DIR/$UPDATER_TIMER" "$SYSTEMD_DIR/$UPDATER_TIMER.innovaC2.bak" || true
  fi
  cat > "$SYSTEMD_DIR/$UPDATER_TIMER" <<EOF
[Unit]
Description=Ejecución diaria del updater de innovaC2 a las 9:00 AM

[Timer]
OnCalendar=*-*-* 09:00:00
Persistent=true
Unit=$UPDATER_SERVICE

[Install]
WantedBy=timers.target
EOF

  # Recargar systemd (system-wide) y habilitar timer/updater
  info "Recargando systemd (system-wide) y habilitando timer/updater..."
  systemctl daemon-reload
  systemctl enable "$UPDATER_TIMER" || true

  # Recargar systemd --user y habilitar/arrancar la unidad del cliente en el contexto del usuario
  info "Intentando recargar systemd --user y habilitar la unidad del cliente para $CURRENT_USER..."
  if [ -d "/run/user/$TARGET_UID" ]; then
      sudo -u "$CURRENT_USER" XDG_RUNTIME_DIR="/run/user/$TARGET_UID" systemctl --user daemon-reload
      sudo -u "$CURRENT_USER" XDG_RUNTIME_DIR="/run/user/$TARGET_UID" systemctl --user enable --now "$CLIENT_SERVICE" || {
          info "No se pudo arrancar la unidad user ahora; pero está habilitada."
      }
      systemctl enable --now "$UPDATER_TIMER" || info "Timer habilitado; no se pudo arrancar ahora, pero está habilado."
  else
      info "/run/user/$TARGET_UID no existe (no hay sesión activa). Habilitando unidad user (no se arrancará ahora)."
      sudo -u "$CURRENT_USER" XDG_RUNTIME_DIR="/run/user/$TARGET_UID" systemctl --user daemon-reload 2>/dev/null || true
      sudo -u "$CURRENT_USER" XDG_RUNTIME_DIR="/run/user/$TARGET_UID" systemctl --user enable "$CLIENT_SERVICE" 2>/dev/null || true
      info "Cuando $CURRENT_USER inicie sesión, la unidad se podrá arrancar con: systemctl --user restart $CLIENT_SERVICE"
      info "Si desea que la unidad user pueda ejecutarse sin sesión gráfica, considere: loginctl enable-linger $CURRENT_USER"
  fi

  # Intento de desactivar Wayland / forzar Xorg y hacer backup de custom.conf si se modifica
  info "Intentando desactivar Wayland / forzar Xorg en GDM/LightDM (si aplica)..."
  if command -v gdm3 >/dev/null 2>&1 && [ -f "$GDM_CUSTOM" ]; then
      info "Detectado GDM3. Haciendo backup de $GDM_CUSTOM -> $GDM_BACKUP (si no existe)..."
      if [ ! -f "$GDM_BACKUP" ]; then
          cp "$GDM_CUSTOM" "$GDM_BACKUP" || info "No se pudo crear backup de $GDM_CUSTOM"
      fi
      if grep -q '^\s*WaylandEnable' "$GDM_CUSTOM" 2>/dev/null; then
          sed -i 's/^\s*#\?\s*WaylandEnable\s*=.*/WaylandEnable=false/' "$GDM_CUSTOM"
      else
          if grep -q '^\[daemon\]' "$GDM_CUSTOM" 2>/dev/null; then
              sed -i '/^\[daemon\]/a WaylandEnable=false' "$GDM_CUSTOM"
          else
              printf '\n[daemon]\nWaylandEnable=false\n' >> "$GDM_CUSTOM"
          fi
      fi
      info "Wayland deshabilitado en $GDM_CUSTOM (si GDM3 está en uso). Backup: $GDM_BACKUP"
  elif command -v lightdm >/dev/null 2>&1 || [ -d /etc/lightdm ]; then
      info "Detectado LightDM. Creando $LIGHTDM_CONF_FILE para forzar Xorg..."
      mkdir -p "$LIGHTDM_CONF_DIR"
      cat > "$LIGHTDM_CONF_FILE" <<EOF
[Seat:*]
# Forzar Xorg (X server). Asegúrate de que Xorg esté instalado en el sistema.
xserver-command=/usr/bin/Xorg
EOF
      info "Creado $LIGHTDM_CONF_FILE"
  else
      info "No se detectó GDM3 ni LightDM; no se aplicó configuración para desactivar Wayland automáticamente."
  fi

  info "Instalación finalizada. Revisa los logs en: $LOG_DIR"
  info "Puedes ver el estado de la unidad user con (como $CURRENT_USER): systemctl --user status $CLIENT_SERVICE"
  echo

  if [ "$FORCE_REBOOT" -eq 1 ]; then
    info "Opción --reboot dada: reiniciando ahora..."
    systemctl reboot -i
  else
    info "No se reiniciará automáticamente. Si modificaste GDM/LightDM, considera reiniciar el equipo manualmente."
  fi
}

# ----------------------------
# DESINSTALACIÓN
# ----------------------------
uninstall_flow() {
  echo "⚠️  ATENCIÓN: La desinstalación eliminará los servicios, timer, directorios de instalación y logs creados por el instalador."
  if [ "$NONINTERACTIVE" -eq 1 ]; then
    yn="yes"
  else
    read -r -p "¿Estás seguro que quieres desinstalar completamente? (yes/NO): " yn
  fi
  if [ "$yn" != "yes" ]; then
      info "Desinstalación cancelada."
      exit 0
  fi

  # Intentar cargar config.env para obtener nombres y rutas; si no existe, usar defaults
  if [ -f "./config.env" ]; then
      # shellcheck disable=SC1091
      source ./config.env || true
  fi

  INSTALL_DIR="${INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
  LOG_DIR="${LOG_DIR:-$DEFAULT_LOG_DIR}"
  CLIENT_SERVICE="${CLIENT_SERVICE:-$DEFAULT_CLIENT_SERVICE}"
  UPDATER_SERVICE="${UPDATER_SERVICE:-$DEFAULT_UPDATER_SERVICE}"
  UPDATER_TIMER="${UPDATER_TIMER:-$DEFAULT_UPDATER_TIMER}"

  # Detectar usuario no-root (preferencia logname)
  CURRENT_USER="$(logname 2>/dev/null || echo "${SUDO_USER:-$(whoami)}")"
  USER_HOME="$(eval echo "~$CURRENT_USER")"
  TARGET_UID="$(id -u "$CURRENT_USER")"

  info "Deteniendo y deshabilitando unidades systemd (si existen)..."

  # Detener/deshabilitar updater (system-wide)
  systemctl stop "$UPDATER_SERVICE" 2>/dev/null || true
  systemctl disable "$UPDATER_SERVICE" 2>/dev/null || true
  systemctl stop "$UPDATER_TIMER" 2>/dev/null || true
  systemctl disable "$UPDATER_TIMER" 2>/dev/null || true

  # Intentar detener y deshabilitar la unidad user del cliente (si posible)
  info "Intentando detener/deshabilitar unidad user del cliente para $CURRENT_USER..."
  if [ -d "/run/user/$TARGET_UID" ]; then
      sudo -u "$CURRENT_USER" XDG_RUNTIME_DIR="/run/user/$TARGET_UID" systemctl --user stop "$CLIENT_SERVICE" 2>/dev/null || true
      sudo -u "$CURRENT_USER" XDG_RUNTIME_DIR="/run/user/$TARGET_UID" systemctl --user disable "$CLIENT_SERVICE" 2>/dev/null || true
  else
      sudo -u "$CURRENT_USER" XDG_RUNTIME_DIR="/run/user/$TARGET_UID" systemctl --user disable "$CLIENT_SERVICE" 2>/dev/null || true
  fi

  # Eliminar archivo de unidad user en home del usuario
  USER_SYSTEMD_DIR="$USER_HOME/.config/systemd/user"
  USER_CLIENT_UNIT="$USER_SYSTEMD_DIR/$CLIENT_SERVICE"
  if [ -f "$USER_CLIENT_UNIT" ]; then
      info "Eliminando unidad user: $USER_CLIENT_UNIT"
      rm -f "$USER_CLIENT_UNIT" || true
      sudo -u "$CURRENT_USER" XDG_RUNTIME_DIR="/run/user/$TARGET_UID" systemctl --user daemon-reload 2>/dev/null || true
  fi

  # Eliminar archivos de unidad systemd system-wide del updater (restaurar backups si existen)
  if [ -f "$SYSTEMD_DIR/$UPDATER_SERVICE.innovaC2.bak" ]; then
      info "Restaurando backup de $UPDATER_SERVICE desde $UPDATER_SERVICE.innovaC2.bak"
      mv -f "$SYSTEMD_DIR/$UPDATER_SERVICE.innovaC2.bak" "$SYSTEMD_DIR/$UPDATER_SERVICE" || info "No se pudo restaurar backup"
  else
      info "Eliminando $SYSTEMD_DIR/$UPDATER_SERVICE"
      rm -f "$SYSTEMD_DIR/$UPDATER_SERVICE" || true
  fi

  if [ -f "$SYSTEMD_DIR/$UPDATER_TIMER.innovaC2.bak" ]; then
      info "Restaurando backup de $UPDATER_TIMER desde $UPDATER_TIMER.innovaC2.bak"
      mv -f "$SYSTEMD_DIR/$UPDATER_TIMER.innovaC2.bak" "$SYSTEMD_DIR/$UPDATER_TIMER" || info "No se pudo restaurar backup"
  else
      info "Eliminando $SYSTEMD_DIR/$UPDATER_TIMER"
      rm -f "$SYSTEMD_DIR/$UPDATER_TIMER" || true
  fi

  info "Recargando systemd (system-wide)..."
  systemctl daemon-reload || true

  # Eliminar directorio de instalación y logs (con verificación de seguridad)
  if [ -d "$INSTALL_DIR" ]; then
      if is_safe_to_rm "$INSTALL_DIR"; then
          info "Eliminando directorio de instalación: $INSTALL_DIR"
          rm -rf "$INSTALL_DIR" || info "No se pudo eliminar $INSTALL_DIR completamente."
      else
          err "Ruta $INSTALL_DIR considerada insegura para eliminación automática. Por favor elimina manualmente si procede."
      fi
  else
      info "No existe $INSTALL_DIR"
  fi

  if [ -d "$LOG_DIR" ]; then
      if is_safe_to_rm "$LOG_DIR"; then
          info "Eliminando directorio de logs: $LOG_DIR"
          rm -rf "$LOG_DIR" || info "No se pudo eliminar $LOG_DIR completamente."
      else
          err "Ruta $LOG_DIR considerada insegura para eliminación automática. Por favor elimina manualmente si procede."
      fi
  else
      info "No existe $LOG_DIR"
  fi

  # Restaurar GDM custom.conf desde backup si existe
  if [ -f "$GDM_BACKUP" ]; then
      info "Restaurando $GDM_CUSTOM desde backup $GDM_BACKUP"
      mv -f "$GDM_BACKUP" "$GDM_CUSTOM" || info "No se pudo restaurar $GDM_CUSTOM desde backup"
  else
      if [ -f "$GDM_CUSTOM" ] && grep -q '^\s*WaylandEnable\s*=\s*false' "$GDM_CUSTOM"; then
          info "No se encontró backup de $GDM_CUSTOM. Intentando retirar la línea 'WaylandEnable=false' si fue añadida por este script."
          sed -i '/^\s*WaylandEnable\s*=\s*false\s*$/d' "$GDM_CUSTOM" || true
      fi
  fi

  # Eliminar el archivo de LightDM si lo creamos (solo si existe)
  if [ -f "$LIGHTDM_CONF_FILE" ]; then
      info "Eliminando $LIGHTDM_CONF_FILE (si fue creado por el instalador)..."
      rm -f "$LIGHTDM_CONF_FILE" || true
      if [ -d "$LIGHTDM_CONF_DIR" ] && [ -z "$(ls -A "$LIGHTDM_CONF_DIR")" ]; then
          rmdir "$LIGHTDM_CONF_DIR" || true
      fi
  fi

  # Eliminar unidades restantes por nombre si están
  info "Eliminando posibles restos de unidades systemd relacionadas..."
  rm -f "$SYSTEMD_DIR/innovaC2_"* 2>/dev/null || true

  systemctl daemon-reload || true

  # Eliminar archivo config.env local si existe
  if [ -f "./config.env" ]; then
      info "Eliminando ./config.env"
      rm -f ./config.env || true
  fi

  info "Desinstalación finalizada."
  info "Recomendación: reinicia el equipo para asegurar que los cambios en el gestor de sesiones tomen efecto."
  exit 0
}

# ----------------------------
# Ejecutar el flujo elegido (o mostrar menú si no se proporcionó)
# ----------------------------
if [ -z "$MODE" ]; then
  echo
  echo "Seleccione una opción:"
  echo "  1) Instalar innovaC2 (comportamiento normal - usa systemd --user para el cliente)"
  echo "  2) Desinstalar completamente innovaC2 (revertir cambios)"
  echo
  read -r -p "Elige 1 o 2: " MODE
  echo
fi

if [ "$MODE" != "1" ] && [ "$MODE" != "2" ]; then
    err "Opción inválida."
    exit 1
fi

if [ "$MODE" = "1" ]; then
  install_flow
elif [ "$MODE" = "2" ]; then
  uninstall_flow
fi
