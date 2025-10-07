#!/usr/bin/env python3
import dbus
import time
from gi.repository import GLib
from dbus.mainloop.glib import DBusGMainLoop
from send_push import send_notification

# Variáveis para controle de duplicação
last_message_content = ""
last_message_time = 0

def notification_handler(bus, message):
    global last_message_content, last_message_time

    args = message.get_args_list()
    app_name, notif_id, icon, title, text, actions, hints, timeout = args
    
    full_message = f"[{app_name}] {title}: {text}"
    current_time = time.time()

    # Lógica de deduplicação:
    # Se a mensagem for idêntica à anterior e chegou há menos de 1 segundo, ignore.
    if full_message == last_message_content and (current_time - last_message_time) < 1.0:
        print(f"🤫 Ignorando notificação duplicada.")
        return

    # Atualiza o cache da última mensagem
    last_message_content = full_message
    last_message_time = current_time
    
    print(f"📩 Capturado: {full_message}")
    
    # 👉 Chama a função para enviar ao celular
    send_notification(full_message)

print("Iniciando watcher de notificações do sistema (com filtro de duplicatas)...")
DBusGMainLoop(set_as_default=True)
bus = dbus.SessionBus()
bus.add_match_string("eavesdrop=true,interface='org.freedesktop.Notifications',member='Notify'")
bus.add_message_filter(notification_handler)
GLib.MainLoop().run()
