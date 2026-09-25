# Заголовки, общие для всех проксируемых путей.
#
# Отдельным файлом, потому что повторять их в четырёх location'ах —
# верный способ однажды забыть один и получить редирект-петлю, которую
# видно только в Telegram WebView и только на телефоне.

proxy_http_version 1.1;
proxy_set_header Connection "";

# Host сохраняется: Django сверяет его с ALLOWED_HOSTS, и подмена на
# имя контейнера превратила бы каждый запрос в 400.
proxy_set_header Host $host;

# X-Forwarded-For ПЕРЕЗАПИСЫВАЕТСЯ, а не дописывается.
#
# `$proxy_add_x_forwarded_for` передал бы backend всё, что прислал клиент,
# плюс адрес соединения: «X-Forwarded-For: 6.6.6.6» доходил до Django
# первой записью, и DRF без NUM_PROXIES считал частоту по всей строке —
# новый заголовок на каждый запрос обходил любой предел.
#
# $remote_addr здесь — уже адрес клиента: его достал модуль realip из
# заголовка доверенного прокси (см. начало nginx.conf). Backend получает
# ровно одну запись, и TRUSTED_PROXY_COUNT=1 у него берёт именно её.
# Секрет бота и Telegram ID, пришедшие снаружи, вырезаются (пустое
# значение nginx не передаёт). Изнутри сети — передаются как есть.
# См. map $from_edge в nginx.conf.
proxy_set_header X-Bot-Token $bot_token_upstream;
proxy_set_header X-Telegram-User-Id $bot_user_upstream;

proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $remote_addr;
proxy_set_header X-Forwarded-Proto $forwarded_proto;
proxy_set_header X-Forwarded-Host $host;
