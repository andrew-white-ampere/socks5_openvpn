#!/bin/sh

wg-quick up $WG_CONF

./goproxy/proxy socks --max-conns-rate=0 -t tcp -p "0.0.0.0:$PORT"