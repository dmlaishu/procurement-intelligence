# LabProcure API (FastAPI). The Streamlit app is deployed separately on Streamlit Community Cloud.
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt requirements_api.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements_api.txt
COPY notebooks/20_proc_lib.py notebooks/
COPY procurement_demo/ procurement_demo/
COPY api/ api/
ENV PROC_CHAT_LLM=0
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
