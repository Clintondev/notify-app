#!/usr/bin/env python3
import sys
import json
import threading
import time
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QPushButton, QDialog, QLineEdit, QComboBox, QFormLayout, QDialogButtonBox,
    QInputDialog
)

CONFIG_FILE = "notify-watcher/config.json"
IGNORE_CONFIG_FILE = "notify-watcher/ignore.json"

# --- Configuração ---
NTFY_TOPIC = ""
NOTIFICATION_METHOD = "ntfy"
IGNORED_APPS = set()


def load_ignored_apps_from_disk():
    global IGNORED_APPS
    try:
        with open(IGNORE_CONFIG_FILE, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        IGNORED_APPS = set()
        return
    except json.JSONDecodeError as exc:
        print(f"⚠️ Falha ao ler {IGNORE_CONFIG_FILE}: {exc}")
        IGNORED_APPS = set()
        return

    if isinstance(data, dict):
        apps = data.get("apps", [])
    else:
        apps = data
    IGNORED_APPS = {str(app) for app in apps}


def save_ignored_apps_to_disk():
    try:
        with open(IGNORE_CONFIG_FILE, 'w') as f:
            json.dump({"apps": sorted(IGNORED_APPS)}, f, indent=2)
    except Exception as exc:
        print(f"⚠️ Falha ao salvar {IGNORE_CONFIG_FILE}: {exc}")


def should_ignore(app_name):
    if not app_name:
        return False
    return str(app_name) in IGNORED_APPS


load_ignored_apps_from_disk()

# --- Lógica de Envio ---
def send_notification(message):
    if NOTIFICATION_METHOD == "ntfy":
        send_to_ntfy(message)
    else:
        print(f"❗️ Método de notificação desconhecido: {NOTIFICATION_METHOD}")

def send_to_ntfy(message):
    if not NTFY_TOPIC:
        print("⚠️ NTFY_TOPIC não configurado")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode('utf-8')
        )
        print(f"✅ Notificação enviada via ntfy.sh: {message[:30]}...")
    except Exception as e:
        print(f"❌ Erro ao enviar para ntfy.sh: {e}")

# --- Lógica do Servidor Web (Thread) ---
app_flask = Flask(__name__)
CORS(app_flask)

@app_flask.route('/notify', methods=['POST'])
def notify():
    data = request.json
    app_name = data.get('app', 'Browser')
    text = data.get('text', 'Nenhuma mensagem.')
    if should_ignore(app_name):
        print(f"⏭️ Ignorando notificação HTTP de {app_name}.")
        return jsonify({'status': 'ignored'}), 200
    full_message = f"[{app_name}] {text}"
    print(f"📡 Recebido via HTTP: {full_message}")
    send_notification(full_message)
    return jsonify({'status': 'ok'}), 200


@app_flask.route('/config', methods=['GET'])
def get_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            rules = json.load(f)
        if not isinstance(rules, list):
            rules = []
    except FileNotFoundError:
        rules = []
    except json.JSONDecodeError as exc:
        print(f"⚠️ Erro ao ler {CONFIG_FILE}: {exc}")
        rules = []

    ignored = sorted(IGNORED_APPS)
    return jsonify({'rules': rules, 'ignored_apps': ignored}), 200

def start_flask_server():
    print("▶️ Iniciando thread do servidor Flask na porta 3000...")
    app_flask.run(host='127.0.0.1', port=3000, use_reloader=False)


class DBusNotificationListener(threading.Thread):
    """Escuta chamadas Notify no DBus e repassa para um callback Python."""

    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback = callback
        self._ready = threading.Event()
        self._error = None
        self._loop = None
        self._glib = None
        self._bus = None

    def run(self):
        try:
            from dbus.mainloop.glib import DBusGMainLoop
            from gi.repository import GLib
            import dbus
        except Exception as exc:
            self._error = exc
            print(f"❌ Integração DBus indisponível: {exc}")
            self._ready.set()
            return

        self._glib = GLib
        DBusGMainLoop(set_as_default=True)

        try:
            self._bus = dbus.SessionBus()
            match_rule = "type='method_call',interface='org.freedesktop.Notifications',member='Notify',eavesdrop='true'"
            self._bus.add_match_string(match_rule)
            self._bus.add_message_filter(self._on_message)
        except Exception as exc:
            self._error = exc
            print(f"❌ Erro ao registrar listener DBus: {exc}")
            self._ready.set()
            return

        print("✅ Listener de DBus configurado para espionar chamadas 'Notify'.")

        self._loop = GLib.MainLoop()
        self._ready.set()
        try:
            self._loop.run()
        finally:
            if self._bus is not None:
                try:
                    self._bus.remove_message_filter(self._on_message)
                except Exception:
                    pass

    def _on_message(self, bus, message):
        try:
            member = message.get_member()
        except AttributeError:
            return
        if member != "Notify":
            return
        try:
            args = message.get_args_list()
        except Exception as exc:
            print(f"❌ Erro ao ler argumentos DBus: {exc}")
            return
        sender = None
        try:
            sender = message.get_sender()
        except Exception:
            sender = "desconhecido"
        print(f"👂 Capturado Notify via DBus de {sender}")
        # Garante 8 argumentos conforme especificação do método Notify
        if len(args) < 8:
            args += [None] * (8 - len(args))
        elif len(args) > 8:
            args = args[:8]
        try:
            app_name, notif_id, icon, title, text, actions, hints, timeout = args[:8]
            if should_ignore(app_name):
                print(f"⏭️ Ignorando Notify DBus de {app_name}.")
                return
            self.callback(
                str(app_name or "Aplicativo"),
                str(title or ""),
                str(text or ""),
                notif_id,
                icon,
                actions,
                hints,
                timeout,
            )
        except Exception as exc:
            print(f"❌ Erro ao processar mensagem DBus: {exc}")

    def wait_until_ready(self, timeout=5.0):
        self._ready.wait(timeout)
        return self._error is None and self._loop is not None

    def stop(self):
        if self._loop is None or self._glib is None:
            return

        def _quit():
            try:
                self._loop.quit()
            except Exception:
                pass
            return False

        self._glib.idle_add(_quit)
        self.join(timeout=2.0)

# --- Diálogo para Adicionar/Editar Regra ---
class RuleDialog(QDialog):
    def __init__(self, parent=None, rule=None):
        super().__init__(parent)
        self.setWindowTitle("Adicionar/Editar Regra")
        self.form_layout = QFormLayout(self)
        self.name_input = QLineEdit(self)
        self.url_input = QLineEdit(self)
        self.type_input = QComboBox(self)
        self.selector_input = QLineEdit(self)
        self.type_input.addItems(["element", "element_text"])
        self.form_layout.addRow("Nome:", self.name_input)
        self.form_layout.addRow("URL Contém:", self.url_input)
        self.form_layout.addRow("Tipo:", self.type_input)
        self.form_layout.addRow("Seletor/Texto:", self.selector_input)
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.form_layout.addRow(self.button_box)
        if rule:
            self.name_input.setText(rule.get("name", ""))
            self.url_input.setText(rule.get("url_contains", ""))
            self.type_input.setCurrentText(rule.get("type", "element"))
            self.selector_input.setText(rule.get("selector", ""))
    def get_data(self):
        return {
            "name": self.name_input.text(),
            "url_contains": self.url_input.text(),
            "type": self.type_input.currentText(),
            "selector": self.selector_input.text(),
        }

# --- Janela Principal da Aplicação ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gerenciador de Notificações")
        self.setGeometry(100, 100, 700, 500)
        self.rules = []
        self.last_message_content = ""
        self.last_message_time = 0
        self._notification_lock = threading.Lock()
        self.dbus_listener = None

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.addWidget(QLabel("Regras de Notificação Ativas:"))
        self.list_widget = QListWidget()
        main_layout.addWidget(self.list_widget)
        button_layout = QHBoxLayout()
        main_layout.addLayout(button_layout)
        btn_add = QPushButton("Adicionar")
        btn_add.clicked.connect(self.add_rule)
        btn_edit = QPushButton("Editar")
        btn_edit.clicked.connect(self.edit_rule)
        btn_remove = QPushButton("Remover")
        btn_remove.clicked.connect(self.remove_rule)
        button_layout.addWidget(btn_add)
        button_layout.addWidget(btn_edit)
        button_layout.addWidget(btn_remove)

        main_layout.addWidget(QLabel("Aplicativos Ignorados:"))
        self.ignore_list_widget = QListWidget()
        main_layout.addWidget(self.ignore_list_widget)
        ignore_button_layout = QHBoxLayout()
        main_layout.addLayout(ignore_button_layout)
        btn_ignore_add = QPushButton("Adicionar Ignorado")
        btn_ignore_add.clicked.connect(self.add_ignored_app)
        btn_ignore_remove = QPushButton("Remover Ignorado")
        btn_ignore_remove.clicked.connect(self.remove_ignored_app)
        ignore_button_layout.addWidget(btn_ignore_add)
        ignore_button_layout.addWidget(btn_ignore_remove)

        self.load_rules()
        self.load_ignore_list()
        self.setup_dbus()

    def load_ignore_list(self):
        load_ignored_apps_from_disk()
        self.refresh_ignore_list()

    def setup_dbus(self):
        print("▶️ Iniciando listener de DBus (dbus-python)...")
        self.dbus_listener = DBusNotificationListener(self.handle_dbus_notification)
        self.dbus_listener.start()
        if not self.dbus_listener.wait_until_ready():
            print("❌ Listener DBus não pôde ser iniciado.")

    def handle_dbus_notification(self, app_name, title, text, notif_id, icon, actions, hints, timeout):
        full_message = f"[{app_name}] {title}: {text}"
        current_time = time.time()

        with self._notification_lock:
            if full_message == self.last_message_content and (current_time - self.last_message_time) < 1.0:
                return
            self.last_message_content = full_message
            self.last_message_time = current_time

        print(f"📩 Capturado do sistema: {full_message}")
        send_notification(full_message)

    def refresh_rule_list(self):
        self.list_widget.clear()
        for rule in self.rules:
            display_text = f'{rule["name"]} (URL: {rule["url_contains"]}) -> {rule["type"]}: {rule["selector"]!r}'
            self.list_widget.addItem(display_text)

    def refresh_ignore_list(self):
        self.ignore_list_widget.clear()
        for app in sorted(IGNORED_APPS):
            self.ignore_list_widget.addItem(app)

    def load_rules(self):
        try:
            with open(CONFIG_FILE, 'r') as f:
                self.rules = json.load(f)
            self.refresh_rule_list()
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.rules = []
            print(f"Erro ao carregar config: {e}")

    def save_rules(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.rules, f, indent=2)
        except Exception as e:
            print(f"Erro ao salvar config: {e}")

    def add_rule(self):
        dialog = RuleDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_rule = dialog.get_data()
            self.rules.append(new_rule)
            self.save_rules()
            self.refresh_rule_list()
            print("Nova regra adicionada.")

    def edit_rule(self):
        current_row = self.list_widget.currentRow()
        if current_row == -1:
            return
        rule_to_edit = self.rules[current_row]
        dialog = RuleDialog(self, rule=rule_to_edit)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated_rule = dialog.get_data()
            self.rules[current_row] = updated_rule
            self.save_rules()
            self.refresh_rule_list()
            print(f"Regra {current_row + 1} atualizada.")

    def remove_rule(self):
        current_row = self.list_widget.currentRow()
        if current_row == -1:
            return
        del self.rules[current_row]
        self.list_widget.takeItem(current_row)
        self.save_rules()
        print(f"Regra {current_row + 1} removida.")

    def add_ignored_app(self):
        name, ok = QInputDialog.getText(self, "Adicionar aplicativo ignorado", "Nome do aplicativo:")
        if not ok:
            return
        cleaned = name.strip()
        if not cleaned:
            return
        if cleaned in IGNORED_APPS:
            print(f"ℹ️ '{cleaned}' já está na lista de ignorados.")
            return
        IGNORED_APPS.add(cleaned)
        save_ignored_apps_to_disk()
        self.refresh_ignore_list()
        print(f"Aplicativo '{cleaned}' adicionado à lista de ignorados.")

    def remove_ignored_app(self):
        item = self.ignore_list_widget.currentItem()
        if not item:
            return
        name = item.text()
        if name in IGNORED_APPS:
            IGNORED_APPS.remove(name)
            save_ignored_apps_to_disk()
            self.refresh_ignore_list()
            print(f"Aplicativo '{name}' removido da lista de ignorados.")

    def closeEvent(self, event):
        if self.dbus_listener:
            self.dbus_listener.stop()
        super().closeEvent(event)

def start_gui():
    app_qt = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app_qt.exec())

# --- Ponto de Entrada Principal ---
if __name__ == "__main__":
    print("🚀 Iniciando Aplicativo Notificador...")

    server_thread = threading.Thread(target=start_flask_server, daemon=True)
    server_thread.start()

    print("✅ Servidor está rodando em background.")
    
    start_gui()
