# ASYL ESP32 Conveyor

ESP-IDF прошивка контроллера конвейера. Телефон передаёт Wi-Fi и device
credentials через зашифрованный BLE Security2 provisioning. После настройки
ESP32 сам делает исходящие HTTPS-запросы в ASYL API. Входящий порт, публичный
IP и «вебхук прямо в ESP32» не нужны.

> Wi-Fi нельзя гарантированно «никогда не отключать». Прошивка бесконечно
> переподключается с exponential backoff, но при любой потере команд не позже
> локального lease переводит выход в `OFF`.

## Контур безопасности

- В самом начале `app_main` GPIO команды устанавливается в `OFF`.
- Команда `ON` живёт максимум 1500 мс. Каждый успешный HTTPS sync продлевает
  lease; отсутствие ответа его не продлевает.
- Высокоприоритетная safety-задача проверяет lease каждые 20 мс. Task watchdog
  перезагружает зависший контроллер, который снова загружается в `OFF`.
- В NVS сохраняются последняя принятая `revision` и terminal fault fence.
- После каждой загрузки API сначала обязан вернуть `OFF`. Затем `ON` принимается
  только с более новой `revision`. Поэтому питание/сеть не возобновляют привод.
- Та же `ON revision` может продлевать lease только в той же загрузке и для той
  же сессии. Старая, изменённая или faulted revision включает fail-OFF.
- `feedback_state` читается с отдельного GPIO вспомогательного контакта
  контактора. Несовпадение после settle timeout защёлкивает fault и снимает
  командный выход.
- Fault снимается только после более новой серверной команды `OFF` и
  подтверждённого физического feedback `OFF`; заблокированная ON revision
  остаётся в NVS и больше никогда не принимается.
- `OFF` применяется сразу даже до синхронизации часов. `ON` требует SNTP и
  свежий `server_time`.

Это fail-safe программная логика, но не сертифицированная functional-safety
система. В шкафу всё равно обязательны аппаратный E-stop, защита двигателя,
безопасный контактор, независимый auxiliary contact и аппаратный pull-down
(либо pull-up для active-low) на управляющем GPIO. Для гарантии при физической
поломке ESP32 нужен внешний safety relay/watchdog с дедлайном не более 2 с.

## Требования

- ESP-IDF 5.2 или 5.3;
- ESP32 с BLE, Wi-Fi и flash не меньше 4 МБ (не ESP32-S2);
- релейный/контакторный вход с гальванической развязкой, совместимый с 3.3 В;
- отдельный изолированный feedback вход;
- backend endpoint из [PROTOCOL.md](PROTOCOL.md).

## Конфигурация и сборка

```bash
. "$IDF_PATH/export.sh"
cd firmware/esp32-conveyor
idf.py set-target esp32
idf.py menuconfig
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

В `ASYL conveyor controller` обязательно выставить:

1. реальные GPIO команды, feedback и provisioning-кнопки;
2. уникальные для каждого устройства Security2 SRP6a salt/verifier в hex;
3. active-high/active-low согласно электрической схеме.

Security2 material генерируется штатными инструментами примера ESP-IDF
`wifi_prov_mgr`. Не используйте demo salt/verifier и не переиспользуйте их
между устройствами. Зафиксируйте сгенерированную пару username/password:
salt/verifier прошиваются в ESP32, а точные username/password передаются
монтажнику отдельным защищённым каналом или QR-наклейкой. Без обоих значений
официальный provisioning-клиент не установит Security2-сессию.

Локальный генератор создаёт все приватные файлы с правами `0600`, не печатает
секреты и отказывается перезаписывать существующую пару:

```bash
./tools/generate_security2.py \
  --idf-path "$IDF_PATH" \
  --device-name ASYL-CONV-A1B2C3 \
  --output-dir device-secrets/esp32-a1b2c3 \
  --sdkconfig-output sdkconfig.defaults.local

idf.py -B build/device-a1b2c3 \
  -DSDKCONFIG_DEFAULTS="sdkconfig.defaults;sdkconfig.defaults.local" \
  build size
```

`device-secrets/`, `sdkconfig.defaults.local`, `sdkconfig` и `build/` исключены
из Git. Приватную копию `provisioning-credentials.json` храните отдельно от
шкафа и репозитория.

Для production включить и проверить на конкретном чипе:

- Secure Boot V2 с собственным закрытым ключом;
- Flash Encryption в release mode;
- NVS Encryption;
- отключение/защиту JTAG и UART download mode;
- уникальный device token для каждого ESP32.

Ключи не хранятся в репозитории. Сначала проверьте recovery-процесс на
непроизводственном контроллере: release flash encryption необратима.

Production build принимает только точный base URL
`https://asyl-ltd.kz/api`. Опция `ASYL_ALLOW_CUSTOM_API_BASE_URL` предназначена
только для локального стенда и по умолчанию выключена. HTTPS redirects,
userinfo, query/fragment и hostname mismatch отклоняются.

## BLE provisioning

BLE запускается только когда нет полной конфигурации либо при удержании
provisioning-кнопки во время загрузки. Имя имеет вид `ASYL-CONV-A1B2C3`.

После Security2 handshake provisioning-клиент должен вызвать standard
custom endpoint `custom-data` до передачи Wi-Fi credentials. Это имя
совместимо с официальным `esp_prov.py --custom_data`:

```json
{
  "base_url": "https://asyl-ltd.kz/api",
  "device_id": "f6b1202e-815d-4f87-8a23-71e12e86b739",
  "token": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
}
```

Ограничения строгие:

- `device_id` — canonical lowercase RFC 4122 UUIDv4, ровно 36 символов;
- `token` — base64url без `=`, ровно 43 символа;
- неизвестные/дублированные поля отклоняются;
- production `base_url` можно опустить, но нельзя заменить другим host.

После успешной настройки BLE освобождается, Wi-Fi credentials остаются во
flash и ESP32 переподключается без лимита попыток (backoff 0.5–30 с). Удержание
кнопки стирает API/Wi-Fi credentials, но **не стирает** replay/fault revisions.

Для первого подключения зарегистрируйте контроллер в backend и сохраните
показанный один раз `credential.token`. Затем на ноутбуке с ESP-IDF 5.3 и
доступом к Bluetooth выполните helper ниже. Пароль Wi-Fi и device token он
спросит скрыто и не поместит в shell history/process list:

```bash
. "$IDF_PATH/export.sh"
./tools/provision_device.py \
  --credentials device-secrets/esp32-a1b2c3/provisioning-credentials.json \
  --device-id '<UUID из backend>' \
  --ssid '<SSID 2.4 GHz>'
```

Если BLE-зависимости ESP-IDF ещё не установлены, один раз выполните
`bash "$IDF_PATH/install.sh" --enable-pytest` и снова загрузите `export.sh`.
На macOS разрешите Bluetooth для Terminal/Python. Helper использует только
официальный ESP-IDF client, Security2 и endpoint `custom-data`.

## Проверки

Чистая логика command guard тестируется обычным host compiler без ESP-IDF:

```bash
firmware/esp32-conveyor/tests/host/run.sh
```

Перед прошивкой также обязательны:

```bash
idf.py build
idf.py size
```

На стенде проверить минимум: boot без сети, обрыв Wi-Fi во время `ON`, timeout
HTTPS, просроченный `server_time`, replay старой revision, перезагрузку во время
`ON`, залипший feedback, обрыв feedback и физический E-stop.
