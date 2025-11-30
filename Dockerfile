# Многоступенчатая сборка для Python приложения
# Этап 1: Сборка зависимостей
FROM python:3.11-slim as builder

WORKDIR /app

# Установка системных зависимостей для сборки
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копирование файла зависимостей
COPY requirements.txt .

# Установка Python зависимостей в виртуальное окружение
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Этап 2: Финальный образ на базе Distroless
FROM gcr.io/distroless/python3-debian12:nonroot

# Копирование виртуального окружения из builder
COPY --from=builder /opt/venv /opt/venv

# Установка переменных окружения
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Копирование кода приложения
COPY --chown=nonroot:nonroot main.py /app/main.py

WORKDIR /app

# Использование nonroot пользователя (уже установлен в distroless)
USER nonroot

# Запуск приложения
CMD ["main.py"]

