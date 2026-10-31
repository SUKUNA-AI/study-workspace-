# Учебный цикл

Рабочий каталог — корень клона `study-workspace-`. Сначала открой условие в
`materials/tasks/<judge-id>/statement.md` и путь из `.course/task-index.json`.
Старые работы сохраняют свой действующий путь; автоматического переноса решений нет.

Если клона ещё нет, выполни в выбранной родительской папке:
```text
git clone --recurse-submodules https://github.com/SUKUNA-AI/study-workspace-.git
cd study-workspace-
```

Готовые клиенты Linux/Windows и CPU-песочница находятся в
[публичном выпуске judge-v2026.10.31.1](https://github.com/SUKUNA-AI/study-materials/releases/tag/judge-v2026.10.31.1).
Из корня клона получи manifest и проверь его SHA-256. Приватный токен для скачивания не нужен.

PowerShell:
```powershell
New-Item -ItemType Directory -Force .study | Out-Null
Invoke-WebRequest -Uri 'https://github.com/SUKUNA-AI/study-materials/releases/download/judge-v2026.10.31.1/judge-release.json' -OutFile .study/judge-release.json
$expectedManifest = 'd21148299d087307481dd499ca50a47d93bf5f98bf6e8356ef5cdafdb614046f'
$actualManifest = (Get-FileHash -LiteralPath .study/judge-release.json -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualManifest -ne $expectedManifest) { throw 'Manifest checksum mismatch; stop installation' }
python tools/study.py install-judge --release-file .study/judge-release.json
python tools/study.py install-sandbox --release-file .study/judge-release.json
$env:PATH = "$PWD/.study/bin;$env:PATH"
```

Bash:
```bash
mkdir -p .study
curl --fail --location --proto '=https' 'https://github.com/SUKUNA-AI/study-materials/releases/download/judge-v2026.10.31.1/judge-release.json' -o .study/judge-release.json
printf '%s  %s\n' 'd21148299d087307481dd499ca50a47d93bf5f98bf6e8356ef5cdafdb614046f' '.study/judge-release.json' | sha256sum --check -
python3 tools/study.py install-judge --release-file .study/judge-release.json
python3 tools/study.py install-sandbox --release-file .study/judge-release.json
export PATH="$PWD/.study/bin:$PATH"
```

Ожидаемый результат — подтверждённые checksum клиента и полный ID образа. Для CPU-песочницы
нужен работающий Docker с Linux-контейнерами. Установка образа — отдельное действие;
doctor ничего не устанавливает. Далее открой условие через `prepare`, напиши собственный
исходник по указанному пути и только затем выполняй строку `check`.

PowerShell:
```powershell
git submodule update --init --recursive
python tools/study.py doctor --profile windows-cpu
python tools/study.py prepare U01.S54
python tools/study.py check U01.S54 --source cpp/u01/g-u01-01-sum-even/solution.cpp
```

Bash:
```bash
git submodule update --init --recursive
python3 tools/study.py doctor --profile linux-cpu
python3 tools/study.py prepare U01.S54
python3 tools/study.py check U01.S54 --source cpp/u01/g-u01-01-sum-even/solution.cpp
```

`prepare` показывает проверенное условие и путь, не создаёт решение или сборку.
Клиент: `python tools/study.py install-judge --release-file <проверенный judge-release.json>`.
Публичная CPU-проверка использует Docker с Linux-контейнерами. Отдельная установка готовой
песочницы: `python tools/study.py install-sandbox --release-file <проверенный judge-release.json>`.
Она проверяет SHA256 архива и полный ID образа; doctor ничего не устанавливает.
Docker Classic может показывать config ID, а Docker Desktop — OCI index ID того же образа.
Обвязка проверяет их связь по pinned OCI descriptors из checksum-верного архива, rootfs и
runtime config; произвольный image ID не принимается. Уже загруженный образ можно проверить
без повторного `docker load`: `python tools/study.py install-sandbox --release-file <проверенный judge-release.json> --archive <локальный архив release> --verify-only`.
Для серверной native-проверки добавь `.study/bin` в PATH и выполни `judge login https://study.sukuna.tech`
(токен вводится штатным клиентом). Для локальной Docker-проверки login не нужен.
PowerShell: `$env:PATH="$PWD/.study/bin;$env:PATH"`;
Bash: `export PATH="$PWD/.study/bin:$PATH"`.
`check` локально компилирует проверочный harness и выполняет только публичные тесты без login;
свою учебную сборку ты настраиваешь сам. `check --remote` явно отправляет на сервер.
`check` сохраняет неизменяемый snapshot и результат реального judge в `.study/`.
Локальный отчёт имеет происхождение `self_reported`; доверие назначает сервер.
После исправления выполни новую проверку: старый результат относится к старому исходнику.

Для личного CUDA-агента сначала вычисли SHA256 выбранного собственного исходника:
`(Get-FileHash <путь-к-исходнику> -Algorithm SHA256).Hash.ToLower()`.
Запуск требует явного согласия на конкретные аккаунт, задание и снимок:
`judge-agent run --personal --approve-owner <числовой-ID> --approve-task <точный-task-ID> --approve-source-sha256 <sha256> --once`.
Личный режим Windows выполняет код с правами твоего пользователя и не является файловой
песочницей. Подтверждай только свой проверенный исходник; согласие действует лишь на этот
процесс. При несовпадении серверного owner/task/source агент отказывает до скачивания и запуска.

Создание попытки и импорт проверки: `python tools/study.py submit U01.S54 --source <путь>`.
Для owner API нужен `STUDY_OWNER_TOKEN`, настроенный владельцем портала с правами только своего аккаунта.
Полученный receipt содержит attempt ID и файл точного AssessmentContract.
Если ответ сети потерялся, повтори сохранённый request ID командой
`python tools/study.py resume-submit <путь pending-submit.json>`. Не создавай новую отправку вслепую.
Для учебного ревью: `python tools/study.py review U01.S54 --source <путь> --attempt <id> --contract <файл> --receipt <submission.json>`.
Недоступность Codex даёт `review_unavailable`; ошибка сборки не превращается в успешный runtime.
Для контрольной ИИ не используется. Отзыв не содержит готового решения.

Если работа состоит из нескольких файлов, выбери их явно. `prepare` показывает обязательные
артефакты. Основной код задаётся `--source`, дополнительный файл — повторяемым `--file`.
Например, l01 требует код и собственную таблицу strace:
```text
python tools/study.py submit U02.S65 --source linux/u02/l01-syscall-errors/solution.cpp --file homework/u02/l01-syscall-errors.md
python tools/study.py review U02.S65 --source linux/u02/l01-syscall-errors/solution.cpp --file homework/u02/l01-syscall-errors.md --attempt <id> --contract <файл-контракта> --receipt <submission.json>
```
Обычный `check --source <код>` проверяет только код. Дополнительная заметка не считается
исполненной native-проверкой; весь набор сохраняется отдельным immutable snapshot.

Для мини-проекта U01 выбери только объявленную папку своей работы:
```text
python tools/study.py prepare U01.S71 --source-dir homework/u01/project
python tools/study.py submit U01.S71 --source-dir homework/u01/project
python tools/study.py review U01.S71 --source-dir homework/u01/project --attempt <id> --contract <файл-контракта> --receipt <submission.json>
```
Сначала создай свои исходники, тесты, сборку и README. Команды их не генерируют и проект
не собирают. При `requiredProfiles=[]` для ревью артефактов не нужны judge login, Docker
или native-профиль; без реального подтверждения сборки runtime-критерий остаётся `not_verified`.
Owner API по-прежнему требует отдельную учётную настройку портала. Runner/reviewer credentials
хранятся только у установленного доверенного адаптера вне learner workspace.

Выбор папки ограничен каталогом этой задачи. Build outputs исключаются; инструкции, приватные
и managed paths, symlinks, binary и дубликаты отклоняются. Лимиты: 32 UTF-8 файла,
64 КиБ каждый, 256 КиБ суммарно. Snapshot хранит точные bytes и digest всего набора,
отдельно — hash primary. Изменение README/заметки требует новой попытки/применимого ревью.

Выгрузка исходника: `python tools/study.py export-source U01.S54 --source <путь> --out submission.json`.
Импорт из браузера: `python tools/study.py import submission.json --task U01.S54`.
Существующий отличающийся файл не перезаписывается: сначала сохрани оба варианта вручную.
Для проекта выгрузи весь выбранный набор: `python tools/study.py export-source U01.S71 --source-dir homework/u01/project --out project-source.json`.
Полный архив принятой попытки доступен в её истории. `import <архив> --task U01.S71` сначала
проверяет все пути, hashes и конфликты. Если один файл отличается, ничего не записывается:
осознанно перенеси локальный вариант в резервную папку вне каталога этой задачи и повтори импорт.
Повтор идентичного импорта не создаёт дубликаты. Materials, инструкции и файлы обвязки защищены.
Импорт отчёта: `python tools/study.py publish-report <report.json> --portal https://study.sukuna.tech --attempt <id>`;
owner credential передаётся через `STUDY_OWNER_TOKEN`, не в исходнике и не reviewer-процессе.

Обновление — отдельный diff pin и `.course/`: `python tools/study.py update --bundle <проверенный root bundle> --expect-digest <sha256>`.
Локальные изменения материалов и обвязки блокируют запись. После обрыва: `python tools/study.py recover`.
Не запускай `git submodule update --remote`. Перед commit проверь `git diff` и `git status`.

Публичная обратная связь: шаблон issue в GitHub; не прикладывай токены, личные черновики и закрытые материалы.
Точная [форма сообщения](https://github.com/SUKUNA-AI/study-workspace-/issues/new?template=course-feedback.yml).
[Порядок fork и PR](https://github.com/SUKUNA-AI/study-workspace-/blob/main/CONTRIBUTING.md)
также находится в корневом `CONTRIBUTING.md`. Внешние предложения рассматриваются
сопровождающим отдельно от личного прогресса ученика.

Публикация файлов не предоставляет дополнительных лицензионных прав. Авторские и сторонние
права сохраняются; полные книги и закрытые ответы в публичный комплект не входят.
