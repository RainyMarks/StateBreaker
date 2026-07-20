FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    && useradd --create-home --uid 10001 labuser

COPY --chown=labuser:labuser bank_lab ./bank_lab

USER labuser
EXPOSE 8000

CMD ["uvicorn", "bank_lab.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]