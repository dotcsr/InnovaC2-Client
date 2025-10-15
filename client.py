import argparse
import asyncio
import json
import platform
import socket
import subprocess
import sys
import time
import base64
import io
import os
import requests
from datetime import datetime

import websockets

def check_for_update(repo_url, local_file):
    """
    Verifica si hay una versión más reciente del cliente y la descarga automáticamente.
    repo_url: URL directa al archivo más reciente (por ejemplo en GitHub Raw)
    local_file: ruta al archivo actual (este script)
    """
    try:
        response = requests.get(repo_url, timeout=10)
        if response.status_code == 200:
            remote_code = response.text
            with open(local_file, "r", encoding="utf-8") as f:
                local_code = f.read()

            if remote_code.strip() != local_code.strip():
                print("[UPDATE] Nueva versión disponible. Actualizando cliente...")
                backup = local_file + ".bak"
                os.rename(local_file, backup)
                with open(local_file, "w", encoding="utf-8") as f:
                    f.write(remote_code)
                print("[UPDATE] Cliente actualizado. Reiniciando...")
                os.execv(sys.executable, ['python'] + sys.argv)
        else:
            print("[UPDATE] No se pudo verificar actualizaciones (HTTP {})".format(response.status_code))
    except Exception as e:
        print("[UPDATE] Error verificando actualizaciones:", e)

# screen capture
try:
    import mss
    from PIL import Image
except Exception:
    mss = None
    Image = None

# === NUEVO: ventana bonita para mensajes ===
async def show_message_text(msg):
    """
    Muestra un mensaje en una ventana sin bordes, moderna y centrada,
    con título, ícono y botón Aceptar.
    Si no hay entorno gráfico, usa notify-send o print.
    """
    try:
        import tkinter as tk
        import threading

        def _show():
            root = tk.Tk()
            root.overrideredirect(True)  # sin bordes
            root.attributes("-topmost", True)
            root.configure(bg="#1E1E1E")

            # ======= Contenedor principal =======
            frame = tk.Frame(root, bg="#1E1E1E")
            frame.pack(padx=40, pady=35)

            # ======= Encabezado =======
            header_frame = tk.Frame(frame, bg="#1E1E1E")
            header_frame.pack(anchor="center")

            # Ícono 📩
            icon_label = tk.Label(
                header_frame,
                text="📩",
                font=("Segoe UI Emoji", 28),
                bg="#1E1E1E",
                fg="#EAEAEA"
            )
            icon_label.pack(pady=(0, 10))

            # Título
            title_label = tk.Label(
                header_frame,
                text="Tienes un mensaje de dirección",
                font=("Segoe UI", 13, "bold"),
                bg="#1E1E1E",
                fg="#FFFFFF"
            )
            title_label.pack(pady=(0, 15))

            # ======= Mensaje principal =======
            msg_label = tk.Label(
                frame,
                text=msg,
                fg="#DCDCDC",
                bg="#1E1E1E",
                font=("Segoe UI", 11),
                justify="center",
                wraplength=420
            )
            msg_label.pack(pady=(0, 30))  # margen inferior amplio antes del botón

            # ======= Botón Aceptar =======
            def close():
                root.destroy()

            btn = tk.Button(
                frame,
                text="Aceptar",
                command=close,
                bg="#2D2D2D",
                fg="#FFFFFF",
                activebackground="#3C3C3C",
                activeforeground="#FFFFFF",
                relief="flat",
                font=("Segoe UI", 10, "bold"),
                padx=25,
                pady=8,
                borderwidth=0
            )
            btn.pack(pady=(0, 10))
            btn.configure(cursor="hand2")

            # Bordes redondeados (opcional, si tu sistema los soporta)
            try:
                root.tk.call("tk", "scaling", 1.2)
            except:
                pass

            # Centrar ventana
            root.update_idletasks()
            width = root.winfo_reqwidth()
            height = root.winfo_reqheight()
            screen_w = root.winfo_screenwidth()
            screen_h = root.winfo_screenheight()
            x = (screen_w // 2) - (width // 2)
            y = (screen_h // 2) - (height // 2)
            root.geometry(f"+{x}+{y}")

            root.mainloop()

        # Ejecutar en hilo para no bloquear asyncio
        threading.Thread(target=_show, daemon=True).start()

    except Exception as e:
        # Si no hay GUI disponible
        try:
            subprocess.run(["notify-send", "Mensaje remoto", msg])
        except Exception:
            print(f"[MESSAGE] {msg}")

async def execute_command(cmd):
    try:
        proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        out, err = proc.communicate(timeout=30)
        return {"stdout": out, "stderr": err, "returncode": proc.returncode}
    except subprocess.TimeoutExpired:
        proc.kill()
        return {"stdout": "", "stderr": "timeout", "returncode": -1}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "returncode": -1}

async def stream_screen(ws, client_id, fps=2, quality=50):
    if mss is None or Image is None:
        print("mss or Pillow not installed; cannot stream screen.")
        return
    interval = 1.0 / max(1, fps)
    try:
        with mss.mss() as sct:
            while True:
                try:
                    img = sct.grab(sct.monitors[0])
                    img_pil = Image.frombytes("RGB", img.size, img.rgb)
                    buffer = io.BytesIO()
                    img_pil.save(buffer, format="JPEG", quality=quality)
                    frame_data = buffer.getvalue()
                    frame_b64 = base64.b64encode(frame_data).decode("ascii")
                    await ws.send(json.dumps({
                        "type": "screen_frame",
                        "client_id": client_id,
                        "frame": frame_b64
                    }))
                    try:
                        await asyncio.sleep(interval)
                    except asyncio.CancelledError:
                        break
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    print("Error streaming screen frame:", e)
                    break
    except asyncio.CancelledError:
        return
    except Exception as e:
        print("Stream session error:", e)
        return

async def run_agent(uri, client_id, name):
    while True:
        try:
            print(f"Connecting to {uri} ...")
            async with websockets.connect(uri, max_size=None) as ws:
                hostname = socket.gethostname()
                await ws.send(json.dumps({"type":"register", "client_id": client_id, "hostname": hostname, "name": name}))
                print("Registered. Listening...")
                stream_task = None

                async def heartbeat():
                    while True:
                        await asyncio.sleep(15)
                        try:
                            await ws.send(json.dumps({"type":"heartbeat"}))
                        except:
                            return

                hb_task = asyncio.create_task(heartbeat())
                try:
                    async for msg in ws:
                        try:
                            j = json.loads(msg)
                        except Exception:
                            continue
                        mtype = j.get("type")
                        if mtype == "message":
                            await show_message_text(j.get("message",""))
                        elif mtype == "exec":
                            cmd = j.get("command")
                            cmd_id = j.get("cmd_id")
                            print(f"Executing command: {cmd}")
                            res = await execute_command(cmd)
                            payload = {"type":"cmd_result", "cmd_id": cmd_id, "stdout": res["stdout"], "stderr": res["stderr"], "returncode": res["returncode"]}
                            try:
                                await ws.send(json.dumps(payload))
                            except Exception as e:
                                print("Failed to send cmd result:", e)
                        elif mtype == "set_name":
                            newname = j.get("name")
                            print("Name set to:", newname)
                        elif mtype == "start_screen_stream":
                            print("Starting screen stream...")
                            if stream_task and not stream_task.done():
                                continue
                            stream_task = asyncio.create_task(stream_screen(ws, client_id, fps=2, quality=50))
                        elif mtype == "stop_screen_stream":
                            print("Stopping screen stream...")
                            if stream_task and not stream_task.done():
                                stream_task.cancel()
                                try:
                                    await stream_task
                                except asyncio.CancelledError:
                                    pass
                                except Exception as e:
                                    print("Error awaiting cancelled stream_task:", e)
                                stream_task = None
                        else:
                            pass
                finally:
                    hb_task.cancel()
                    try:
                        await hb_task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        pass
                    if stream_task and not stream_task.done():
                        stream_task.cancel()
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass
                        except Exception as e:
                            print("Error awaiting stream_task in finally:", e)
                        stream_task = None

        except websockets.exceptions.ConnectionClosed as cc:
            print(f"Connection failed or lost: {cc}")
            time.sleep(3)
        except Exception as e:
            print("Connection failed or lost:", e)
            time.sleep(3)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--server", required=True, help="WebSocket server URL e.g. ws://server:9000/ws/client")
    parser.add_argument("--id", required=True, help="Unique client id")
    parser.add_argument("--name", default="", help="Friendly name")
    args = parser.parse_args()
    try:
        # === AUTOUPDATE ===
        check_for_update(
            repo_url="https://raw.githubusercontent.com/tu_usuario/tu_repo/main/cliente.py",
            local_file=os.path.abspath(__file__)
        )
        asyncio.run(run_agent(args.server, args.id, args.name))
    except KeyboardInterrupt:
        print("Client interrupted by user, exiting.")
    except Exception as e:
        print("Client exiting due to exception:", e)

if __name__ == "__main__":
    main()
