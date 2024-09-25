FROM python:3.12
ADD ./requirements.txt /
RUN pip install -r /requirements.txt
ADD ./src/ /agent
WORKDIR /agent
CMD streamlit run main.py