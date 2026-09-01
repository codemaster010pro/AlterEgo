FROM python:3.11-slim

RUN pip install --no-cache-dir \
    numpy pandas scipy matplotlib \
    requests httpx beautifulsoup4 pydantic \
    python-dateutil pyyaml pillow tqdm