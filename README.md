# Binance P2P Monitoring Telegram Bot

Production-ready Telegram-бот для автоматического мониторинга мерчантов и объявлений Binance P2P, извлечения контактов и предотвращения спама уведомлений.

## 🌟 Основные Возможности

- 🚀 **Прямой HTTP Клиент Binance P2P**: Быстрое получение объявлений с автоматическим ротированием User-Agent, пагинацией и обработкой Rate Limit (HTTP 429).
- 📞 **Извлечение Контактов (Contact Extractor)**: Регулярные выражения для поиска Telegram (`@username`, `t.me/...`), WhatsApp (`wa.me/...`), телефонов, Viber, Instagram, Email из ника, условий и автоответов.
- 🛡 **Дедупликация & База Данных**: Асинхронный SQLAlchemy 2.x + SQLite (WAL mode) / PostgreSQL ready. Дедупликация мерчантов по уникальному Binance `userNo`.
- 🔇 **Первоначальный Baseline (Без Спама)**: При первом запуске профиля создается базовая линия. Уведомления высылаются только при появлении **новых** мерчантов или **новых** контактов.
- 📊 **Google Sheets Интеграция**: Асинхронная очередь выгрузки мерчантов в Google Таблицы.
- 📱 **Telegram Admin Panel (aiogram 3.x)**:
  - Управление профилями мониторинга (создание, пауза, удаление)
  - Ручной скан
  - Поиск по мерчантам и карточки мерчантов
  - Дашборд показателей
  - Просмотр логов в реальном времени (`/logs`)
  - Экспорт базы в CSV и горячее скачивание бэкапа БД (`/backup`)
- 🐧 **Без Docker**: Развертывание через системную службу `systemd` на Ubuntu VPS или локально на Windows.

---

## 🛠 Технологический Стек

- **Python 3.12+**
- **aiogram 3.x**
- **SQLAlchemy 2.x async + aiosqlite**
- **Alembic**
- **httpx**
- **Pydantic v2 & pydantic-settings**
- **APScheduler**
- **pytest & Ruff**

---

## 🚀 Быстрый запуск

Подробные инструкции по установке на Windows и Ubuntu доступны в файле [INSTALLATION.md](file:///d:/боты%20пайтоны/Binance%20bot/INSTALLATION.md).

```bash
# 1. Склонировать репозиторий и настроить venv
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -r requirements.txt

# 2. Скопировать .env и указать BOT_TOKEN
cp .env.example .env

# 3. Применить миграции
alembic upgrade head

# 4. Запустить бота
python run.py
```

---

## ⚙️ Дефолтные Настройки Доступа

- **Администратор по умолчанию:** `@sergebybitp2p`
- **Разрешенный Chat ID:** `930460307`
- **Часовой пояс:** `Europe/Kyiv`
- **База данных:** `sqlite+aiosqlite:///./data/bot.db`
