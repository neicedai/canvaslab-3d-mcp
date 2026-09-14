FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY server3d/requirements.txt /app/server3d/requirements.txt
COPY gpu3d/requirements*.txt /app/gpu3d/
RUN python -m pip install --no-cache-dir -r gpu3d/requirements-p4.txt
COPY server3d /app/server3d
COPY scripts3d /app/scripts3d
COPY gpu3d /app/gpu3d
# No network port; workers share the local disk queue with the existing MCP.
CMD ["python", "-m", "gpu3d.worker", "--help"]
