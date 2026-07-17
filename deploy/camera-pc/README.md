# Windows camera agent

Этот пакет защищает Windows-ПК камер от повторения полного обрыва. Он следит за
задачей и процессом MediaMTX, портами `8554/8888/8889/9996`, количеством RTSP-связей с
NVR и состоянием Tailscale. Секретов и реквизитов камер в пакете нет.

## Что именно делает агент

- отсутствующий процесс или listener чинит сразу;
- хранит site-baseline `20` RTSP-источников (10 камер: main+sub) отдельно от
  текущего конфига; обрезанный конфиг не может занизить expected count;
- одну/несколько пропавших камер помечает как `degraded`, но **не роняет остальные
  камеры перезапуском**;
- перезапускает MediaMTX из-за источников только после трёх подряд проверок и
  потери не менее 60% потоков при доступном NVR;
- при недоступном NVR не перезапускает MediaMTX по кругу;
- после успешного ремонта hard-failure имеет только минутный guard (восстановление
  не дольше следующего цикла), а source-loss — отдельный cooldown 5 минут;
- записывает попытку до перезапуска и применяет постоянный backoff
  `5 → 15 → 30 → 60 минут` после неудач;
- проверяет службу, control-plane и адрес Tailscale, а также указанный tailnet-peer;
- запускается при старте Windows и каждую минуту от `SYSTEM`, без входа оператора;
- блокирует supervisor, NVR-sync и обновление конфига одним global mutex;
- отключает задачи со старым `nvr-watchdog.ps1`, сохраняя их XML для rollback;
- пишет атомарный status в
  `C:\ProgramData\ASYL-Camera-Agent\state.json` и ротируемый `agent.log`.
- записывает только аннотированные AI-потоки `cam…ai` в
  `C:\mediamtx\recordings`, нарезает их по 5 минут и автоматически удаляет
  старше 14 дней; веб-сервер получает видео через локальный playback API и не
  хранит копии файлов.

## Установка

Скопировать всю папку `deploy/camera-pc` на Windows-ПК, например в
`C:\Temp\camera-agent`, затем открыть **PowerShell от администратора**:

```powershell
cd C:\Temp\camera-agent
powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\Invoke-StaticTests.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 `
  -MediaRoot C:\mediamtx `
  -TailnetPeer '<TAILSCALE-IP-PROD-СЕРВЕРА>'
```

AI-сервис ставится отдельной защищённой boot-задачей после MediaMTX. Папки
`deploy/camera-pc` и `cv_service` должны сохранять структуру репозитория; веса
передаются отдельно и в git не попадают:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\install-ai-service.ps1 `
  -ApiKeySha256 '<64-символьный SHA-256>' `
  -ModelPath 'C:\Temp\best.pt' `
  -BackendTailnetIp '<TAILSCALE-IP-PROD-СЕРВЕРА>' `
  -ModelDevice '0' `
  -PrewarmCameras 'cam2' `
  -MaxActiveProcessors 2
```

Installer сохраняет только SHA-256 ключа, прогревает и валидирует модель до
регистрации HTTP-задачи, закрывает ACL для обычных пользователей и создаёт
Windows Firewall rule на порт `8890` только с указанного backend-IP. Состояние:

```powershell
Get-ScheduledTask -TaskName ASYL-AI-Service
Get-ScheduledTaskInfo -TaskName ASYL-AI-Service
Get-Content C:\mediamtx\ai-service\service.log -Tail 100
```

`TailnetPeer` — Tailscale IP production-сервера, который забирает камеры. Его
нужно получить на сервере командой `tailscale ip -4`; пароль или auth-key здесь
не нужен. Если параметр временно оставить пустым, агент всё равно проверит
локальную службу и подключение Tailscale, но не end-to-end peer.

Installer идемпотентен: повторный запуск создаёт новый timestamped backup, затем
атомарно заменяет пакет и перерегистрирует задачи. Исходные XML задач и предыдущая
версия агента сохраняются в `C:\mediamtx\camera-agent-backups\<timestamp>`.
Если установка падает после создания manifest, выполняется автоматический rollback.
До регистрации SYSTEM-задачи installer проверяет текущий `mediamtx.yml` и
фиксирует неубывающие floors источников/путей. Папки installed scripts,
ProgramData и конкретного backup получают protected ACL: полный доступ только у
`SYSTEM` и локальных Administrators; наследуемые права обычных пользователей
удаляются, чтобы SYSTEM-скрипт нельзя было подменить.
На время freeze/backup старый `MediaMTX-NVR-Sync` сначала отключается и
останавливается; его исходные enabled/running state сохраняются в manifest и
восстанавливаются после успешной установки либо rollback.

## Проверка состояния

```powershell
C:\mediamtx\camera-agent\status.ps1
C:\mediamtx\camera-agent\status.ps1 -AsJson
Get-Content C:\ProgramData\ASYL-Camera-Agent\agent.log -Tail 50
```

Нормальное состояние: `AgentStatus=healthy`, один процесс MediaMTX, все четыре
listener-порта, ожидаемое число sources и `Tailscale.Healthy=true`.

Архив доступен приложению только пока Windows-ПК камер включён. Проверить его
локально можно командой `Invoke-WebRequest http://127.0.0.1:9996/list`; политика
14 дней восстанавливается installer-ом после каждого NVR-sync и безопасного
обновления `mediamtx.yml`.

## Безопасное обновление mediamtx.yml

Не заменять рабочий конфиг через `Set-Content`. Подготовить candidate и выполнить:

```powershell
C:\mediamtx\camera-agent\update-mediamtx-config.ps1 `
  -CandidatePath C:\Temp\mediamtx.candidate.yml
```

Команда проверит структуру и RTSP URI, заменит файл атомарно, запустит MediaMTX и
проверит процесс/listeners. Если новая версия не поднимается, предыдущий конфиг
возвращается автоматически. Existing `MediaMTX-NVR-Sync` installer оборачивает
скриптом `run-nvr-sync.ps1`, который использует тот же mutex, проверяет полученный
конфиг и возвращает snapshot при ошибке. И ручное обновление, и NVR-sync обязаны
сохранить минимум `20` eager sources/paths и установленный current baseline;
формально валидный конфиг только с одной камерой отклоняется до рестарта.
Current baseline и точный snapshot candidate читаются и проверяются уже внутри
общего mutex, поэтому параллельное расширение NVR-sync не может быть затёрто
устаревшим ручным candidate.

## Rollback

Последняя установка:

```powershell
C:\mediamtx\camera-agent\rollback.ps1
```

Конкретный backup:

```powershell
C:\Temp\camera-agent\rollback.ps1 `
  -BackupDirectory C:\mediamtx\camera-agent-backups\20260713-180000
```

Rollback восстанавливает прежние XML `MediaMTX`, supervisor, NVR-sync и старых
watchdog-задач, затем возвращает предыдущую папку агента.

## Обязательный fault-test после установки

Проводить в согласованное окно, наблюдая production camera wall.

1. `Stop-Process -Name mediamtx -Force` — задача должна восстановиться максимум
   за две минуты, в state появится ровно одна новая restart attempt.
2. Отключить одну тестовую камеру — state должен стать `degraded`, счётчик
   `TotalRestartAttempts` не должен измениться, остальные камеры продолжают видео.
3. Кратко отключить NVR — ожидается `nvr-unreachable` без restart-loop.
4. `Stop-Service Tailscale -Force` — агент пробует поднять службу один раз и не
   повторяет попытку чаще заданного cooldown.
5. Передать заведомо битый candidate в `update-mediamtx-config.ps1` — файл должен
   быть отклонён до замены рабочего конфига.
6. Перезагрузить Windows без входа пользователя — обе задачи должны подняться от
   `SYSTEM`, а cameras health вернуться в normal.

После fault-test нужен 24–72-часовой soak: серверный монитор должен видеть 10/10,
а `RestartFailureCount` оставаться нулевым.
