# Сертификаты

Эта папка не обязательна при использовании `certs_helper.py` (Способ 1).
Она нужна только если вы выбираете Способ 2 — ручную загрузку PEM-файлов
в репозиторий (например, если `gu-st.ru` недоступен при старте контейнера).

## Если используете Способ 2

Скачайте сертификаты на своей машине:

```bash
curl -fsSL https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt -o certs/root.crt
curl -fsSL https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt -o certs/sub.crt
cat certs/root.crt certs/sub.crt > certs/russian_trusted_ca_bundle.crt
cat /etc/ssl/certs/ca-certificates.crt certs/russian_trusted_ca_bundle.crt > certs/ca-bundle.crt
