FROM python:3.12

WORKDIR /dashboard
COPY ui/dashboard/build/web/ .
COPY ui/dashboard/server.py .

# CMD ["python3", "-m", "http.server","8080"]
CMD ["python3", "server.py"]