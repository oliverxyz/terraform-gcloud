"""Stand checker: проверяет каждый элемент стенда отдельным эндпоинтом.

Вся конфигурация берётся из переменных окружения (см. README.md).
Каждая проверка возвращает JSON: ok, component, details или error + hint.
"""

import os
import time
import uuid

import httpx
import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from google.api_core import exceptions as gexc
from google.cloud import pubsub_v1, storage

app = FastAPI(title="stand-checker")

METADATA_URL = "http://metadata.google.internal/computeMetadata/v1"


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise ConfigError(f"не задана переменная окружения {name}")
    return value


class ConfigError(Exception):
    pass


def ok(component: str, **details) -> dict:
    return {"ok": True, "component": component, "details": details}


def fail(component: str, exc: Exception, hint: str) -> dict:
    return {
        "ok": False,
        "component": component,
        "error_type": type(exc).__name__,
        "error": str(exc)[:500],
        "hint": hint,
    }


def google_hint(exc: Exception) -> str:
    """Подсказка по типичным ошибкам клиентских библиотек Google."""
    if isinstance(exc, ConfigError):
        return "Проверь env в манифесте Deployment."
    if isinstance(exc, gexc.PermissionDenied):
        return ("Нет прав IAM. Проверь: KSA привязан к GSA (Workload Identity), "
                "у GSA есть роль на этот ресурс. GET /check/identity покажет, от чьего имени работает под.")
    if isinstance(exc, gexc.NotFound):
        return "Ресурс не найден. Проверь имя ресурса и GOOGLE_CLOUD_PROJECT в env."
    if isinstance(exc, gexc.Unauthenticated):
        return "Нет учётных данных. В GKE: включён ли Workload Identity на кластере и node pool?"
    if isinstance(exc, (gexc.DeadlineExceeded, gexc.ServiceUnavailable, gexc.RetryError, TimeoutError)):
        return ("API Google недоступен по сети. Проверь private_ip_google_access на подсети "
                "и firewall egress.")
    return "Неизвестная ошибка, смотри error и логи пода."


def run(component: str, check, hint_fn=google_hint) -> dict:
    try:
        return check()
    except Exception as exc:  # noqa: BLE001 — нам нужна любая ошибка в ответе
        return fail(component, exc, hint_fn(exc))


# ---------- health ----------

@app.get("/healthz")
def healthz():
    """Liveness: процесс жив. Внешние зависимости не трогает."""
    return {"ok": True}


# ---------- identity ----------

def check_identity() -> dict:
    headers = {"Metadata-Flavor": "Google"}
    email = httpx.get(f"{METADATA_URL}/instance/service-accounts/default/email",
                      headers=headers, timeout=3).text
    project = httpx.get(f"{METADATA_URL}/project/project-id", headers=headers, timeout=3).text
    return ok("identity", service_account=email, project=project)


def identity_hint(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPError):
        return ("Metadata server недоступен. Вне GCP это нормально; в GKE проверь, "
                "что под запущен на узле с GKE metadata server (Workload Identity).")
    return google_hint(exc)


@app.get("/check/identity")
def identity():
    return respond(run("identity", check_identity, identity_hint))


# ---------- database ----------

def read_db_password() -> str:
    path = env("DB_PASSWORD_FILE")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError as exc:
        raise ConfigError(f"файл с паролем {path} не найден — секрет не смонтирован") from exc


def check_db() -> dict:
    note = f"stand-check-{uuid.uuid4()}"
    with psycopg.connect(
        host=env("DB_HOST", "127.0.0.1"),
        port=int(env("DB_PORT", "5432")),
        dbname=env("DB_NAME"),
        user=env("DB_USER"),
        password=read_db_password(),
        connect_timeout=5,
    ) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS stand_check ("
            " id serial PRIMARY KEY,"
            " note text NOT NULL,"
            " created_at timestamptz NOT NULL DEFAULT now())"
        )
        row_id = conn.execute(
            "INSERT INTO stand_check (note) VALUES (%s) RETURNING id", (note,)
        ).fetchone()[0]
        read_back = conn.execute(
            "SELECT note FROM stand_check WHERE id = %s", (row_id,)
        ).fetchone()[0]
        total = conn.execute("SELECT count(*) FROM stand_check").fetchone()[0]
    if read_back != note:
        raise RuntimeError(f"прочитано {read_back!r}, ожидалось {note!r}")
    return ok("database", written_id=row_id, rows_total=total)


def db_hint(exc: Exception) -> str:
    if isinstance(exc, ConfigError):
        return str(exc) + ". Проверь env и SecretProviderClass / volumeMounts."
    text = str(exc).lower()
    if "password authentication failed" in text:
        return "Неверный пароль: секрет в Secret Manager не совпадает с паролем пользователя Cloud SQL."
    if "does not exist" in text:
        return "Нет такой базы или пользователя. Проверь google_sql_database / google_sql_user и DB_NAME / DB_USER."
    if isinstance(exc, psycopg.OperationalError):
        return ("Нет соединения с 127.0.0.1:5432. Запущен ли sidecar Cloud SQL Auth Proxy? "
                "Смотри его логи: kubectl logs <pod> -c <имя контейнера прокси>. "
                "Частые причины: нет роли Cloud SQL Client у GSA, прокси без --private-ip, нет PSA-пиринга.")
    return google_hint(exc)


@app.get("/check/db")
def db():
    return respond(run("database", check_db, db_hint))


# ---------- pub/sub ----------

def check_pubsub() -> dict:
    project = env("GOOGLE_CLOUD_PROJECT")
    topic = pubsub_v1.PublisherClient.topic_path(project, env("PUBSUB_TOPIC"))
    subscription = pubsub_v1.SubscriberClient.subscription_path(project, env("PUBSUB_SUBSCRIPTION"))
    check_id = str(uuid.uuid4())

    publisher = pubsub_v1.PublisherClient()
    message_id = publisher.publish(topic, b"stand-check", check_id=check_id).result(timeout=15)

    with pubsub_v1.SubscriberClient() as subscriber:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                response = subscriber.pull(
                    request={"subscription": subscription, "max_messages": 10}, timeout=10
                )
            except gexc.DeadlineExceeded:
                continue  # пустая очередь, пробуем ещё раз
            if not response.received_messages:
                continue
            # подтверждаем всё прочитанное, включая хвосты прошлых проверок
            subscriber.acknowledge(
                request={"subscription": subscription,
                         "ack_ids": [m.ack_id for m in response.received_messages]}
            )
            for m in response.received_messages:
                if m.message.attributes.get("check_id") == check_id:
                    return ok("pubsub", published_message_id=message_id,
                              received_message_id=m.message.message_id)
    raise TimeoutError("сообщение опубликовано, но за 20 с не пришло в подписку")


def pubsub_hint(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return ("Publish прошёл, pull — нет. Подписка привязана к этому топику? "
                "У GSA есть роль subscriber на подписку?")
    return google_hint(exc)


@app.get("/check/pubsub")
def pubsub():
    return respond(run("pubsub", check_pubsub, pubsub_hint))


# ---------- storage ----------

def check_storage() -> dict:
    bucket = storage.Client(project=env("GOOGLE_CLOUD_PROJECT")).bucket(env("GCS_BUCKET"))
    blob = bucket.blob(f"stand-check/{uuid.uuid4()}.txt")
    content = f"stand-check {time.time()}"
    blob.upload_from_string(content, content_type="text/plain")
    downloaded = blob.download_as_text()
    blob.delete()
    if downloaded != content:
        raise RuntimeError("скачанный файл отличается от загруженного")
    return ok("storage", object=blob.name, bytes=len(content))


@app.get("/check/storage")
def gcs():
    return respond(run("storage", check_storage))


# ---------- egress ----------

def check_egress() -> dict:
    url = env("EGRESS_CHECK_URL", "https://www.google.com/generate_204")
    response = httpx.get(url, timeout=5)
    return ok("egress", url=url, status=response.status_code)


def egress_hint(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return ("Нет выхода в интернет. Если так и задумано — всё верно. "
                "Иначе: включён ли Cloud NAT и транслирует ли он диапазон подов?")
    if isinstance(exc, httpx.ConnectError):
        return "Соединение не установлено: DNS или firewall egress."
    return google_hint(exc)


@app.get("/check/egress")
def egress():
    return respond(run("egress", check_egress, egress_hint))


# ---------- all ----------

@app.get("/check/all")
def check_all():
    results = [
        run("identity", check_identity, identity_hint),
        run("database", check_db, db_hint),
        run("pubsub", check_pubsub, pubsub_hint),
        run("storage", check_storage),
        run("egress", check_egress, egress_hint),
    ]
    all_ok = all(r["ok"] for r in results)
    return JSONResponse({"ok": all_ok, "checks": results}, status_code=200 if all_ok else 503)


def respond(result: dict) -> JSONResponse:
    return JSONResponse(result, status_code=200 if result["ok"] else 503)
