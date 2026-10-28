FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN python -m pip install --no-cache-dir "reportlab>=4,<5" "pypdf>=5,<7" \
    && useradd --create-home --uid 10001 brit \
    && mkdir -p /app/data \
    && chown -R brit:brit /app
USER brit
ENV PLACEMENT_HOST=0.0.0.0 PLACEMENT_PORT=8765
EXPOSE 8765
CMD ["python", "app.py", "serve"]
