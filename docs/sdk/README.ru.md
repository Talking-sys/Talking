# SDK Talking для игр

Copyright (C) 2026 hateaboutyouone. Контакт: hateaboutyou111@icloud.com.

Встраиваемая **мгновенная переводческая** подсистема: студия отдаёт **один** текст и **один** голос. В рантайме игра отправляет строку или аудио в Talking и получает реплику на языке игрока (и, по желанию, озвучку).

English: [`README.md`](README.md). План и привязки движков: [`PLAN.md`](PLAN.md). HTTP: [`openapi-sdk-v1.yaml`](openapi-sdk-v1.yaml).

Клиентские библиотеки в `sdk/` **предполагаются под MIT** (закрытые игры могут их встраивать). Подтверждение этого разделения **ожидается** от правообладателя — `sdk/NOTICE.md`. Сервер остаётся GNU GPL v3. Это не юридическая консультация.

## Что даёт SDK

| Вызов | Вход | Выход |
|---|---|---|
| `list_languages` | — | список языков Whisper с сервера (те же коды, что у приложения Talking) |
| `translate_text` | строка, `source` (по умолчанию `auto`), `target` | перевод, провайдер, время; опционально TTS |
| `translate_audio` | WAV / PCM (байты, поток или файл) | распознанный текст + перевод; опционально TTS |
| `translate_text_stream` | как текст | конверт SSE (в v1 — одно финальное событие) |

`set_target_language` / `get_target_language` хранят **клиентский** язык по умолчанию. Серверной игровой сессии в v1 нет.

## Установка

```bash
pip install -e sdk/python
```

JavaScript: каталог `sdk/js` (пакет `talking-sdk`). Публикация в PyPI / npm в этом скелете не делается.

Unity / Unreal / Godot: наброски интерфейсов в `sdk/bindings/` — пока вызывайте HTTP.

## Инициализация

**Не вставляйте ключ API в исходники, скриншоты и логи.**

```python
import os
from talking_sdk import TalkingClient

client = TalkingClient(
    server_url=os.environ["TALKING_SERVER_URL"],
    api_key=os.environ["TALKING_API_KEY"],
    app_id=os.environ.get("TALKING_APP_ID", "my-game"),
    timeout=15.0,
    max_retries=3,
    offline=False,
    target_language="es",
)
```

На сервере Talking:

```bash
SDK_API_KEYS=my-game:change-me
SDK_RATE_LIMIT_PER_MIN=60
APP_TRANSLATE_PROVIDERS=stub
```

Формат `SDK_API_KEYS`: `app_id:секрет,other:секрет2`. Пустое значение → **503** `sdk_disabled`.

Заголовок `Authorization: Bearer <секрет>` (или `X-Talking-Api-Key`). Необязательный `X-Talking-App-Id` должен совпасть с id, привязанным к секрету.

## Вызов перевода

```python
client.set_target_language("de")
line = client.translate_text("Мост обрушен.", source="ru")
print(line.translation)
clip = client.translate_audio(open("line.wav", "rb"), format="wav")
print(clip.text, "→", clip.translation)
```

`source='auto'`: для текста — автоопределение провайдера; для аудио — язык Whisper.

Асинхронно: `AsyncTalkingClient`. В JS все методы уже возвращают Promise.

## Языки

Список **не выдуман**. Это набор Whisper из `talking/languages.py`, те же коды, что `GET /api/languages` и `GET /api/sdk/v1/languages`. Примеры: `en`, `ru`, `de`, `fr`, `es`, `zh`, `ja`, `ko`, `ar`, `hi`, `pt`, `tr` и ещё ~100. В рантайме вызывайте `list_languages()`, чтобы игра не расходилась с сервером.

## Задержки и лимиты (скелет v1)

| Лимит | По умолчанию | Переменная |
|---|---|---|
| Длина текста | 4000 символов | `SDK_MAX_TEXT_CHARS` |
| Тело аудио | 5 МиБ | `SDK_MAX_AUDIO_BYTES` |
| Форматы | `wav`, `pcm_s16le`, `pcm_f32le` | — |
| Частота запросов | 60 / мин / ключ | `SDK_RATE_LIMIT_PER_MIN` |
| Таймаут перевода | 15 с | `APP_TRANSLATE_TIMEOUT` / `timeout` клиента |
| TTS | выключен | `APP_TTS=1` и `APP_TTS_PROVIDERS=gtts`; `return_audio=true` |

Заглушка `APP_TRANSLATE_PROVIDERS=stub` отвечает сразу и префиксирует `[src→tgt]`. HTTP **429** содержит `Retry-After`; клиенты повторяют 429/502/503/504 сами.

## Приватность

- Запросы SDK **без состояния**: не пишутся в трёхминутный буфер, чат и историю пользователя телефона.
- Текст сегмента уходит к настроенным переводчикам (Grok / бесплатный Google / stub) без привязки к аккаунту игрока.
- В логах сервера: id приложения, хеш ключа, коды языков и размеры — **не реплика и не ключ**.
- Рекомендуется проксировать API через **сервер студии**, не вшивая долгоживущий ключ в бинарник игры.
- Игрока нужно предупредить, если отправляется голос (`translate_audio`).
- Запросы по данным: hateaboutyou111@icloud.com. Заготовки: `/privacy`. Не юридическая консультация.

## Ошибки

`AuthError` (401), `RateLimitError` (429), `InvalidRequestError` (400/415/422, в т.ч. `no_speech`), `UnsupportedLanguageError`, `PayloadTooLargeError` (413), `ServiceUnavailableError` (503), `TalkingTimeoutError` (504), `OfflineError`, `NetworkError`. `repr(client)` ключ не содержит.

## Примеры

- Текстовая таверна: `sdk/examples/python_game_demo/`
- Браузерная форма: `sdk/examples/browser/` (для чужого origin задайте `APP_CORS_ORIGINS`)

## Дорожная карта

[`PLAN.md`](PLAN.md): пакеты Unity/Unreal/Godot, потоковый SSE по токенам, короткоживущие токены игрока, опциональный офлайн-пакет.
