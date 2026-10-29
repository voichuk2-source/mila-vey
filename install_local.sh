#!/usr/bin/env bash
# Isolated local-only Mila Vey install. Does not alter web server configuration.
set -euo pipefail
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${EUID}" -ne 0 ]]; then
    exec sudo bash "$SOURCE_DIR/install_local.sh"
fi
TARGET=/opt/mila-vey
MARKER="$TARGET/.mila-managed"
[[ -f "$SOURCE_DIR/.env" ]] || { echo 'Сначала выполни python3 configure.py'; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo 'Ожидается архитектура x86_64'; exit 1; }
for unit in mila-vey.service mila-vey-ai.service; do
    if systemctl cat "$unit" >/dev/null 2>&1 && [[ ! -f "$MARKER" ]]; then
        echo "Сервис $unit уже существует. Ничего не меняю."; exit 1
    fi
done
if [[ -e "$TARGET" && ! -f "$MARKER" ]]; then
    echo "$TARGET уже существует. Не перезаписываю чужой проект."; exit 1
fi
command -v curl >/dev/null || { echo 'Не найден curl. Установка остановлена.'; exit 1; }
if ! command -v zstd >/dev/null; then
    echo 'Добавляю только пакет zstd для распаковки официального архива Ollama.'
    apt-get install -y --no-install-recommends zstd
fi
if [[ ! -f "$MARKER" ]] && id mila-vey >/dev/null 2>&1; then
    echo 'Пользователь mila-vey уже существует. Не использую чужую учётную запись.'; exit 1
fi
if ! id mila-vey >/dev/null 2>&1; then
    useradd --system --user-group --home-dir "$TARGET" --shell /usr/sbin/nologin mila-vey
fi
install -d -m 750 -o mila-vey -g mila-vey "$TARGET" "$TARGET/data" "$TARGET/runtime" "$TARGET/models"
touch "$MARKER"
# Stop only our managed services on a repeated install; never web projects.
systemctl stop mila-vey.service mila-vey-ai.service 2>/dev/null || true
install -m 640 -o root -g mila-vey "$SOURCE_DIR/business.py" "$TARGET/business.py"
install -m 640 -o root -g mila-vey "$SOURCE_DIR/bot.py" "$TARGET/bot.py"
# Preserve the configured identity and secret without printing either.
if [[ ! -f "$TARGET/.env" ]]; then
    python3 - "$SOURCE_DIR/.env" "$TARGET/.env" <<'PY'
import sys
from pathlib import Path
src, dst = map(Path, sys.argv[1:])
values = dict(line.split('=',1) for line in src.read_text().splitlines() if line and not line.startswith('#'))
values.update(DB_PATH='/opt/mila-vey/data/mila.sqlite3', OLLAMA_URL='http://127.0.0.1:11435', OLLAMA_MODEL='qwen2.5:0.5b')
with dst.open('x') as f:
    for k, v in values.items():
        f.write(k+'='+v+'\n')
PY
    chmod 600 "$TARGET/.env"
fi
if [[ ! -e "$TARGET/data/mila.sqlite3" && -e "$SOURCE_DIR/data/mila.sqlite3" ]]; then
    python3 - "$SOURCE_DIR/data/mila.sqlite3" "$TARGET/data/mila.sqlite3" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close()
src.close()
PY
    chown mila-vey:mila-vey "$TARGET/data/mila.sqlite3"
    chmod 600 "$TARGET/data/mila.sqlite3"
fi
if [[ ! -x "$TARGET/runtime/bin/ollama" ]]; then
    echo 'Скачиваю официальный Ollama в отдельный каталог. Это может занять несколько минут.'
    curl --fail --location --retry 2 --connect-timeout 20 --progress-bar \
        https://ollama.com/download/ollama-linux-amd64.tar.zst \
        -o "$TARGET/runtime/ollama-linux-amd64.tar.zst"
    tar --zstd -xf "$TARGET/runtime/ollama-linux-amd64.tar.zst" -C "$TARGET/runtime"
fi
# Dedicated loopback port. Refuse to occupy a port belonging to another service.
python3 - <<'PY'
import socket
s=socket.socket()
try:
    s.bind(('127.0.0.1',11435))
except OSError:
    raise SystemExit('Порт 11435 занят. Установка остановлена; чужой сервис не изменён.')
finally:
    s.close()
PY
cat > /etc/systemd/system/mila-vey-ai.service <<'EOF'
[Unit]
Description=Mila Vey isolated local AI
After=network-online.target
Wants=network-online.target
[Service]
User=mila-vey
Group=mila-vey
WorkingDirectory=/opt/mila-vey
ExecStart=/opt/mila-vey/runtime/bin/ollama serve
Environment=HOME=/opt/mila-vey/models
Environment=OLLAMA_HOST=127.0.0.1:11435
Environment=OLLAMA_MODELS=/opt/mila-vey/models
Environment=OLLAMA_CONTEXT_LENGTH=2048
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_NO_CLOUD=1
Environment=OLLAMA_KEEP_ALIVE=2m
Restart=on-failure
RestartSec=10
Nice=15
CPUQuota=80%
MemoryHigh=1100M
MemoryMax=1400M
TasksMax=128
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/mila-vey/models
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/mila-vey.service <<'EOF'
[Unit]
Description=Mila Vey Telegram bot
After=network-online.target mila-vey-ai.service
Wants=network-online.target mila-vey-ai.service
[Service]
User=mila-vey
Group=mila-vey
WorkingDirectory=/opt/mila-vey
EnvironmentFile=/opt/mila-vey/.env
ExecStart=/usr/bin/python3 /opt/mila-vey/bot.py
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/opt/mila-vey/data
MemoryMax=180M
UMask=0077
[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl start mila-vey-ai.service
for attempt in {1..40}; do
    if curl --fail --silent --max-time 2 http://127.0.0.1:11435/api/version >/dev/null; then break; fi
    sleep 1
done
curl --fail --silent --max-time 3 http://127.0.0.1:11435/api/version >/dev/null || {
    echo 'AI пока не запустился. Пришли вывод: sudo journalctl -u mila-vey-ai -n 25 --no-pager'; exit 1;
}
echo 'Скачиваю маленькую тестовую модель Qwen2.5 0.5B (около 398 МБ).'
runuser -u mila-vey -- env OLLAMA_HOST=127.0.0.1:11435 "$TARGET/runtime/bin/ollama" pull qwen2.5:0.5b
# Verify actual inference before claiming that free chat is available.
echo 'Проверяю реальный ответ локальной модели…'
python3 - <<'PY'
import json, urllib.request
body={'model':'qwen2.5:0.5b','messages':[{'role':'user','content':'Скажи привет на русском.'}],
      'stream':False,'options':{'num_ctx':2048,'num_thread':1,'num_predict':24}}
try:
    request=urllib.request.Request('http://127.0.0.1:11435/api/chat',data=json.dumps(body).encode(),headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=240) as response:
        answer=json.load(response)['message']['content'].strip()
    if not answer:
        raise ValueError()
    print('Ответ AI:',answer[:300])
except Exception:
    raise SystemExit('Тест AI не прошёл. Бот ещё не запущен. Пришли последние строки терминала.')
PY
systemctl enable mila-vey-ai.service mila-vey.service
systemctl start mila-vey.service
systemctl is-active --quiet mila-vey.service || { echo 'Бот не запустился.'; exit 1; }
echo 'ГОТОВО. Открой @MilaVeyChatBot и отправь /start, затем обычное сообщение.'
echo 'Продажи остаются выключены. Для админки: /admin. Реальные ответы в Telegram ещё нужно проверить.'
