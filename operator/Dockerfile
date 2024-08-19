FROM python:3.12
ADD ./requirements.txt /
RUN pip install -r /requirements.txt
ADD ./src/ /operator
ADD google-compute /operator
ADD google-compute.pub /operator
ADD networkagent.json /operator
CMD kopf run /operator/main.py