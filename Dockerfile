# Используем официальный легковесный образ Python
FROM python:3.11-slim

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем файл зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь оставшийся код проекта
COPY . .

# Команда по умолчанию (будет переопределяться Procfile'ом на хостинге)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
