FROM cube-sandbox-image.tencentcloudcr.com/opensource/openresty:1.21.4.1-6-alpine-fat

LABEL maintainer="staryxchen <staryxchen@tencent.com>"

RUN mkdir -p /data/log/cube-proxy/ \
    && apk update \
    && apk add tcpdump \
    && apk add vim \
    && apk add tzdata \
    && apk add python3 \
    && apk add py3-pip \
    && apk add bpftrace busybox curl ethtool file gawk inetutils-telnet \
        iperf iperf3 iproute2 netcat-openbsd net-tools sysstat wget \
    && pip3 install pythonping requests \
    && cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo "Asia/Shanghai" > /etc/timezone

WORKDIR /usr/local/openresty/nginx/
COPY lua/ /usr/local/openresty/nginx/lua/
COPY nginx.conf /usr/local/openresty/nginx/conf/nginx.conf
COPY rotate_nginx_log.sh /usr/local/openresty/nginx/sbin/rotate_nginx_log.sh
COPY root /etc/crontabs/root
COPY start.sh /usr/local/openresty/nginx/sbin/start.sh

EXPOSE 8080 8081

STOPSIGNAL SIGQUIT

ENTRYPOINT ["/usr/local/openresty/nginx/sbin/start.sh"]
