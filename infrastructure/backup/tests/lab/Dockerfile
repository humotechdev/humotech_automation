# Изолированный Linux-стенд для проверки скриптов бэкапа: age, rclone и
# systemd — как на сервере. Docker внутри не запускается: скрипты работают с
# Docker хоста через смонтированный сокет (см. README.md рядом).
FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        age rclone systemd systemd-sysv sudo ca-certificates \
        bash coreutils util-linux procps gzip tar findutils gawk \
    && rm -rf /var/lib/apt/lists/*

# Только клиент Docker — из официального образа.
COPY --from=docker:27-cli /usr/local/bin/docker /usr/local/bin/docker

# Unit-файлы бэкапа требуют docker.service. Демон здесь чужой (хоста), поэтому
# unit-заглушка лишь проверяет, что сокет отвечает.
COPY docker.service /etc/systemd/system/docker.service
RUN chmod 644 /etc/systemd/system/docker.service \
    && systemctl enable docker.service \
    && systemctl mask systemd-logind.service getty.target console-getty.service

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
