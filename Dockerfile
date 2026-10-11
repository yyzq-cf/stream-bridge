FROM python:3.12-slim

# 构建时注入版本号
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# 安装ffmpeg和yt-dlp
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg curl && \
    curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp && \
    chmod a+rx /usr/local/bin/yt-dlp && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 写入构建时版本号(只读文件,运行时不再动态生成)
RUN echo "${APP_VERSION}" > /app/VERSION

# 数据目录
RUN mkdir -p /app/data

EXPOSE 5300

ENV PYTHONUNBUFFERED=1

CMD ["gunicorn", "--bind", "0.0.0.0:5300", "--workers", "1", "--threads", "8", "--timeout", "120", "app:app"]
