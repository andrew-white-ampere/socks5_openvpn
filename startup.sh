#!/bin/sh
echo "root:$ROOT_PASS" | chpasswd

rc-status
touch /run/openrc/softlevel
rc-update add sshd
cp /etc/ssh/sshd_config_set /etc/ssh/sshd_config
rc-service sshd restart



openvpn --config /$OVPN.ovpn --auth-user-pass /hmapass.txt