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
from datetime import datetime

import websockets

# screen capture
try:
    import mss
    from PIL import Image
except Exception:
    mss = None
    Image = None

import asyncio
import threading
import subprocess

async def show_message_text(msg):
    """
    Muestra un mensaje en una ventana sin bordes, moderna y centrada en la pantalla principal,
    con título, ícono y botón Aceptar.
    Si no hay entorno gráfico o no se puede crear la GUI en el hilo actual, usa notify-send o print.
    - Requisitos: screeninfo (pip install screeninfo)
    - Debe llamarse desde el hilo principal para que la ventana Tk se muestre correctamente.
    """
    try:
        import tkinter as tk
        from screeninfo import get_monitors
    except Exception:
        # no tkinter o screeninfo: fallback
        try:
            subprocess.run(["notify-send", "Mensaje remoto", msg])
        except Exception:
            print(f"[MESSAGE] {msg}")
        return

    # Si no estamos en el hilo principal, no intentamos crear Tk (evita segfault en Linux)
    if threading.current_thread() is not threading.main_thread():
        try:
            subprocess.run(["notify-send", "Mensaje remoto", msg])
        except Exception:
            print(f"[MESSAGE] {msg}")
        return

    # Obtenemos el loop de asyncio (la función es async, así que debe existir)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Si por alguna razón no hay loop corriendo, hacer fallback
        try:
            subprocess.run(["notify-send", "Mensaje remoto", msg])
        except Exception:
            print(f"[MESSAGE] {msg}")
        return

    # Inicializar root una sola vez (guardado como atributo en la función)
    if not hasattr(show_message_text, "_tk_root") or show_message_text._tk_root is None:
        root = tk.Tk()
        root.withdraw()  # ocultamos el root principal
        show_message_text._tk_root = root
        show_message_text._tk_update_interval = 0.02  # intervalo en segundos para procesar eventos

        # updater que llama periódicamente a root.update() usando el loop de asyncio
        def _tk_updater():
            root_ref = getattr(show_message_text, "_tk_root", None)
            if root_ref is None:
                return
            try:
                root_ref.update()
            except tk.TclError:
                # root fue destruido externamente
                show_message_text._tk_root = None
                return
            # re-schedule
            try:
                loop.call_later(show_message_text._tk_update_interval, _tk_updater)
            except Exception:
                # si el loop ya no existe, limpiamos
                show_message_text._tk_root = None

        # arrancar el updater en el próximo ciclo del loop
        loop.call_soon(_tk_updater)
    else:
        root = show_message_text._tk_root

    # --- Crear la ventana (Toplevel) y su contenido ---
    try:
        win = tk.Toplevel(root)
        win.overrideredirect(True)  # sin bordes
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        win.configure(bg="#1E1E1E")

        # Contenedor principal
        frame = tk.Frame(win, bg="#1E1E1E")
        frame.pack(padx=40, pady=35)

        # Header
        header_frame = tk.Frame(frame, bg="#1E1E1E")
        header_frame.pack(anchor="center")

        # Ícono (fallback si la fuente emoji no existe)
        try:
            icon_label = tk.Label(
                header_frame,
                text="📩",
                font=("Noto Color Emoji", 25),
                bg="#1E1E1E",
                fg="#EAEAEA"
            )
        except Exception:
            icon_label = tk.Label(
                header_frame,
                text="📩",
                bg="#1E1E1E",
                fg="#EAEAEA"
            )
        icon_label.pack(pady=(0, 10))

        # Título (fallback de fuente)
        try:
            title_font = ("Segoe UI", 13, "bold")
        except Exception:
            title_font = None

        title_label = tk.Label(
            header_frame,
            text="Tienes un mensaje de dirección",
            font=title_font,
            bg="#1E1E1E",
            fg="#FFFFFF"
        )
        title_label.pack(pady=(0, 15))

        # Mensaje principal
        try:
            msg_font = ("Segoe UI", 11)
        except Exception:
            msg_font = None

        msg_label = tk.Label(
            frame,
            text=msg,
            fg="#DCDCDC",
            bg="#1E1E1E",
            font=msg_font,
            justify="center",
            wraplength=420
        )
        msg_label.pack(pady=(0, 30))

        # Botón Aceptar
        def _close():
            try:
                try:
                    win.overrideredirect(False)
                except:
                    pass
                try:
                    win.attributes("-topmost", False)
                except:
                    pass
                win.update_idletasks()
                win.withdraw()
            except Exception:
                pass


        btn = tk.Button(
            frame,
            text="Aceptar",
            command=_close,
            bg="#2D2D2D",
            fg="#FFFFFF",
            activebackground="#3C3C3C",
            activeforeground="#FFFFFF",
            relief="flat",
            font=("Segoe UI", 10, "bold") if title_font else None,
            padx=25,
            pady=8,
            borderwidth=0
        )
        btn.pack(pady=(0, 10))
        try:
            btn.configure(cursor="hand2")
        except Exception:
            pass

        # scale attempt (no crítico)
        try:
            win.tk.call("tk", "scaling", 1.2)
        except Exception:
            pass

        # Centrar en pantalla principal
        win.update_idletasks()
        width = win.winfo_reqwidth()
        height = win.winfo_reqheight()

        try:
            monitor = get_monitors()[0]  # pantalla principal
            screen_x = monitor.x
            screen_y = monitor.y
            screen_w = monitor.width
            screen_h = monitor.height

            x = screen_x + (screen_w // 2) - (width // 2)
            y = screen_y + (screen_h // 2) - (height // 2)
        except Exception:
            # fallback
            screen_w = win.winfo_screenwidth()
            screen_h = win.winfo_screenheight()
            x = (screen_w // 2) - (width // 2)
            y = (screen_h // 2) - (height // 2)

        win.geometry(f"+{x}+{y}")
        win.deiconify()

        # No bloqueamos: devolvemos inmediatamente y la ventana se fornea responsive
        # gracias al updater que está llamando periódicamente a root.update()
        return

    except Exception:
        # fallback si algo falla al intentar crear la ventana
        try:
            subprocess.run(["notify-send", "Mensaje remoto", msg])
        except Exception:
            print(f"[MESSAGE] {msg}")
        return

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
    parser.add_argument("--ip", required=True, help="IP address of the WebSocket server, e.g. 192.168.1.10")
    parser.add_argument("--port", required=False, type=int, default=9000, help="Port of the WebSocket server (default: 9000)")
    parser.add_argument("--id", required=True, help="Unique client id")
    parser.add_argument("--name", default="", help="Friendly name")
    args = parser.parse_args()

    # Validate port range
    if not (1 <= args.port <= 65535):
        print("Error: --port must be between 1 and 65535")
        sys.exit(1)

    # Construir la URL WebSocket (usa ws por defecto)
    server_uri = f"ws://{args.ip}:{args.port}/ws/client"

    try:
        asyncio.run(run_agent(server_uri, args.id, args.name))
    except KeyboardInterrupt:
        print("Client interrupted by user, exiting.")
    except Exception as e:
        print("Client exiting due to exception:", e)

if __name__ == "__main__":
    main()
