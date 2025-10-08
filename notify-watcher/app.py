#!/usr/bin/env python3
import sys
import json
import threading
import time
import base64
import requests
from flask import Flask, request, jsonify
from flask_cors import CORS
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QPushButton, QDialog, QLineEdit, QComboBox, QFormLayout, QDialogButtonBox,
    QInputDialog, QPlainTextEdit, QSpinBox
)
from urllib.parse import urlparse

CONFIG_FILE = "notify-watcher/config.json"
IGNORE_CONFIG_FILE = "notify-watcher/ignore.json"
PENDING_RULE_FILE = "notify-watcher/pending_rule.json"
RULES_SCHEMA_VERSION = 2

# --- Configuração ---
def _load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

_config = _load_config()
NTFY_TOPIC = _config.get("ntfy_topic", "gemini-notify-r2d2-ax7b9")
NOTIFICATION_METHOD = "ntfy"
IGNORED_APPS = set()

SUPPORTED_RULE_TYPES = {"element", "element_text"}
SUPPORTED_CONDITIONS = {
    "element",
    "element_text",
    "text_equals",
    "text_differs",
    "text_contains",
    "text_not_contains",
    "text_length_gt",
    "text_length_lt",
}
TEXT_MATCH_CONDITIONS = {
    "text_equals",
    "text_differs",
    "text_contains",
    "text_not_contains",
}
TEXT_LENGTH_CONDITIONS = {"text_length_gt", "text_length_lt"}
DEFAULT_CONDITION_BY_TYPE = {
    "element": "element",
    "element_text": "element_text",
}
DEFAULT_RULE_NAME = "Regra"


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


def clean_string(value):
    if value is None:
        return ""
    return str(value).strip()


def read_pending_rule():
    try:
        with open(PENDING_RULE_FILE, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        print(f"⚠️ Falha ao ler {PENDING_RULE_FILE}: {exc}")
        return None
    if not isinstance(data, dict) or not data:
        return None
    return data


def write_pending_rule(rule_data):
    try:
        with open(PENDING_RULE_FILE, 'w') as f:
            json.dump(rule_data or {}, f, indent=2)
    except Exception as exc:
        print(f"⚠️ Falha ao salvar {PENDING_RULE_FILE}: {exc}")


def clear_pending_rule():
    write_pending_rule({})


def sanitize_rule_payload(payload, *, default_source="extension"):
    if not isinstance(payload, dict):
        return None

    name = clean_string(payload.get("name"))
    page_url = clean_string(payload.get("page_url"))
    url_contains = clean_string(payload.get("url_contains"))
    if not url_contains and page_url:
        try:
            parsed = urlparse(page_url)
            url_contains = parsed.netloc or parsed.path or page_url
        except Exception:
            url_contains = page_url

    rule_type = clean_string(payload.get("type")).lower() or "element"
    if rule_type not in SUPPORTED_RULE_TYPES:
        rule_type = "element"

    raw_selector = payload.get("selector")
    css_selector = clean_string(
        payload.get("css_selector")
        or payload.get("cssPath")
        or payload.get("css_path")
        or raw_selector
    )
    text_snapshot = clean_string(
        payload.get("text_snapshot")
        or payload.get("text")
        or payload.get("captured_text")
    )

    condition = clean_string(payload.get("condition")).lower()
    if condition not in SUPPORTED_CONDITIONS:
        condition = DEFAULT_CONDITION_BY_TYPE.get(rule_type, "element")

    if rule_type == "element":
        selector_value = css_selector
        if not selector_value:
            selector_value = clean_string(raw_selector)
        if not selector_value:
            return None
    else:  # element_text
        selector_value = clean_string(raw_selector)
        if not selector_value and text_snapshot:
            selector_value = text_snapshot
        if not selector_value:
            return None

    baseline_text = clean_string(payload.get("baseline_text"))
    if not baseline_text and condition in TEXT_MATCH_CONDITIONS:
        baseline_text = text_snapshot

    length_threshold = payload.get("length_threshold")
    if length_threshold is None and condition in TEXT_LENGTH_CONDITIONS:
        try:
            length_threshold = int(payload.get("baseline_length") or len(text_snapshot))
        except Exception:
            length_threshold = len(text_snapshot)
    if condition not in TEXT_LENGTH_CONDITIONS:
        length_threshold = None

    sanitized = {
        "name": name or (text_snapshot or DEFAULT_RULE_NAME),
        "url_contains": url_contains,
        "type": rule_type,
        "selector": selector_value,
        "css_selector": css_selector if rule_type == "element" else "",
        "condition": condition,
        "baseline_text": baseline_text if baseline_text else "",
        "text_snapshot": text_snapshot if text_snapshot else "",
        "length_threshold": length_threshold,
        "page_url": page_url,
        "source": clean_string(payload.get("source")) or default_source,
        "captured_at": payload.get("captured_at") or time.time(),
    }

    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        sanitized["metadata"] = metadata

    return sanitized


load_ignored_apps_from_disk()

# --- Lógica de Envio ---
def send_notification(message, screenshot=None, screenshot_title=None, screenshot_mime='image/png'):
    if NOTIFICATION_METHOD == "ntfy":
        send_to_ntfy(message, screenshot=screenshot, screenshot_title=screenshot_title, screenshot_mime=screenshot_mime)
    else:
        print(f"❗️ Método de notificação desconhecido: {NOTIFICATION_METHOD}")

def send_to_ntfy(message, screenshot=None, screenshot_title=None, screenshot_mime='image/png'):
    if not NTFY_TOPIC:
        print("⚠️ NTFY_TOPIC não configurado")
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode('utf-8')
        )
        print(f"✅ Notificação enviada via ntfy.sh: {message[:30]}...")
        if screenshot:
            extension = 'png'
            if screenshot_mime.endswith('/jpeg') or screenshot_mime.endswith('/jpg'):
                extension = 'jpg'
            filename = f"screenshot-{int(time.time() * 1000)}.{extension}"
            headers = {}
            if screenshot_title:
                headers['Title'] = screenshot_title[:120]
            requests.post(
                f"https://ntfy.sh/{NTFY_TOPIC}",
                files={'file': (filename, screenshot, screenshot_mime)},
                headers=headers
            )
            print("✅ Screenshot enviada via ntfy.sh")
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
    screenshot_b64 = data.get('screenshot')
    screenshot_bytes = None
    screenshot_mime = 'image/png'
    if screenshot_b64:
        try:
            if screenshot_b64.startswith('data:image'):
                header, screenshot_b64 = screenshot_b64.split(',', 1)
                try:
                    screenshot_mime = header.split(';')[0].split(':', 1)[1] or screenshot_mime
                except (IndexError, ValueError):
                    screenshot_mime = 'image/png'
            screenshot_bytes = base64.b64decode(screenshot_b64)
        except Exception as exc:
            screenshot_bytes = None
            print(f"⚠️ Falha ao decodificar screenshot: {exc}")
    if should_ignore(app_name):
        print(f"⏭️ Ignorando notificação HTTP de {app_name}.")
        return jsonify({'status': 'ignored'}), 200
    full_message = f"[{app_name}] {text}"
    print(f"📡 Recebido via HTTP: {full_message}")
    screenshot_title = data.get('rule', {}).get('name') if isinstance(data.get('rule'), dict) else None
    if screenshot_title:
        screenshot_title = f"{screenshot_title} – captura"
    send_notification(full_message, screenshot=screenshot_bytes, screenshot_title=screenshot_title, screenshot_mime=screenshot_mime)
    return jsonify({'status': 'ok'}), 200


@app_flask.route('/config', methods=['GET'])
def get_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            raw_data = json.load(f)
        if isinstance(raw_data, dict):
            version = raw_data.get("version", 1)
            rules = raw_data.get("rules", [])
        else:
            version = 1
            rules = raw_data if isinstance(raw_data, list) else []
    except FileNotFoundError:
        version = RULES_SCHEMA_VERSION
        rules = []
    except json.JSONDecodeError as exc:
        print(f"⚠️ Erro ao ler {CONFIG_FILE}: {exc}")
        version = RULES_SCHEMA_VERSION
        rules = []

    ignored = sorted(IGNORED_APPS)
    pending_rule = read_pending_rule()
    return jsonify({
        'version': version,
        'rules': rules,
        'ignored_apps': ignored,
        'pending_rule': pending_rule
    }), 200


@app_flask.route('/pending_rule', methods=['POST'])
def set_pending_rule_route():
    payload = request.get_json(silent=True) or {}
    sanitized = sanitize_rule_payload(payload)
    if not sanitized:
        return jsonify({'status': 'error', 'reason': 'invalid_rule'}), 400
    sanitized['status'] = 'pending'
    if 'created_at' not in sanitized:
        sanitized['created_at'] = sanitized.get('captured_at') or time.time()
    write_pending_rule(sanitized)
    print(f"📝 Nova regra pendente recebida: {sanitized.get('name')}")
    return jsonify({'status': 'ok'}), 200


@app_flask.route('/pending_rule', methods=['DELETE'])
def clear_pending_rule_route():
    clear_pending_rule()
    print("🗑️ Regra pendente descartada.")
    return jsonify({'status': 'ok'}), 200

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
        self.condition_input = QComboBox(self)
        self.baseline_input = QPlainTextEdit(self)
        self.baseline_input.setPlaceholderText("Texto base para comparação (opcional)")
        self.baseline_input.setFixedHeight(60)
        self.length_threshold_input = QSpinBox(self)
        self.length_threshold_input.setRange(0, 1_000_000)
        self.length_threshold_input.setValue(0)
        self.length_threshold_input.setEnabled(False)

        self.type_input.addItems(["element", "element_text"])
        self.condition_input.addItems([
            "element",
            "element_text",
            "text_equals",
            "text_differs",
            "text_contains",
            "text_not_contains",
            "text_length_gt",
            "text_length_lt",
        ])

        self.form_layout.addRow("Nome:", self.name_input)
        self.form_layout.addRow("URL Contém:", self.url_input)
        self.form_layout.addRow("Tipo:", self.type_input)
        self.form_layout.addRow("Seletor/Texto:", self.selector_input)
        self.form_layout.addRow("Condição:", self.condition_input)
        self.form_layout.addRow("Texto Base:", self.baseline_input)
        self.form_layout.addRow("Limite de Tamanho:", self.length_threshold_input)

        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        self.form_layout.addRow(self.button_box)

        self.css_selector = ""
        self.snapshot_text = ""
        self.page_url = ""
        self.source = "manual"
        self.captured_at = None
        self.metadata = {}

        self.condition_input.currentTextChanged.connect(self._update_condition_fields)
        self.type_input.currentTextChanged.connect(self._on_type_changed)

        if rule:
            self.name_input.setText(rule.get("name", ""))
            self.url_input.setText(rule.get("url_contains", ""))
            self.type_input.setCurrentText(rule.get("type", "element"))
            self.selector_input.setText(rule.get("selector", ""))
            self.condition_input.setCurrentText(rule.get("condition", rule.get("type", "element")))
            baseline_text = rule.get("baseline_text", "")
            self.baseline_input.setPlainText(baseline_text)
            length_threshold = rule.get("length_threshold")
            if isinstance(length_threshold, (int, float)):
                self.length_threshold_input.setValue(int(length_threshold))
                self.length_threshold_input.setEnabled(True)
            self.css_selector = rule.get("css_selector", "")
            self.snapshot_text = rule.get("text_snapshot", baseline_text)
            self.page_url = rule.get("page_url", "")
            self.source = rule.get("source", "extension")
            self.captured_at = rule.get("captured_at")
            metadata = rule.get("metadata")
            if isinstance(metadata, dict):
                self.metadata = dict(metadata)
        self._update_condition_fields()

    def _on_type_changed(self, new_type):
        if new_type == "element" and self.condition_input.currentText() == "element_text":
            self.condition_input.setCurrentText("element")
        elif new_type == "element_text" and self.condition_input.currentText() == "element":
            self.condition_input.setCurrentText("element_text")
        self._update_condition_fields()

    def _update_condition_fields(self, *_):
        condition = self.condition_input.currentText()
        enable_text = condition in TEXT_MATCH_CONDITIONS or condition == "element_text"
        enable_length = condition in TEXT_LENGTH_CONDITIONS
        self.baseline_input.setEnabled(enable_text)
        self.length_threshold_input.setEnabled(enable_length)
        if not enable_length:
            self.length_threshold_input.setValue(
                self.length_threshold_input.value() if enable_length else 0
            )
        elif enable_length and self.length_threshold_input.value() == 0:
            base_length = len(self.snapshot_text or self.baseline_input.toPlainText().strip())
            if base_length > 0:
                self.length_threshold_input.setValue(base_length)

    def get_data(self):
        baseline_text = self.baseline_input.toPlainText().strip()
        condition = self.condition_input.currentText()
        length_threshold = self.length_threshold_input.value() if self.length_threshold_input.isEnabled() else None
        rule_type = self.type_input.currentText()
        result = {
            "name": self.name_input.text(),
            "url_contains": self.url_input.text(),
            "type": rule_type,
            "selector": self.selector_input.text(),
            "condition": condition,
            "baseline_text": baseline_text,
            "text_snapshot": self.snapshot_text or baseline_text,
            "length_threshold": length_threshold,
            "css_selector": self.selector_input.text() if rule_type == "element" else self.css_selector,
        }
        if self.page_url:
            result["page_url"] = self.page_url
        if self.source:
            result["source"] = self.source
        if self.captured_at is not None:
            result["captured_at"] = self.captured_at
        if self.metadata:
            result["metadata"] = self.metadata
        if length_threshold is None:
            result.pop("length_threshold", None)
        return result

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
        self.pending_rule = None
        self.pending_timer = None

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

        main_layout.addWidget(QLabel("Regra Pendente da Extensão:"))
        self.pending_label = QLabel("Nenhuma regra pendente.")
        self.pending_label.setWordWrap(True)
        main_layout.addWidget(self.pending_label)
        pending_button_layout = QHBoxLayout()
        main_layout.addLayout(pending_button_layout)
        self.btn_apply_pending = QPushButton("Aplicar Regra Pendente")
        self.btn_apply_pending.clicked.connect(self.apply_pending_rule)
        self.btn_discard_pending = QPushButton("Descartar Regra Pendente")
        self.btn_discard_pending.clicked.connect(self.discard_pending_rule)
        pending_button_layout.addWidget(self.btn_apply_pending)
        pending_button_layout.addWidget(self.btn_discard_pending)
        self.btn_apply_pending.setEnabled(False)
        self.btn_discard_pending.setEnabled(False)

        self.load_rules()
        self.load_ignore_list()
        self.setup_dbus()
        self.pending_timer = QTimer(self)
        self.pending_timer.setInterval(2000)
        self.pending_timer.timeout.connect(self.check_pending_rule)
        self.check_pending_rule()
        self.pending_timer.start()

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
            name = rule.get("name", "Sem nome")
            url_contains = rule.get("url_contains", "")
            rule_type = rule.get("type", "element")
            selector = rule.get("selector") or ""
            condition = rule.get("condition", rule_type)
            display_text = f'{name} (URL: {url_contains}) -> {condition}: {selector!r}'
            self.list_widget.addItem(display_text)

    def refresh_ignore_list(self):
        self.ignore_list_widget.clear()
        for app in sorted(IGNORED_APPS):
            self.ignore_list_widget.addItem(app)

    def _format_pending_rule_summary(self, rule):
        name = rule.get("name", "Sem nome")
        condition = rule.get("condition", rule.get("type", "element"))
        selector = rule.get("selector") or rule.get("css_selector") or ""
        snippet = rule.get("text_snapshot") or rule.get("baseline_text") or ""
        url_info = rule.get("url_contains") or rule.get("page_url") or ""

        def shorten(text, limit=80):
            text = str(text)
            return text if len(text) <= limit else text[: limit - 1] + "…"

        parts = [
            f"{name}",
            f"condição: {condition}",
        ]
        if selector:
            parts.append(f"alvo: {shorten(selector)}")
        if snippet:
            parts.append(f"texto: {shorten(snippet)}")
        if url_info:
            parts.append(f"url: {shorten(url_info)}")
        return "Regra pendente → " + " | ".join(parts)

    def _normalize_rule(self, rule):
        if not isinstance(rule, dict):
            return {}
        condition = rule.get("condition") or rule.get("type", "element")
        rule["condition"] = condition
        if condition not in TEXT_MATCH_CONDITIONS and condition != "element_text":
            rule.pop("baseline_text", None)
        if condition not in TEXT_LENGTH_CONDITIONS:
            rule.pop("length_threshold", None)
        return rule

    def check_pending_rule(self):
        pending = read_pending_rule()
        if not pending:
            if self.pending_rule:
                self.pending_rule = None
                self.pending_label.setText("Nenhuma regra pendente.")
                self.btn_apply_pending.setEnabled(False)
                self.btn_discard_pending.setEnabled(False)
            return
        if self.pending_rule != pending:
            self.pending_rule = pending
            self.pending_label.setText(self._format_pending_rule_summary(pending))
        self.btn_apply_pending.setEnabled(True)
        self.btn_discard_pending.setEnabled(True)

    def load_rules(self):
        try:
            with open(CONFIG_FILE, 'r') as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.rules = data.get("rules", [])
            else:
                self.rules = data if isinstance(data, list) else []
            self.refresh_rule_list()
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.rules = []
            print(f"Erro ao carregar config: {e}")

    def save_rules(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump({
                    "version": RULES_SCHEMA_VERSION,
                    "rules": self.rules
                }, f, indent=2)
        except Exception as e:
            print(f"Erro ao salvar config: {e}")

    def apply_pending_rule(self):
        pending = self.pending_rule or read_pending_rule()
        if not pending:
            return
        dialog = RuleDialog(self, rule=pending)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_rule = self._normalize_rule(dialog.get_data())
            self.rules.append(new_rule)
            self.save_rules()
            self.refresh_rule_list()
            clear_pending_rule()
            self.pending_rule = None
            self.pending_label.setText("Nenhuma regra pendente.")
            self.btn_apply_pending.setEnabled(False)
            self.btn_discard_pending.setEnabled(False)
            print("✅ Regra pendente aplicada.")
        else:
            print("ℹ️ Regra pendente mantida para revisão posterior.")

    def discard_pending_rule(self):
        clear_pending_rule()
        self.pending_rule = None
        self.pending_label.setText("Nenhuma regra pendente.")
        self.btn_apply_pending.setEnabled(False)
        self.btn_discard_pending.setEnabled(False)
        print("🗑️ Regra pendente descartada pelo usuário.")

    def add_rule(self):
        dialog = RuleDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_rule = self._normalize_rule(dialog.get_data())
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
            updated_rule = self._normalize_rule(dialog.get_data())
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
        if self.pending_timer:
            self.pending_timer.stop()
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
