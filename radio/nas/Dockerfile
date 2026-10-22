FROM python:3.12-slim-bookworm

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl libxml2-utils openssl rclone ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

# 録音は rec_radiko_ts.sh に任せる。radiko の仕様変更で録れなくなったら、
# 作者の修正を待ってこのコミットを新しくし、docker compose up -d --build し直す
ARG REC_RADIKO_TS_COMMIT=65d6a9a51ed220fe85b0fe9e801428e35db2a1f7
RUN curl -fsSL "https://raw.githubusercontent.com/uru2/rec_radiko_ts/${REC_RADIKO_TS_COMMIT}/rec_radiko_ts.sh" \
      -o /usr/local/bin/rec_radiko_ts.sh \
 && chmod +x /usr/local/bin/rec_radiko_ts.sh

ENV TZ=Asia/Tokyo PYTHONUNBUFFERED=1
COPY rec.py /app/rec.py
CMD ["python", "/app/rec.py"]
