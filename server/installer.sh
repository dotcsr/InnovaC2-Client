#!/usr/bin/env bash
set -euo pipefail

# ==============================================================
#  InnovaC2 Server - Instalador / Desinstalador
# ==============================================================
#  Este script instala o desinstala el servidor InnovaC2.
#  - Instala Python 3.9 + entorno virtual
#  - Copia la carpeta `server` desde el repositorio
#  - Crea y configura un servicio systemd
#  - Desinstalación limpia: elimina servicio, logs y archivos
#
#  Uso:
#     sudo ./installer_innovaC2_server.sh
# ==============================================================

REPO_URL="https://github.com/dotcsr/InnovaC2-Client.git"
INSTALL_DIR="/opt/innovaC2-server"
SERVICE_NAME="innovaC2-server.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
PYTHON_BIN="python3.9"

GREEN="\e[32m"; RED="\e[31m"; YELLOW="\e[33m"; NC="\e[0m"

# === Validaciones iniciales ===
if [[ $(id -u) -ne 0 ]]; then
  echo -e "${RED}❌ Este script debe ejecutarse como root.${NC}"
  exit 1
fi

if ! SYSTEM_USER=$(logname 2>/dev/null); then
  echo -e "${RED}❌ No se pudo determinar el usuario activo (logname falló).${NC}"
  exit 1
fi

if [[ "$SYSTEM_USER" == "root" ]]; then
  echo -e "${RED}❌ No se permite instalar ni ejecutar el servicio como root.${NC}"
  exit 1
fi

# ==============================================================
#  Funciones auxiliares
# ==============================================================

detect_pkg_mgr() {
  if command -v apt-get >/dev/null 2>&1; then echo "apt"
  elif command -v dnf >/dev/null 2>&1; then echo "dnf"
  elif command -v yum >/dev/null 2>&1; then echo "yum"
  elif command -v pacman >/dev/null 2>&1; then echo "pacman"
  else echo "unknown"; fi
}

install_python() {
  local PKG_MGR
  PKG_MGR=$(detect_pkg_mgr)
  echo -e "${YELLOW}Instalando Python 3.9...${NC}"
  case "$PKG_MGR" in
    apt)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -y
      apt-get install -y software-properties-common curl git || true
      add-apt-repository -y ppa:deadsnakes/ppa || true
      apt-get update -y
      apt-get install -y --no-install-recommends python3.9 python3.9-venv python3.9-distutils
      ;;
    dnf) dnf install -y python39 python39-devel python39-venv git curl ;;
    yum) yum install -y python39 python39-devel git curl ;;
    pacman) pacman -Syu --noconfirm python git curl ;;
    *) echo "Gestor de paquetes no reconocido. Instala Python 3.9 manualmente."; exit 1 ;;
  esac
}

ensure_python_and_pip() {
  if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    install_python
  fi
  if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
    echo "Instalando pip..."
    "$PYTHON_BIN" -m ensurepip --default-pip || {
      curl -sS https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
      "$PYTHON_BIN" /tmp/get-pip.py
      rm -f /tmp/get-pip.py
    }
    "$PYTHON_BIN" -m pip install --upgrade pip
  fi
}

# ==============================================================
#  Función: INSTALAR
# ==============================================================

install_innova() {
  echo -e "${GREEN}=== Instalando InnovaC2 Server ===${NC}"
  TMP_DIR="/tmp/innovaC2_installer_$$"
  mkdir -p "$TMP_DIR"
  trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

  ensure_python_and_pip
  echo "Usando $($PYTHON_BIN --version)"

  echo "Descargando código desde GitHub..."
  git clone --depth 1 "$REPO_URL" "$TMP_DIR/repo"

  if [[ ! -d "$TMP_DIR/repo/server" ]]; then
    echo -e "${RED}❌ No se encontró la carpeta 'server' en el repositorio.${NC}"
    exit 1
  fi

  echo "Instalando en $INSTALL_DIR..."
  rm -rf "$INSTALL_DIR"
  mkdir -p "$INSTALL_DIR"
  cp -r "$TMP_DIR/repo/server" "$INSTALL_DIR/server"
  chown -R "$SYSTEM_USER":"$SYSTEM_USER" "$INSTALL_DIR"

  echo "Creando entorno virtual..."
  sudo -u "$SYSTEM_USER" "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"

  if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]]; then
    echo -e "${RED}❌ Falló la creación del entorno virtual.${NC}"
    exit 1
  fi

  echo "Actualizando pip e instalando dependencias..."
  sudo -u "$SYSTEM_USER" "$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip
  if [[ -f "$INSTALL_DIR/server/requirements.txt" ]]; then
    sudo -u "$SYSTEM_USER" "$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/server/requirements.txt"
  else
    echo -e "${YELLOW}⚠️ No se encontró requirements.txt, omitiendo.${NC}"
  fi

  echo "Creando servicio systemd..."
  cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=InnovaC2 Server
After=network.target

[Service]
Type=simple
User=%i  # ya está: $SYSTEM_USER en tu script original
WorkingDirectory=/opt/innovaC2-server/server
ExecStart=/opt/innovaC2-server/venv/bin/python /opt/innovaC2-server/server/server.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/innovaC2-server.log
StandardError=append:/var/log/innovaC2-server.log
Environment=PATH=/opt/innovaC2-server/venv/bin:/usr/bin:/bin
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF

  chmod 644 "$SERVICE_PATH"
  touch /var/log/innovaC2-server.log
  chown "$SYSTEM_USER":"$SYSTEM_USER" /var/log/innovaC2-server.log
  chmod 640 /var/log/innovaC2-server.log

  systemctl daemon-reload
  systemctl enable --now "$SERVICE_NAME"

  echo -e "${GREEN}✅ Instalación completada exitosamente.${NC}"
  echo "Servicio: $SERVICE_NAME"
  echo "Ruta: $INSTALL_DIR"
  echo "Usuario: $SYSTEM_USER"
  systemctl status --no-pager "$SERVICE_NAME" || true
}

# ==============================================================
#  Función: DESINSTALAR
# ==============================================================

uninstall_innova() {
  echo -e "${RED}=== Desinstalando InnovaC2 Server ===${NC}"

  if systemctl is-active --quiet "$SERVICE_NAME"; then
    echo "Deteniendo servicio..."
    systemctl stop "$SERVICE_NAME" || true
  fi

  if systemctl is-enabled --quiet "$SERVICE_NAME"; then
    echo "Deshabilitando servicio..."
    systemctl disable "$SERVICE_NAME" || true
  fi

  echo "Eliminando unidad systemd..."
  rm -f "$SERVICE_PATH"

  echo "Eliminando archivos de instalación..."
  rm -rf "$INSTALL_DIR"

  echo "Eliminando logs..."
  rm -f /var/log/innovaC2-server.log

  systemctl daemon-reload
  echo -e "${GREEN}✅ Desinstalación completada.${NC}"
}

# ==============================================================
#  Menú principal
# ==============================================================

clear
echo -e "${GREEN}=== Gestor de instalación InnovaC2 Server ===${NC}"
echo "Usuario detectado: ${YELLOW}${SYSTEM_USER}${NC}"
echo ""
echo "1) Instalar InnovaC2 Server"
echo "2) Desinstalar InnovaC2 Server"
echo ""
read -rp "Selecciona una opción [1-2]: " choice

case "$choice" in
  1) install_innova ;;
  2) uninstall_innova ;;
  *) echo -e "${RED}Opción inválida.${NC}" ;;
esac
