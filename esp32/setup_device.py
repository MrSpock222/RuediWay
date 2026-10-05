"""Local USB setup. Passwords travel to the user's ESP32, never to the host API."""
import argparse
import json
from pathlib import Path
import queue
import re
import threading
import time
import urllib.request
from urllib.parse import urlsplit


def validate(server, ssid, password, keep_wifi):
    url = urlsplit(server)
    if (url.scheme != 'http' or not url.hostname or url.path not in ('', '/') or url.query or url.fragment
            or url.username or url.password or not re.fullmatch(r'[A-Za-z0-9.:-]+', url.netloc)
            or url.hostname in {'localhost', '127.0.0.1', '0.0.0.0'}):
        raise ValueError('PC-LAN-Adresse verwenden, z. B. http://192.168.254.118:8000.')
    if not keep_wifi and not (1 <= len(ssid.encode('utf-8')) <= 32):
        raise ValueError('WLAN-Name fehlt oder ist zu lang.')
    if not keep_wifi and password and not 8 <= len(password.encode('utf-8')) <= 63:
        raise ValueError('WLAN-Passwort muss 8 bis 63 Bytes lang sein.')
    return server.rstrip('/')


def wait_event(port, wanted, timeout, report):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = port.readline(4096)
        try:
            event = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get('event') == 'error':
            raise RuntimeError(event.get('message', 'ESP32 meldet einen Fehler.'))
        if event.get('event') in {'connecting', 'wifi_connected', 'paired'}:
            report(event['message'])
        if event.get('event') == wanted:
            return event
    raise RuntimeError('Keine Bestätigung vom ESP32. Seriellen Monitor schließen und Verbindung prüfen.')


def provision(port_name, server, ssid, password, keep_wifi, report):
    import serial
    server = validate(server, ssid, password, keep_wifi)
    port = serial.Serial(port=None, baudrate=115200, timeout=.3, write_timeout=3)
    port.dtr = False
    port.rts = False
    port.port = port_name
    with port:
        port.reset_input_buffer()
        port.write(b'{"cmd":"status"}\n')
        status = wait_event(port, 'status', 10, report)
        if status.get('firmware') not in {'ruediway-xiao-1', 'ruediway-xiao-2', 'ruediway-xiao-3'} or not status.get('camera_ready'):
            raise RuntimeError('RuediWay-Firmware bzw. Kamera ist nicht bereit.')
        if keep_wifi and not status.get('ssid'):
            raise ValueError('Noch kein WLAN gespeichert. WLAN-Daten eingeben.')
        request = urllib.request.Request('http://127.0.0.1:8000/host/code', method='POST', data=b'{}',
                                         headers={'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            code = json.load(response)['code']
        command = {'cmd':'pair' if keep_wifi else 'configure', 'server':server, 'code':code}
        if not keep_wifi:
            command.update(ssid=ssid, password=password)
        port.write((json.dumps(command, ensure_ascii=True) + '\n').encode('utf-8'))
        port.flush()
        report('Warte auf WLAN und Kopplung …')
        wait_event(port, 'paired', 65, report)
        port.write(b'{"cmd":"status"}\n')
        status = wait_event(port, 'status', 10, report)
        return {key: status.get(key) for key in ('camera_ready', 'wifi_connected', 'paired', 'ip', 'sensor_pid', 'psram_bytes')}


def main():
    import tkinter as tk
    from tkinter import ttk
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='COM6')
    parser.add_argument('--server', default='http://192.168.254.118:8000')
    parser.add_argument('--status-file', type=Path)
    args = parser.parse_args()
    window = tk.Tk()
    window.title('RuediWay · ESP32 einrichten')
    window.geometry('560x450')
    frame = ttk.Frame(window, padding=22)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='XIAO ESP32-S3 Sense verbinden', font=('Segoe UI', 16, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='WLAN-Daten werden über USB auf dem ESP32 gespeichert.\nBitte ein 2,4-GHz-WLAN verwenden; die Antenne anschließen.').pack(anchor='w', pady=10)
    fields = {}
    for key, label, initial in [('port','USB-Port',args.port),('server','PC-Adresse',args.server),('ssid','WLAN-Name',''),('password','WLAN-Passwort','')]:
        row = ttk.Frame(frame); row.pack(fill='x', pady=4)
        ttk.Label(row, text=label, width=17).pack(side='left')
        variable = tk.StringVar(value=initial)
        ttk.Entry(row, textvariable=variable, show='•' if key == 'password' else '').pack(side='left', fill='x', expand=True)
        fields[key] = variable
    keep = tk.BooleanVar(value=False)
    ttk.Checkbutton(frame, text='Gespeichertes WLAN behalten – nur neu koppeln', variable=keep).pack(anchor='w', pady=10)
    message = tk.StringVar(value='Arduino Serial Monitor vor der Einrichtung schließen.')
    updates = queue.Queue()
    running = False

    def record(state, **values):
        if args.status_file:
            args.status_file.write_text(json.dumps({'state':state, **values}, ensure_ascii=False), encoding='utf-8')

    def start():
        nonlocal running
        if running:
            return
        values = {k:v.get() for k,v in fields.items()}
        keep_wifi = keep.get()
        try:
            validate(values['server'], values['ssid'], values['password'], keep_wifi)
        except ValueError as exc:
            message.set(str(exc)); return
        running = True; button.configure(state='disabled')
        record('connecting')
        def work():
            try:
                result = provision(values['port'], values['server'], values['ssid'], values['password'], keep_wifi,
                                   lambda text: updates.put(('progress', text)))
                updates.put(('complete', result))
            except Exception as exc:
                updates.put(('error', str(exc)))
            finally:
                values['password'] = ''
        threading.Thread(target=work, daemon=True).start()

    def poll():
        nonlocal running
        while not updates.empty():
            kind, payload = updates.get_nowait()
            if kind == 'progress':
                message.set(payload)
            else:
                running = False; button.configure(state='normal')
                if kind == 'complete':
                    fields['password'].set(''); keep.set(True)
                    message.set('Verbunden! Kamera-IP: ' + str(payload['ip']) + '\nAm Host sind Foto, Live-Vorschau und Seitenscanner bereit.')
                    record('complete', **payload)
                else:
                    message.set(payload); record('error', message=payload)
        window.after(150, poll)

    button = ttk.Button(frame, text='WLAN einrichten und mit Host koppeln', command=start)
    button.pack(fill='x', pady=10)
    ttk.Label(frame, textvariable=message, wraplength=510).pack(anchor='w', pady=8)
    record('waiting_for_wifi')
    window.after(150, poll)
    window.mainloop()


if __name__ == '__main__':
    main()
