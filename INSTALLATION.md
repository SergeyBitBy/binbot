# Инструкция по развертыванию и запуску (Windows & Ubuntu)

Этот проект разработан без использования Docker. Ниже приведены пошаговые инструкции для локального запуска на Windows и production развертывания на Ubuntu VPS.

---

## 1. Локальная разработка (Windows)

### Требования:
- Python 3.12+

### Шаги установки:

1. **Создание виртуального окружения:**
   ```cmd
   python -m venv .venv
   .venv\Scripts\activate
   ```

2. **Установка зависимостей:**
   ```cmd
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Настройка переменных окружения:**
   Скопируйте пример конфигурации:
   ```cmd
   copy .env.example .env
   ```
   Откройте файл `.env` и укажите ваш токен Telegram бота (`BOT_TOKEN`).

4. **Выполнение миграций базы данных (Alembic):**
   ```cmd
   python -m alembic upgrade head
   ```

5. **Тестирование подключения к Binance P2P (Диагностический скрипт):**
   ```cmd
   python scripts/test_binance.py
   ```

6. **Запуск тестов:**
   ```cmd
   python -m pytest
   ```

7. **Запуск бота:**
   ```cmd
   python run.py
   ```

---

## 2. Production Deployment (Ubuntu 22.04 / 24.04 VPS)

### Требования:
- Python 3.12+
- `systemd`

### Пошаговое развертывание:

1. **Клонирование/копирование проекта в директорию `/opt/binance-p2p-monitor`:**
   ```bash
   sudo mkdir -p /opt/binance-p2p-monitor
   sudo chown -R ubuntu:ubuntu /opt/binance-p2p-monitor
   cd /opt/binance-p2p-monitor
   ```

2. **Создание venv и установка зависимостей:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Создание и заполнение файла `.env`:**
   ```bash
   cp .env.example .env
   nano .env
   ```

4. **Применение миграций БД:**
   ```bash
   alembic upgrade head
   ```

5. **Установка и запуск systemd службы:**
   ```bash
   sudo cp deploy/binance-p2p-monitor.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable binance-p2p-monitor
   sudo systemctl start binance-p2p-monitor
   ```

6. **Проверка статуса и логов:**
   ```bash
   sudo systemctl status binance-p2p-monitor
   journalctl -u binance-p2p-monitor -f
   ```
