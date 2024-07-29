FROM ubuntu:22.04

RUN apt update && apt install -y python3-pip

# Environment variables for k8s and Gemini
# ENV KUBECONFIG
# ENV 

ADD . /
RUN pip install -r requirements.txt

CMD kpof run /main.py 