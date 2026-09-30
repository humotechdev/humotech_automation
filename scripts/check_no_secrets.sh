#!/usr/bin/env bash
# Проверка повторного попадания секретов в репозиторий.
#
# Ищет в ОТСЛЕЖИВАЕМЫХ файлах узнаваемые секреты: настоящий токен бота
# Telegram, приватные ключи, ключи AWS/age/OpenAI/GitHub. Тестовые
# заглушки отсекаются по маркерам (TEST, NOT-REAL, example, changeme,
# placeholder). Значения секретов НЕ печатаются — только файл и тип.
#
# Запуск: bash scripts/check_no_secrets.sh   (код 0 — чисто, 1 — нашёл)
# Подключено к CI (.github/workflows/ci.yml, job secrets-scan).
set -u
cd "$(git rev-parse --show-toplevel)" || exit 2

# Файлы, которым по смыслу положено содержать образцы (тесты-фикстуры с
# заведомо фиктивными значениями, примеры конфигурации).
ALLOW_RE='(\.env\.example|_example\.|/tests/|/django_tests/|test_.*\.py|check_no_secrets\.sh)'

# тип → регулярное выражение секрета
declare -A PATTERNS=(
  ["telegram-bot-token"]='[0-9]{8,10}:[A-Za-z0-9_-]{35}'
  ["private-key-block"]='-----BEGIN ([A-Z]+ )?PRIVATE KEY-----'
  ["aws-access-key"]='AKIA[0-9A-Z]{16}'
  ["age-secret-key"]='AGE-SECRET-KEY-1[0-9A-Z]{50,}'
  ["openai-key"]='sk-(proj-)?[A-Za-z0-9_-]{32,}'
  ["github-pat"]='gh[pousr]_[A-Za-z0-9]{36,}'
  ["ngrok-token"]='[0-9][A-Za-z0-9]{20,}_[A-Za-z0-9]{20,}'
)
# Маркеры заведомо фиктивных значений на той же строке.
FAKE_RE='TEST|NOT.?REAL|example|EXAMPLE|placeholder|PLACEHOLDER|changeme|change-me|dummy|fake|xxxxx|<[^>]+>'

found=0
for kind in "${!PATTERNS[@]}"; do
  # -I: пропустить бинарные. --line-number для отчёта.
  while IFS=: read -r file line _; do
    [ -z "$file" ] && continue
    printf 'ВОЗМОЖНЫЙ СЕКРЕТ [%s]: %s:%s\n' "$kind" "$file" "$line"
    found=1
  done < <(
    git grep -InE "${PATTERNS[$kind]}" -- \
      ':!*.png' ':!*.jpg' ':!*.jpeg' ':!*.pdf' ':!*.lock' ':!package-lock.json' \
    2>/dev/null \
      | grep -vE "$ALLOW_RE" \
      | grep -vE "$FAKE_RE"
  )
done

if [ "$found" -ne 0 ]; then
  echo "ОТКАЗ: найдены строки, похожие на настоящие секреты (см. выше)." >&2
  echo "Если это заведомо фиктивное значение — добавьте маркер (TEST/example) или путь в ALLOW_RE." >&2
  exit 1
fi
echo "Секретов в отслеживаемых файлах не найдено."
