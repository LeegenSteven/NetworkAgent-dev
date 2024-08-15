FROM python:3.12
ADD ./requirements.txt /
RUN pip install -r /requirements.txt
ADD ./src/ /operator
ADD google-compute /
CMD kopf run /operator/main.py