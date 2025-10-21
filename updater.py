#!/usr/bin/env python3
import os
import sys
import requests
import subprocess

# === CONFIGURACIÓN ===
BASE_URL = "https://raw.githubusercontent.com/dotcsr/InnovaC2-Client/main"  # Cambia esto
LOCAL_PATH = os.path.dirname(os.path.abspath(__file__))
MAIN_FILE = os.path.join(LOCAL_PATH, "client.py")
LOCAL_VERSION_FILE = os.path.join(LOCAL_PATH, ".version")
REMOTE_VERSION_FILE = f"{BASE_URL}/version.txt"
REMOTE_MAIN_FILE = f"{BASE_URL}/client.py"
REMOTE_REQUIREMENTS = f"{BASE_URL}/requirements.txt"
LOCAL_REQUIREMENTS = os.path.join(LOCAL_PATH, "requirements.txt")

# === FUNCIONES ===

def get_remote_version():
    try:
        r = requests.get(REMOTE_VERSION_FILE, timeout=10)
        return r.text.strip()
    except Exception as e:
        print(f"⚠️ No se pudo obtener la versión remota: {e}")
        return None


def get_local_version():
    if not os.path.exists(LOCAL_VERSION_FILE):
        return None
    with open(LOCAL_VERSION_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def update_file(remote_url, local_path):
    """Descarga un archivo del servidor y lo guarda localmente sin alterar formato"""
    r = requests.get(remote_url, timeout=10)
    if r.status_code == 200:
        # Guardar el archivo exactamente como está en el repositorio (modo binario)
        with open(local_path, "wb") as f:
            f.write(r.content)
        print(f"📄 Actualizado: {os.path.basename(local_path)}")

        # Intentar formatear el código automáticamente (si tienes black instalado)
        if local_path.endswith(".py"):
            try:
                subprocess.run([sys.executable, "-m", "black", local_path], check=False)
            except Exception:
                pass
    else:
        print(f"⚠️ No se pudo descargar {remote_url}")


def ensure_requirements():
    """Instala dependencias nuevas si es necesario"""
    if os.path.exists(LOCAL_REQUIREMENTS):
        subprocess.call([sys.executable, "-m", "pip", "install", "-r", LOCAL_REQUIREMENTS])


def update_code():
    """Actualiza el código si hay una nueva versión"""
    print("🔍 Verificando actualizaciones...")

    remote_version = get_remote_version()
    if not remote_version:
        print("❌ No se pudo obtener la versión remota.")
        return

    local_version = get_local_version()
    if local_version == remote_version:
        print("🟢 Ya está en la última versión.")
        return

    print(f"📦 Nueva versión disponible: {remote_version} (actual: {local_version or 'ninguna'})")

    try:
        update_file(REMOTE_MAIN_FILE, MAIN_FILE)
        update_file(REMOTE_REQUIREMENTS, LOCAL_REQUIREMENTS)
        ensure_requirements()
        with open(LOCAL_VERSION_FILE, "w", encoding="utf-8") as f:
            f.write(remote_version)
        print("✅ Actualización completa.")
    except Exception as e:
        print(f"⚠️ Error durante la actualización: {e}")


def main():
    update_code()
    # Si quieres ejecutar el programa automáticamente después de actualizar:
    #os.execv(sys.executable, [sys.executable, MAIN_FILE])
    subprocess.call(["systemctl", "--user", "restart", "innovaC2_client"])


if __name__ == "__main__":
    main()
