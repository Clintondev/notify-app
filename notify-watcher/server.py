#!/usr/bin/env python3
from flask import Flask, request, jsonify
from flask_cors import CORS
from send_push import send_notification

app = Flask(__name__)
# Adiciona suporte a CORS para todas as rotas
CORS(app)

@app.route('/notify', methods=['POST'])
def notify():
    data = request.json
    app_name = data.get('app', 'Browser')
    text = data.get('text', 'Nenhuma mensagem.')
    
    full_message = f"[{app_name}] {text}"
    print(f"📡 Recebido via HTTP: {full_message}")
    
    # 👉 Chama a função para enviar ao celular
    send_notification(full_message)
    
    return jsonify({'status': 'ok'}), 200

if __name__ == '__main__':
    print("Iniciando servidor HTTP na porta 3000...")
    # Para rodar fora de um container, use 127.0.0.1
    app.run(host='127.0.0.1', port=3000)
