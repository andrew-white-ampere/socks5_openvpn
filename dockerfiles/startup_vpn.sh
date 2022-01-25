#!/bin/sh

openvpn --config /vpn/$OVPN --auth-user-pass /vpn/secret/$OVPN_AUTH.txt --daemon

./goproxy/proxy socks -t tcp -p "0.0.0.0:$PORT"