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

proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $forwarded_proto;
proxy_set_header X-Forwarded-Host $host;
