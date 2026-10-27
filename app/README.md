# stand-checker

Минимальное приложение (FastAPI) для проверки стенда. Каждый эндпоинт проверяет один элемент и отвечает JSON:
`200` + `"ok": true` — работает, `503` + `error`, `error_type`, `hint` — нет.

## Эндпоинты

| Эндпоинт | Что делает | Проверяет |
|---|---|---|
| `GET /healthz` | ничего, просто отвечает | процесс жив (для liveness probe) |
| `GET /check/identity` | спрашивает metadata server, от какого сервисного аккаунта работает под | Workload Identity |
| `GET /check/db` | создаёт таблицу `stand_check`, пишет строку, читает её обратно | Auth Proxy, пароль из Secret Manager, Cloud SQL |
| `GET /check/pubsub` | публикует сообщение с уникальным `check_id` и ждёт его в подписке (до 20 с) | Pub/Sub, IAM |
| `GET /check/storage` | загружает файл, скачивает, сравнивает, удаляет | GCS, IAM |
| `GET /check/egress` | HTTP-запрос во внешний интернет | Cloud NAT (или его отсутствие) |
| `GET /check/all` | все проверки сразу | — |

Ошибки с подсказкой, куда смотреть. Например, `PermissionDenied` → проверить IAM и `/check/identity`, таймаут egress → NAT.

## Конфигурация (переменные окружения)

| Переменная | Обязательна | По умолчанию | Значение |
|---|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | да | — | ID проекта |
| `PUBSUB_TOPIC` | да | — | имя топика (короткое, не полный путь) |
| `PUBSUB_SUBSCRIPTION` | да | — | имя подписки (короткое) |
| `GCS_BUCKET` | да | — | имя бакета для файлов |
| `DB_NAME` | да | — | имя базы в Cloud SQL |
| `DB_USER` | да | — | пользователь БД |
| `DB_PASSWORD_FILE` | да | — | путь к **файлу** с паролем (туда его смонтирует Secret Manager add-on) |
| `DB_HOST` | нет | `127.0.0.1` | адрес Auth Proxy в поде |
| `DB_PORT` | нет | `5432` | порт Auth Proxy |
| `EGRESS_CHECK_URL` | нет | `https://www.google.com/generate_204` | внешний адрес для проверки выхода в интернет |

Пароль приложение читает только из файла: в коде, образе и env его нет.

Учётные данные Google приложение не получает явно: клиентские библиотеки берут их через ADC. В GKE это Workload Identity, локально — твой `gcloud auth application-default login`.

## Контракт для манифестов

- Контейнер слушает порт **8080**.
- Работает от UID **10001** (не root).
- Liveness — `/healthz`. Не вешай `/check/*` на пробы: они ходят во внешние сервисы, и сбой БД перезапускал бы под.
- Сервисному аккаунту приложения нужны права: публиковать в топик, читать (и подтверждать) сообщения подписки, создавать/читать/удалять объекты в бакете, подключаться к Cloud SQL (для прокси) и читать секрет с паролем. Какие именно роли и на каком уровне — решаешь ты на этапе 5.

## Сборка и публикация образа

Из папки `app/`. Подставь свой регион, проект и имя репозитория из Terraform.

Один раз настроить Docker на Artifact Registry:

```bash
gcloud auth configure-docker europe-southwest1-docker.pkg.dev
```

Собрать и отправить (тег меняй на каждую версию: `v1`, `v2`, ...):

```bash
docker build --provenance=false -t europe-southwest1-docker.pkg.dev/<PROJECT_ID>/<REPOSITORY>/stand-checker:v1 .
```

`--provenance=false` — один манифест на версию вместо трёх (индекс + образ + attestation), чтобы политика очистки `keep 3` считала версии предсказуемо.

```bash
docker push europe-southwest1-docker.pkg.dev/<PROJECT_ID>/<REPOSITORY>/stand-checker:v1
```

## Локальный запуск (без кластера)

Проверки Pub/Sub и Storage работают с твоими ADC. `identity` и `db` локально упадут — это ожидаемо, и заодно видно, как выглядят ошибки.

```bash
docker run --rm -p 8080:8080 -u 0 -e GOOGLE_CLOUD_PROJECT=my-experience-512317 -e PUBSUB_TOPIC=my-topic -e PUBSUB_SUBSCRIPTION=my-subscription -e GCS_BUCKET=my-experience-512317-app-files -e DB_NAME=app -e DB_USER=app -e DB_PASSWORD_FILE=/nope -e GOOGLE_APPLICATION_CREDENTIALS=/creds/adc.json -v $HOME/.config/gcloud/application_default_credentials.json:/creds/adc.json:ro europe-southwest1-docker.pkg.dev/my-experience-512317/my-repository/stand-checker:v1
```

```bash
curl -s localhost:8080/check/all
```

`-u 0` нужен только локально: файл ADC доступен на чтение лишь владельцу (root), а образ по умолчанию работает от UID 10001. В кластере так не делаем. Файл ADC монтируется только для чтения и только при локальном запуске: в образ он не попадает (`.dockerignore` исключает `*.json`).
