import os
import requests

def send_telegram_message(message):
    token = os.getenv('TELEGRAM_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    payload = {'chat_id': chat_id, 'text': message}

    response = requests.post(url, json=payload)
    return response.text

if __name__ == '__main__':
    message = 'Hello from bot_single_run!'
    print(send_telegram_message(message))