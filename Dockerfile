FROM python:3.12-slim
WORKDIR /app
COPY requisitos.txt .
RUN pip install --no-cache-dir -r requisitos.txt
COPY . .
RUN chmod +x start_server.sh configure_gemini.sh
EXPOSE 8000
CMD ["./start_server.sh"]
