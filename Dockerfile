FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
RUN useradd --create-home --uid 10001 partpilot && mkdir -p state data/raw data_clean reports && chown -R partpilot /app
USER partpilot
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
# first run: generate the seeded data, clean it, create the demo accounts; then start the web UI
CMD ["sh", "-c", "python -m partpilot.bootstrap && exec streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true --browser.gatherUsageStats false"]
