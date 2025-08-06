FROM python:3.12-alpine

COPY --from=ghcr.io/astral-sh/uv:0.6.9 /uv /uvx /bin/

WORKDIR /bot

ADD . /bot
RUN uv sync --frozen --no-dev

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/bot

CMD ["uv", "run", "python", "src/bot.py"]