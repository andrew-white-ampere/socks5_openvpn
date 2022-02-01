#!/bin/sh

openvpn --config /vpn/$OVPN --auth-user-pass /vpn/secret/$OVPN_AUTH.txt --daemon

./goproxy/proxy socks --max-conns-rate=0 -t tcp -p "0.0.0.0:$PORT"