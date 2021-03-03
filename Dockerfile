FROM python:3.6-alpine3.13

RUN export CRYPTOGRAPHY_DONT_BUILD_RUST=1
RUN apk update
RUN apk add --no-cache \
    python3 \
    py3-pip \
    postgresql-dev \
    gcc \
    musl-dev \
    python3-dev \
    curl \
    linux-headers \
    build-base \
    libffi-dev \
    memcached \ 
    rust

RUN mkdir /app
ADD ./ /usr/src/app
WORKDIR /usr/src/app/src

RUN pip3 install -r requirements.txt
RUN curl -OL https://raw.githubusercontent.com/mrako/wait-for/master/wait-for && chmod +x wait-for && chmod +x db-ops.sh
