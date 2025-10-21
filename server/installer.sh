#!/usr/bin/env bash
set -euo pipefail

# installer_innovaC2_server.sh
# Instalador limpio para InnovaC2 Server
# Descarga la carpeta `server` desde GitHub y crea un entorno virtual
# que ejecuta server.py bajo el usuario logueado (no root).

REPO_URL="https://github.com/dotcsr/InnovaC2-Client.git"
INSTALL_DIR="/opt/innovaC2-server"
TMP_DIR="/tmp/innovaC2_installer_$$"
SERVICE_NAME="innovaC2-server.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
PYTHON_BIN="python3.9"

# === Detectar usuario logueado ===
if ! SYSTEM_USER=$(logname 2>/dev/null); then
  echo "❌ No se pudo determinar el usuario activo (logname falló)."
  exit 1
fi

if [[ "$SYSTEM_USER" == "root" ]]; then
  echo "❌ No se permite instalar como root. Usa: sudo ./installer_innovaC2_server.sh desde un usuario normal."
  exit 1
fi

GREEN="\e[32m"; RED="\e[31m"; YELLOW="\e[33m"; NC="\e[0m"
echo -e "${GREEN}=== Instalador de InnovaC2 Server ===${NC}"
echo "Instalando para el usuario: ${YELLOW}${SYSTEM_USER}${NC}"

if [[ $(id -u) -ne 0 ]]; then
  echo "❌ Este script debe ejecutarse como root. Usa: sudo $0"
  exit 1
fi

mkdir -p "$TMP_DIR"
trap 'rm -rf "$TMP_DIR"' EXIT INT TERM

# === Detectar gestor de paquetes ===
detect_pkg_mgr() {
  if command -v apt-get >/dev/null 2>&1; then echo "apt"
  elif command -v dnf >/dev/null 2>&1; then echo "dnf"
  elif command -v yum >/dev/null 2>&1; then echo "yum"
  elif command -v pacman >/dev/null 2>&1; then echo "pacman"
  else echo "unknown"; fi
}

PKG_MGR=$(detect_pkg_mgr)

# === Instalar Python 3.9 si falta ===
install_python_apt() {
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y software-properties-common curl git || true
  add-apt-repository -y ppa:deadsnakes/ppa || true
  apt-get update -y
  apt-get install -y --no-install-recommends python3.9 python3.9-venv python3.9-distutils
}
install_python_dnf() { dnf install -y python39 python39-devel python39-venv git curl; }
install_python_yum() { yum install -y python39 python39-devel git curl; }
install_python_pacman() { pacman -Syu --noconfirm python git curl; }

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo -e "${YELLOW}Python 3.9 no encontrado. Instalando...${NC}"
  case "$PKG_MGR" in
    apt) install_python_apt ;;
    dnf) install_python_dnf ;;
    yum) install_python_yum ;;
    pacman) install_python_pacman ;;
    *) echo "Gestor de paquetes no reconocido. Instala Python 3.9 manualmente."; exit 1 ;;
  esac
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo -e "${RED}❌ No se pudo instalar Python 3.9 automáticamente.${NC}"
  exit 1
fi

# === Instalar pip si falta ===
if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
  echo "Instalando pip..."
  "$PYTHON_BIN" -m ensurepip --default-pip || {
    curl -sS https://bootstrap.pypa.io/get-pip.py -o "$TMP_DIR/get-pip.py"
    "$PYTHON_BIN" "$TMP_DIR/get-pip.py"
  }
  "$PYTHON_BIN" -m pip install --upgrade pip
fi

echo "Usando $($PYTHON_BIN --version)"

# === Descargar el repositorio ===
echo "Descargando código desde GitHub..."
git clone --depth 1 "$REPO_URL" "$TMP_DIR/repo"

if [[ ! -d "$TMP_DIR/repo/server" ]]; then
  echo -e "${RED}❌ No se encontró la carpeta 'server' en el repositorio.${NC}"
  exit 1
fi

# === Instalar en /opt/innovaC2-server ===
echo "Instalando en $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -r "$TMP_DIR/repo/server" "$INSTALL_DIR/server"

# Asignar propiedad al usuario logueado
chown -R "$SYSTEM_USER":"$SYSTEM_USER" "$INSTALL_DIR"

# === Crear entorno virtual dentro de /opt/innovaC2-server/venv ===
echo "Creando entorno virtual en $INSTALL_DIR/venv..."
sudo -u "$SYSTEM_USER" "$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"

# Confirmar que el venv se creó correctamente
if [[ ! -x "$INSTALL_DIR/venv/bin/python" ]]; then
  echo -e "${RED}❌ Falló la creación del entorno virtual.${NC}"
  exit 1
fi

# Actualizar pip e instalar dependencias
sudo -u "$SYSTEM_USER" "$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip
REQ_FILE="$INSTALL_DIR/server/requirements.txt"
if [[ -f "$REQ_FILE" ]]; then
  echo "Instalando dependencias desde requirements.txt..."
  sudo -u "$SYSTEM_USER" "$INSTALL_DIR/venv/bin/pip" install -r "$REQ_FILE"
else
  echo -e "${YELLOW}⚠️ No se encontró $REQ_FILE, omitiendo instalación de dependencias.${NC}"
fi

# === Crear servicio systemd ===
echo "Creando servicio systemd para el usuario $SYSTEM_USER..."

cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=InnovaC2 Server
After=network.target

[Service]
Type=simple
User=$SYSTEM_USER
WorkingDirectory=$INSTALL_DIR/server
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/server/server.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/innovaC2-server.log
StandardError=append:/var/log/innovaC2-server.log
Environment=PATH=$INSTALL_DIR/venv/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "$SERVICE_PATH"
touch /var/log/innovaC2-server.log
chown "$SYSTEM_USER":"$SYSTEM_USER" /var/log/innovaC2-server.log
chmod 640 /var/log/innovaC2-server.log

# === Activar servicio ===
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo -e "${GREEN}✅ Instalación completada correctamente.${NC}"
systemctl status --no-pager "$SERVICE_NAME" || true
echo -e "${YELLOW}El servidor se ejecuta bajo el usuario: $SYSTEM_USER${NC}"
echo -e "${YELLOW}Ruta del entorno virtual:${NC} $INSTALL_DIR/venv"
