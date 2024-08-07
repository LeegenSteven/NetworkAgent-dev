FROM python:3.12
ADD ./requirements.txt /
RUN pip install -r requirements.txt
ADD . /
CMD kopf run /main.py --verbose