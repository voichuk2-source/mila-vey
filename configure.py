#!/usr/bin/env python3
"""Configure a new private deployment without printing or committing credentials."""
import getpass
import json
import os
import secrets
import time
import urllib.request
from pathlib import Path
from bot import Telegram


def main():
    os.umask(0o077)
    root = Path(__file__).resolve().parent
    env = root / '.env'
    if env.exists():
        raise SystemExit('Настройки .env уже существуют. Ничего не изменено.')
    print('Возьми токен @MilaVeyChatBot в своём чате с @BotFather.')
    print('Вставь его здесь. При вводе символы скрыты. В ChatGPT токен не отправляй.')
    token = getpass.getpass('Токен бота: ').strip()
    if not token or '\n' in token or ':' not in token:
        raise SystemExit('Неверный формат токена. Файл не создан.')
    tg = Telegram(token)
    try:
        me = tg.call('getMe')
        if me.get('username','').lower() != 'milaveychatbot':
            raise SystemExit('Это другой бот. Настройка остановлена без изменений.')
        if tg.call('getWebhookInfo').get('url'):
            raise SystemExit('У бота уже есть webhook. Настройка остановлена без изменений.')
    except RuntimeError:
        raise SystemExit('Не удалось проверить токен через Telegram. Файл не создан.')
    claim = secrets.token_hex(12)
    expected = '/start ' + claim
    print('\nОткрой @MilaVeyChatBot в своём Telegram и отправь ОДНИМ сообщением:')
    print(expected)
    print('\nОжидаю сообщение до 5 минут. Код одноразовый; никому его не передавай.')
    offset = 0
    deadline = time.monotonic() + 300
    admin = None
    while time.monotonic() < deadline:
        try:
            updates = tg.call('getUpdates',offset=offset,timeout=20,allowed_updates=['message','pre_checkout_query'])
        except RuntimeError:
            raise SystemExit('Telegram недоступен. Настройки не сохранены. Повтори настройку позже.')
        for update in updates:
            offset = max(offset,update['update_id']+1)
            msg = update.get('message',{})
            if msg.get('chat',{}).get('type')=='private' and msg.get('text','').strip()==expected:
                admin = msg['from']['id']
                break
        if admin:
            break
    if not admin:
        raise SystemExit('Время ожидания истекло. Настройки не сохранены.')
    values = {'BOT_TOKEN':token,'ADMIN_USER_ID':str(admin),'TEST_USER_IDS':'',
        'OLLAMA_URL':'http://127.0.0.1:11434','OLLAMA_MODEL':'qwen2.5:0.5b',
        'DB_PATH':str(root/'data'/'mila.sqlite3')}
    if any('\n' in v or '\r' in v for v in values.values()):
        raise SystemExit('Недопустимые настройки. Файл не создан.')
    with env.open('x') as file:
        for key,value in values.items():
            file.write(key+'='+value+'\n')
    from bot import Store
    store = Store(values['DB_PATH'])
    store.set('offset',offset)
    store.set('sales','off')
    store.set('public','off')
    store.db.close()
    try:
        tg.send(admin,'Админка Mila Vey привязана к твоему аккаунту. Продажи выключены. AI ещё не запущен — продолжаем настройку сервера.')
    except RuntimeError:
        print('Не удалось отправить уведомление, но настройки сохранены.')
    print('\nГотово: токен сохранён в закрытом .env, администратор подтверждён.')
    print('Продажи и публичный доступ выключены. AI-модель ещё НЕ установлена и НЕ запущена.')

if __name__=='__main__':
    try:
        main()
    except (KeyboardInterrupt,EOFError):
        print('\nНастройка прервана.')
