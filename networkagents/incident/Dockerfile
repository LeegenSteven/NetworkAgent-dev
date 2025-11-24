# Stage 1: Build the React frontend
FROM node:18 AS build

WORKDIR /agent

COPY incident/frontend/package.json ./
COPY incident/frontend/package-lock.json ./
RUN npm install

COPY incident/frontend/ ./
RUN npm run build

# Stage 2: Create the final image
FROM python:3.13.2

WORKDIR /agent

COPY --from=build /agent/build ./frontend/build
RUN apt update && apt install git -y

ADD lib/ /lib/
RUN pip install /lib

COPY incident/requirements.txt .
RUN pip install -r requirements.txt

COPY incident/src/ ./src/

EXPOSE 8080

CMD ["python3", "src/main.py"]
