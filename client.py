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

async def show_message_text(msg, timeout_seconds: int = None):
    """
    Muestra un mensaje en una ventana sin bordes, moderna y centrada.
    - timeout_seconds: si es entero >0, la ventana se cerrará automáticamente tras ese número de segundos.
      Si se pulsa "Aceptar" antes, la ventana se cierra y el timer (si existe) se cancela.
    - Si no hay tkinter / no se puede crear GUI, intenta usar notify-send con timeout (si aplica) o hace un print.
    """
    try:
        import tkinter as tk
        from screeninfo import get_monitors
    except Exception:
        # fallback a notify-send (Linux) con timeout si tenemos timeout_seconds
        try:
            if timeout_seconds and isinstance(timeout_seconds, int) and timeout_seconds > 0:
                # notify-send -t expects milliseconds
                subprocess.run(["notify-send", "Mensaje remoto", msg, "-t", str(int(timeout_seconds * 1000))])
            else:
                subprocess.run(["notify-send", "Mensaje remoto", msg])
        except Exception:
            print(f"[MESSAGE] {msg}")
        return

    # Si no estamos en el hilo principal, hacemos fallback (notify-send / print)
    if threading.current_thread() is not threading.main_thread():
        try:
            if timeout_seconds and isinstance(timeout_seconds, int) and timeout_seconds > 0:
                subprocess.run(["notify-send", "Mensaje remoto", msg, "-t", str(int(timeout_seconds * 1000))])
            else:
                subprocess.run(["notify-send", "Mensaje remoto", msg])
        except Exception:
            print(f"[MESSAGE] {msg}")
        return

    # Obtener loop de asyncio (debe existir)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            if timeout_seconds and isinstance(timeout_seconds, int) and timeout_seconds > 0:
                subprocess.run(["notify-send", "Mensaje remoto", msg, "-t", str(int(timeout_seconds * 1000))])
            else:
                subprocess.run(["notify-send", "Mensaje remoto", msg])
        except Exception:
            print(f"[MESSAGE] {msg}")
        return

    # Inicializar root una sola vez (se reutiliza)
    if not hasattr(show_message_text, "_tk_root") or show_message_text._tk_root is None:
        root = tk.Tk()
        root.withdraw()
        show_message_text._tk_root = root
        show_message_text._tk_update_interval = 0.02

        def _tk_updater():
            root_ref = getattr(show_message_text, "_tk_root", None)
            if root_ref is None:
                return
            try:
                root_ref.update()
            except tk.TclError:
                show_message_text._tk_root = None
                return
            try:
                loop.call_later(show_message_text._tk_update_interval, _tk_updater)
            except Exception:
                show_message_text._tk_root = None

        loop.call_soon(_tk_updater)
    else:
        root = show_message_text._tk_root

    # Crear la ventana Toplevel
    try:
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        win.configure(bg="#1E1E1E")

        frame = tk.Frame(win, bg="#1E1E1E")
        frame.pack(padx=28, pady=22)

        header_frame = tk.Frame(frame, bg="#1E1E1E")
        header_frame.pack(anchor="center")

        try:
            icon_label = tk.Label(header_frame, text="📩", font=("Noto Color Emoji", 22), bg="#1E1E1E", fg="#EAEAEA")
        except Exception:
            icon_label = tk.Label(header_frame, text="📩", bg="#1E1E1E", fg="#EAEAEA")
        icon_label.pack(pady=(0, 8))

        try:
            title_font = ("Segoe UI", 12, "bold")
        except Exception:
            title_font = None
        title_label = tk.Label(header_frame, text="Tienes un mensaje de dirección", font=title_font, bg="#1E1E1E", fg="#FFFFFF")
        title_label.pack(pady=(0, 10))

        try:
            msg_font = ("Segoe UI", 10)
        except Exception:
            msg_font = None
        msg_label = tk.Label(frame, text=msg, fg="#DCDCDC", bg="#1E1E1E", font=msg_font, justify="center", wraplength=420)
        msg_label.pack(pady=(0, 18))

        # Control para el timer de autodestrucción
        timer_handle = {"handle": None}

        def _close():
            # cancelar timer si existe
            try:
                if timer_handle["handle"] is not None:
                    try:
                        timer_handle["handle"].cancel()
                    except Exception:
                        pass
                    timer_handle["handle"] = None
            except Exception:
                pass
            try:
                # destruir la ventana Toplevel
                win.destroy()
            except Exception:
                try:
                    win.withdraw()
                except Exception:
                    pass

        btn = tk.Button(frame, text="Aceptar", command=_close,
                        bg="#2D2D2D", fg="#FFFFFF", activebackground="#3C3C3C",
                        activeforeground="#FFFFFF", relief="flat",
                        font=("Segoe UI", 10, "bold") if title_font else None,
                        padx=20, pady=6, borderwidth=0)
        btn.pack(pady=(0, 6))
        try:
            btn.configure(cursor="hand2")
        except Exception:
            pass

        # Centrar en pantalla principal
        win.update_idletasks()
        width = win.winfo_reqwidth()
        height = win.winfo_reqheight()

        try:
            monitor = get_monitors()[0]
            screen_x = monitor.x
            screen_y = monitor.y
            screen_w = monitor.width
            screen_h = monitor.height
            x = screen_x + (screen_w // 2) - (width // 2)
            y = screen_y + (screen_h // 2) - (height // 2)
        except Exception:
            screen_w = win.winfo_screenwidth()
            screen_h = win.winfo_screenheight()
            x = (screen_w // 2) - (width // 2)
            y = (screen_h // 2) - (height // 2)

        win.geometry(f"+{x}+{y}")
        win.deiconify()

        # Si timeout_seconds está definido, programar cierre automático
        if timeout_seconds and isinstance(timeout_seconds, int) and timeout_seconds > 0:
            try:
                # schedule cancelable callback via asyncio loop
                h = loop.call_later(timeout_seconds, _close)
                timer_handle["handle"] = h
            except Exception:
                # fallback: usar threading.Timer
                try:
                    import threading as _th
                    t = _th.Timer(timeout_seconds, _close)
                    t.daemon = True
                    t.start()
                    timer_handle["handle"] = t
                except Exception:
                    pass

        return

    except Exception:
        # fallback
        try:
            if timeout_seconds and isinstance(timeout_seconds, int) and timeout_seconds > 0:
                subprocess.run(["notify-send", "Mensaje remoto", msg, "-t", str(int(timeout_seconds * 1000))])
            else:
                subprocess.run(["notify-send", "Mensaje remoto", msg])
        except Exception:
            print(f"[MESSAGE] {msg}")
        return

async def show_hidden_preview(brief_text="Mensaje oculto", full_message=""):
    """
    Mini-ventana para 'mensaje oculto' con diseño más grande y centrado.
    Al pulsar "Ver" se cierra la preview y se abre la ventana completa (show_message_text).
    """
    try:
        import tkinter as tk
        from screeninfo import get_monitors
    except Exception:
        # fallback simple: notificar por consola / notify
        try:
            subprocess.run(["notify-send", "Mensaje oculto", "Pulsa 'Ver' para mostrar el contenido"])
        except Exception:
            print("[HIDDEN MESSAGE AVAILABLE]")
        return

    # si no estamos en el hilo principal, fallback
    if threading.current_thread() is not threading.main_thread():
        try:
            subprocess.run(["notify-send", "Mensaje oculto", "Pulsa 'Ver' para mostrar el contenido"])
        except Exception:
            print("[HIDDEN MESSAGE AVAILABLE]")
        return

    # obtener loop de asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            subprocess.run(["notify-send", "Mensaje oculto", "Pulsa 'Ver' para mostrar el contenido"])
        except Exception:
            print("[HIDDEN MESSAGE AVAILABLE]")
        return

    # inicializar root si no existe (reusar el root de show_message_text si fue creado)
    if not hasattr(show_message_text, "_tk_root") or show_message_text._tk_root is None:
        root = tk.Tk()
        root.withdraw()
        show_message_text._tk_root = root
        show_message_text._tk_update_interval = 0.02

        def _tk_updater():
            root_ref = getattr(show_message_text, "_tk_root", None)
            if root_ref is None:
                return
            try:
                root_ref.update()
            except Exception:
                show_message_text._tk_root = None
                return
            try:
                loop.call_later(show_message_text._tk_update_interval, _tk_updater)
            except Exception:
                show_message_text._tk_root = None

        loop.call_soon(_tk_updater)

    root = show_message_text._tk_root

    try:
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        win.configure(bg="#1E1E1E")

        # contenedor con padding más grande para sensación "similar" a la ventana principal
        frame = tk.Frame(win, bg="#1E1E1E")
        frame.pack(padx=28, pady=20)

        # Icono grande
        try:
            icon_label = tk.Label(frame, text="🔒", font=("Noto Color Emoji", 36), bg="#1E1E1E", fg="#FFFFFF")
        except Exception:
            icon_label = tk.Label(frame, text="🔒", bg="#1E1E1E", fg="#FFFFFF")
        icon_label.pack(pady=(0, 8))

        # Título centrado (más grande)
        try:
            title_font = ("Segoe UI", 13, "bold")
        except Exception:
            title_font = None
        title_label = tk.Label(frame, text=brief_text, font=title_font, bg="#1E1E1E", fg="#FFFFFF", justify="center")
        title_label.pack(pady=(0, 6))

        # Texto descriptivo centrado (un poco más grande y con wrap)
        try:
            desc_font = ("Segoe UI", 11)
        except Exception:
            desc_font = None
        desc_label = tk.Label(frame, text="Contenido oculto — pulsa Ver para mostrarlo", font=desc_font,
                              bg="#1E1E1E", fg="#DCDCDC", wraplength=520, justify="center")
        desc_label.pack(pady=(0, 12))

        # Botones en centro
        btn_frame = tk.Frame(frame, bg="#1E1E1E")
        btn_frame.pack(pady=(0, 4))

        # Handler para el botón Ver: cerrar preview y abrir la ventana completa
        def on_ver():
            try:
                win.destroy()
            except Exception:
                try:
                    win.withdraw()
                except Exception:
                    pass
            # programar show_message_text en el loop asincrónico
            try:
                # si el loop está corriendo en este hilo, crear tarea
                loop.create_task(show_message_text(full_message))
            except Exception:
                # fallback: usar ensure_future
                try:
                    asyncio.ensure_future(show_message_text(full_message))
                except Exception:
                    # último recurso: imprimir
                    print("[HIDDEN MESSAGE REVEALED]", full_message)

        def on_close():
            try:
                win.destroy()
            except Exception:
                try:
                    win.withdraw()
                except Exception:
                    pass

        # Botones estilizados (más grandes)
        btn_ver = tk.Button(btn_frame, text="Ver", command=on_ver,
                            bg="#2D2D2D", fg="#FFFFFF", activebackground="#3C3C3C",
                            relief="flat", padx=18, pady=8, borderwidth=0)
        btn_cerrar = tk.Button(btn_frame, text="Cerrar", command=on_close,
                               bg="#2D2D2D", fg="#FFFFFF", activebackground="#3C3C3C",
                               relief="flat", padx=12, pady=8, borderwidth=0)
        btn_ver.pack(side="left", padx=(0, 10))
        btn_cerrar.pack(side="left")

        # centrar preview en pantalla principal
        win.update_idletasks()
        width = win.winfo_reqwidth()
        height = win.winfo_reqheight()
        try:
            monitor = get_monitors()[0]
            screen_x = monitor.x; screen_y = monitor.y; screen_w = monitor.width; screen_h = monitor.height
            x = screen_x + (screen_w // 2) - (width // 2)
            y = screen_y + (screen_h // 2) - (height // 2)
        except Exception:
            screen_w = win.winfo_screenwidth(); screen_h = win.winfo_screenheight()
            x = (screen_w // 2) - (width // 2); y = (screen_h // 2) - (height // 2)

        win.geometry(f"+{x}+{y}")
        win.deiconify()
        return

    except Exception:
        try:
            subprocess.run(["notify-send", "Mensaje oculto", "Pulsa 'Ver' para mostrar el contenido"])
        except Exception:
            print("[HIDDEN MESSAGE AVAILABLE]")
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
                            # Nuevos campos esperados: message_type (fixed|temporary|hidden), timeout_seconds (int)
                            msg_text = j.get("message", "")
                            msg_type = (j.get("message_type") or "fixed").lower()
                            timeout_seconds = None
                            try:
                                if j.get("timeout_seconds") is not None:
                                    timeout_seconds = int(j.get("timeout_seconds"))
                            except Exception:
                                timeout_seconds = None

                            if msg_type == "fixed":
                                await show_message_text(msg_text)
                            elif msg_type == "temporary":
                                # si no se especifica timeout, usar 5s por defecto
                                if not timeout_seconds or timeout_seconds <= 0:
                                    timeout_seconds = 5
                                await show_message_text(msg_text, timeout_seconds=timeout_seconds)
                            elif msg_type == "hidden":
                                # mostrar preview con botón "Ver" para revelar contenido
                                # el preview no muestra el contenido hasta que se pulse "Ver"
                                await show_hidden_preview("Mensaje oculto recibido", msg_text)
                            else:
                                # fallback al comportamiento original
                                await show_message_text(msg_text)
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
                        elif mtype == "open_url":
                            url = j.get("url")
                            if not url:
                                print("open_url recibido sin url")
                            else:
                                try:
                                    import webbrowser
                                    webbrowser.open(url)
                                    print(f"Opened URL: {url}")
                                except Exception as e:
                                    print(f"Failed to open URL {url}: {e}")
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
