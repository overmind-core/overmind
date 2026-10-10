FROM python:3.13-slim-bookworm
COPY runner.py /runner.py
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-I", "/runner.py"]
