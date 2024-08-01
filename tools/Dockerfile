FROM ubuntu:22.04

RUN apt update
RUN apt upgrade -y
RUN DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC apt-get -y install tzdata

RUN mkdir /app
WORKDIR /app
ADD . /app

RUN pip install --upgrade pip setuptools wheel
RUN pip install -r requirements.txt
RUN chmod +x /app/startup.sh

ENTRYPOINT /app/startup.sh