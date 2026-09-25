FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Папка для базы данных — подключи её как persistent volume на хостинге,
# иначе данные (файлы, настройки, счётчик пользователей) будут теряться при редеплое.
RUN mkdir -p /app/data

CMD ["python", "bot.py"]
