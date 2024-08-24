FROM python:3.12
ADD ./requirements.txt /
RUN pip install -r /requirements.txt
ADD ./src/ /tools
WORKDIR /tools
CMD python3 main.py