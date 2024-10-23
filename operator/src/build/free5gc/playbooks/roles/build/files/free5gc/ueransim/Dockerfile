FROM gcc:9.4.0 AS builder

LABEL maintainer="Free5GC <support@free5gc.org>"

ENV DEBIAN_FRONTEND noninteractive

# Install dependencies
RUN apt-get update \
    && apt-get install libssl-dev wget libsctp-dev lksctp-tools iproute2 git -y 

RUN wget https://github.com/Kitware/CMake/archive/refs/tags/v3.27.0-rc1.tar.gz -O cmake.tar.gz\
    && tar xzvf cmake.tar.gz\
    && cd CMake-3.27.0-rc1\
    && ./bootstrap && make && make install
  

RUN git clone -b master -j `nproc` https://github.com/aligungr/UERANSIM \
    && cd ./UERANSIM \
    && make

FROM bitnami/minideb:bullseye

ENV DEBIAN_FRONTEND noninteractive

# Install runtime dependencies + ping
RUN apt-get update \
    && apt-get install libsctp-dev lksctp-tools iproute2 iputils-ping -y \
    && apt-get clean

WORKDIR /ueransim

RUN mkdir -p config/ binder/

COPY --from=builder /UERANSIM/build/nr-gnb .
COPY --from=builder /UERANSIM/build/nr-ue .
COPY --from=builder /UERANSIM/build/nr-cli .
COPY --from=builder /UERANSIM/build/nr-binder binder/
COPY --from=builder /UERANSIM/build/libdevbnd.so binder/

VOLUME [ "/ueransim/config" ]
