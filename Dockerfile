FROM python:3.12-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir --prefix=/install .


FROM python:3.12-slim

RUN useradd --create-home --shell /bin/bash spatialgrid

COPY --from=builder /install /usr/local

USER spatialgrid
WORKDIR /home/spatialgrid

EXPOSE 8080

CMD ["spatialgrid", "serve-http", "--host", "0.0.0.0", "--port", "8080"]
