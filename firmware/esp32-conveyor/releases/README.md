# Firmware releases

`*.merged.bin` содержит bootloader, partition table и приложение и прошивается
в новую/полностью стираемую ESP32 с адреса `0x0`. Такой образ затирает NVS — не
используйте его как OTA/update поверх уже настроенного контроллера.

Приватные BLE provisioning credentials, Wi-Fi passwords и backend device tokens
в эту директорию и Git не добавляются.

## `universal-bench-d15-v1.1.0`

Это общий **стендовый** образ для одинаковых classic ESP32 с flash 4 МБ и одним
active-low реле на GPIO15. GPIO4 используется как provisioning/reset-кнопка.
Backend `device_id`, token и Wi-Fi задаются после прошивки через BLE и поэтому
не зашиты в BIN.

GPIO15 — strapping pin classic ESP32. Проверьте, что вход конкретного релейного
модуля не ломает уровень при reset, плата стабильно проходит холодный старт и
реле остаётся OFF. Для перепривязки использованной платы к другому `device_id`
нужны полный `erase-flash`, повторная прошивка и provisioning.

В образе включён `CONFIG_ASYL_BENCH_NO_PHYSICAL_FEEDBACK=y`: сообщаемый
`feedback_state` является эхом GPIO и не подтверждает состояние реле,
контактора или двигателя. Образ нельзя подключать к производственному
конвейеру. Для production обязательны независимый feedback-вход, E-stop и
отдельная аппаратно проверенная сборка.

Перед прошивкой проверьте файл:

```bash
cd firmware/esp32-conveyor/releases
shasum -a 256 -c SHA256SUMS
```

Прошивка новой или полностью стираемой платы:

```bash
uvx esptool --chip esp32 --port /dev/cu.usbserial-XXXX erase-flash
uvx esptool --chip esp32 --port /dev/cu.usbserial-XXXX \
  write-flash 0x0 asyl-conveyor-universal-bench-d15-v1.1.0.merged.bin
```

Общий Security2 bootstrap password хранится и передаётся отдельно. Его нельзя
добавлять в Git или класть рядом с публичным BIN.
