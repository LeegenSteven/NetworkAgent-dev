FROM python:3.12
ADD ./build/web/ /dashboard
WORKDIR /dashboard
CMD ["python3", "-m", "http.server","8080"]