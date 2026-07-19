FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml ./
COPY novel_agent ./novel_agent
COPY apps ./apps
RUN pip install --no-cache-dir .

ENV NOVEL_AGENT_HOST=0.0.0.0
ENV NOVEL_AGENT_PORT=8000
EXPOSE 8000
CMD ["python", "-m", "novel_agent", "serve"]

