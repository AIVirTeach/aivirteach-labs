FROM ghcr.io/astral-sh/uv:0.12.3-python3.13-trixie-slim@sha256:5f3c58899cb4ab5b723f81641d6aed08968e6c93f9a84641321ae66ba7103f42

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libvirt-clients \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN uv pip install --system --no-cache --requirement requirements.txt

COPY service.py ./service.py
COPY libvirt/config ./libvirt/config
COPY libvirt/scripts ./libvirt/scripts

EXPOSE 8760

CMD ["python", "-m", "uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8760", "--no-access-log"]
