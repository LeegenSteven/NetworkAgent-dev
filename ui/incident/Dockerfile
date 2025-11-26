# Stage 1: Build the React frontend
FROM node:18 AS build

WORKDIR /agent

COPY ui/incident/frontend/package.json ./
COPY ui/incident/frontend/package-lock.json ./
RUN npm install

COPY ui/incident/frontend/ ./
RUN npm run build

# Stage 2: Create the final image
FROM python:3.13.2

WORKDIR /agent

COPY --from=build /agent/build ./frontend/build
RUN apt update && apt install git -y

ADD lib/ /lib/
RUN pip install /lib

COPY ui/incident/requirements.txt .
RUN pip install -r requirements.txt

COPY ui/incident/src/ ./src/

EXPOSE 8080

CMD ["python3", "src/main.py"]
