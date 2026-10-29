#!/usr/bin/env bash
# Update only our bot code. Preserve secrets, memory, payments, AI and web projects.
set -euo pipefail
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${EUID}" -ne 0 ]]; then
    exec sudo bash "$SOURCE_DIR/update_bot.sh"
fi
TARGET=/opt/mila-vey
[[ -f "$TARGET/.mila-managed" && -f "$TARGET/.env" ]] || {
    echo 'Управляемая установка Mila Vey не найдена. Ничего не изменено.'; exit 1;
}
/usr/bin/python3 - "$SOURCE_DIR/bot.py" <<'PY'
import sys
from pathlib import Path
compile(Path(sys.argv[1]).read_text(),sys.argv[1],'exec')
PY
install -m 640 -o root -g mila-vey "$SOURCE_DIR/business.py" "$TARGET/business.py"
install -m 640 -o root -g mila-vey "$SOURCE_DIR/bot.py" "$TARGET/bot.py.new"
mv -f "$TARGET/bot.py.new" "$TARGET/bot.py"
systemctl restart mila-vey.service
systemctl is-active mila-vey.service
echo 'Код бота обновлён. Настройки, память, фотографии и платежи сохранены.'
echo 'Проверь /start, затем отправь обычный текст: Hey Mila, how are you?'
