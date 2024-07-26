FROM python:3.12
ADD . /src
RUN pip install -r src/requirements.txt
CMD kopf run /src/main.py --verbose