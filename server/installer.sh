#!/usr/bin/env bash
set -euo pipefail

# installer_innovaC2_server.sh
# Descarga la carpeta `server` del repo especificado, verifica/instala Python 3.9 + pip,
# crea un venv, instala requirements.txt y crea un servicio systemd para ejecutar server.py
# Se debe ejecutar como root: sudo ./installer_innovaC2_server.sh

REPO_URL="https://github.com/dotcsr/InnovaC2-Client.git"
INSTALL_DIR="/opt/innovaC2-server"
TMP_DIR="/tmp/innovaC2_installer_$$"
SERVICE_NAME="innovaC2-server.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
SYSTEM_USER="$(logname)"
PYTHON_BIN="python3.9"

echo "=== InnovaC2 Server installer ==="

if [[ $(id -u) -ne 0 ]]; then
  echo "Este script debe ejecutarse como root. Usa: sudo $0"
  exit 1
fi

mkdir -p "$TMP_DIR"


cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

# Detect package manager
detect_pkg_mgr() {
  if command -v apt-get >/dev/null 2>&1; then
    echo "apt"
  elif command -v dnf >/dev/null 2>&1; then
    echo "dnf"
  elif command -v yum >/dev/null 2>&1; then
    echo "yum"
  elif command -v pacman >/dev/null 2>&1; then
    echo "pacman"
  else
    echo "unknown"
  fi
}

PKG_MGR=$(detect_pkg_mgr)

install_python_apt() {
  apt-get update
  # Use deadsnakes if available to get python3.9 on older Ubuntus
  if ! apt-cache policy | grep -q "deadsnakes"; then
    apt-get install -y software-properties-common || true
    add-apt-repository -y ppa:deadsnakes/ppa || true
    apt-get update || true
  fi
  apt-get install -y --no-install-recommends python3.9 python3.9-venv python3.9-distutils git curl
}

install_python_dnf() {
  dnf install -y python39 python39-devel python39-venv git curl || true
}

install_python_yum() {
  yum install -y python39 python39-devel git curl || true
}

install_python_pacman() {
  pacman -Syu --noconfirm python git curl || true
}

# Install python3.9 if missing
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.9 no encontrado. Instalando..."
  case "$PKG_MGR" in
    apt)
      install_python_apt
      ;;
    dnf)
      install_python_dnf
      ;;
    yum)
      install_python_yum
      ;;
    pacman)
      install_python_pacman
      ;;
    *)
      echo "Gestor de paquetes no reconocido. Por favor instala Python 3.9 manualmente."
      exit 1
      ;;
  esac
fi

# Ensure python3.9 exists now
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "No se pudo instalar python3.9 automáticamente. Salida."
  exit 1
fi

# Ensure pip for python3.9
if ! "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then
  echo "pip para python3.9 no encontrado. Intentando instalar pip..."
  # try ensurepip
  if "$PYTHON_BIN" -m ensurepip --default-pip >/dev/null 2>&1; then
    "$PYTHON_BIN" -m pip install --upgrade pip
  else
    # fallback to get-pip.py
    curl -sS https://bootstrap.pypa.io/get-pip.py -o "$TMP_DIR/get-pip.py"
    "$PYTHON_BIN" "$TMP_DIR/get-pip.py"
    rm -f "$TMP_DIR/get-pip.py"
  fi
fi

echo "Usando $($PYTHON_BIN --version)"

# Clone repository (shallow) to temp
echo "Descargando repo $REPO_URL..."
if command -v git >/dev/null 2>&1; then
  git clone --depth 1 "$REPO_URL" "$TMP_DIR/repo"
else
  echo "git no encontrado. Instalando git..."
  case "$PKG_MGR" in
    apt) apt-get install -y git ;; 
    dnf) dnf install -y git ;; 
    yum) yum install -y git ;; 
    pacman) pacman -S --noconfirm git ;; 
  esac
  git clone --depth 1 "$REPO_URL" "$TMP_DIR/repo"
fi

# Verify server folder exists
if [[ ! -d "$TMP_DIR/repo/server" ]]; then
  echo "No se encontró la carpeta 'server' en el repositorio. Ruta esperada: server/"
  exit 1
fi

# Create install dir and copy server
echo "Instalando en $INSTALL_DIR..."
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp -r "$TMP_DIR/repo/server" "$INSTALL_DIR/server"

# Create dedicated system user
if ! id -u "$SYSTEM_USER" >/dev/null 2>&1; then
  echo "Creando usuario de sistema: $SYSTEM_USER"
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SYSTEM_USER" || true
fi

chown -R "$SYSTEM_USER":"$SYSTEM_USER" "$INSTALL_DIR"

# Create virtualenv and install requirements
echo "Creando virtualenv con $PYTHON_BIN..."
"$PYTHON_BIN" -m venv "$INSTALL_DIR/venv"
# Ensure pip is up to date for venv
"$INSTALL_DIR/venv/bin/python" -m pip install --upgrade pip || true

REQ_FILE="$INSTALL_DIR/server/requirements.txt"
if [[ -f "$REQ_FILE" ]]; then
  echo "Instalando dependencias desde $REQ_FILE..."
  "$INSTALL_DIR/venv/bin/pip" install -r "$REQ_FILE"
else
  echo "No se encontró requirements.txt en $REQ_FILE. Omitiendo instalación de dependencias." 
fi

# Create systemd service
echo "Creando unidad systemd en $SERVICE_PATH"
cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=InnovaC2 Server (uvicorn)
After=network.target

[Service]
Type=exec
User=$SYSTEM_USER
WorkingDirectory=$INSTALL_DIR/server
StandardOutput=append:/var/log/innovaC2-server.log
StandardError=append:/var/log/innovaC2-server.log
ExecStart=$INSTALL_DIR/venv/bin/python3 $INSTALL_DIR/server/server.py
Restart=always
RestartSec=5
Environment=PATH=$INSTALL_DIR/venv/bin:/usr/bin:/bin

[Install]
WantedBy=multi-user.target
EOF

chmod 644 "$SERVICE_PATH"

# Crear archivo de log con permisos correctos
touch /var/log/innovaC2-server.log
chown $SYSTEM_USER:$SYSTEM_USER /var/log/innovaC2-server.log
chmod 640 /var/log/innovaC2-server.log

# Reload systemd, enable and start service
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"

echo "Servicio habilitado y arrancado. Estado actual:"
systemctl status --no-pager "$SERVICE_NAME" || true

echo "Instalación completada. El servidor debería escuchar en el puerto 9000 (si server.py lo configura así)."

# end
