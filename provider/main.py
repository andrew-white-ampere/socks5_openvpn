import docker

### Network

VPN_SOCKS_NET = 'vpn_socks_net'
VPN_SOCKS_IPAM = docker.types.IPAMConfig(
	pool_configs = [docker.types.IPAMPool(
		subnet = "10.58.0.0/16",
		iprange  =  "10.58.255.255/16",
		gateway = "10.58.0.1"
	)]
)

def get_vpn_socks_net(client):
	try:
		return client.networks.get(VPN_SOCKS_NET)
	except docker.errors.NotFound:
		return None

def create_vpn_socks_net(client):
	if get_vpn_socks_net(client):
		print(f'Network {VPN_SOCKS_NET} already exists.\nSkipping.')
		return
	client.networks.create(
		VPN_SOCKS_NET,
		driver = 'bridge',
		ipam = VPN_SOCKS_IPAM
	)

###

### Switch

VPN_SOCKS_SWITCH = 'vpn_socks_switch'

def build_vpn_socks_switch(client):
	image, log = client.images.build(
		path='./dockerfiles', 
		dockerfile='Dockerfile-switch', 
		tag=VPN_SOCKS_SWITCH
	)
	return ''.join(str(v) for l in log for v in l.values())

def rebiuld_vpn_socks_switch(client):
	try:
		client.images.get(VPN_SOCKS_SWITCH)
		print(f'Removing {VPN_SOCKS_SWITCH}')
		client.images.remove(VPN_SOCKS_SWITCH)
		log = build_vpn_socks_switch(client)
	except docker.errors.ImageNotFound:
		log = build_vpn_socks_switch(client)
	print(log)

def get_vpn_socks_switch(client):
	try:
		return client.containers.get(VPN_SOCKS_SWITCH)
	except docker.errors.NotFound:
		print(f'{VPN_SOCKS_SWITCH} not found!')
		return None

def restart_vpn_socks_switch(client):
	# Check if switch already running, if so, stop then retry
	if any(c.name == VPN_SOCKS_SWITCH for c in client.containers.list()):
		print(f'Stopping {VPN_SOCKS_SWITCH}')
		socks_switch = get_vpn_socks_switch(client)
		socks_switch.stop(1)
		socks_switch.remove()
		return restart_vpn_socks_switch(client)
	
	# If socks_net does not exist, abort
	socks_net = get_vpn_socks_net(client)
	if not socks_net:
		print(f'{VPN_SOCKS_NET} network does not exist.\nExiting')
		exit(1)

	# Start the container
	switch = client.containers.run(
		VPN_SOCKS_SWITCH,
		name = VPN_SOCKS_SWITCH,
		network = 'default',
		ports = {p: p for p in range(50000, 51000)},
		restart_policy = {'name': 'always'},
		detach = True
	)
	socks_net.connect(
		switch,
		aliases = ['switch'],
		ipv4_address = '10.58.0.2'
	)

def connect_to_switch(client, name, port):
	print(f'Connecting {name} to switch at port {port}')
	switch = get_vpn_socks_switch(client)
	# First ensure that any existing goproxy process on the port is terminated
	switch.exec_run(f"kill -9 $(lsof -t -i:{port} -sTCP:LISTEN | awk '/goproxy/{{print $1}}' | uniq)")
	# Then set up proxy chain to container
	switch.exec_run(f"./goproxy/proxy socks -t tcp -p '0.0.0.0:{port}' -T tcp -P {name}:{port} --log /tmp/{name}.log", detach=True)

def disconnect_all_proxies(client):
	print('Disconnecting all proxies from switch')
	switch = get_vpn_socks_switch(client)
	# Kill any process that is listening on a tcp socket that has "proxy" in the program name
	switch.exec_run(f"""kill -9 $(netstat -tulpn | awk '/proxy/{{split($7, a, "/"); print a[1]}};')""")

###

### vpn 

VPN_SOCKS_PROXY = 'vpn_socks_proxy'

def stop_all_vpn_socks_proxies(client):
	print(f'Stopping vpn proxy containers')
	proxies = [c for c in client.containers.list() if c.name.startswith('vpn_proxy_')]
	for proxy in proxies:
		print(f'Stopping {proxy.name}')
		proxy.stop(timeout=1)
		proxy.remove()

def build_vpn_socks_proxy(client):
	image, log = client.images.build(path='./dockerfiles', dockerfile='Dockerfile-vpn', tag=VPN_SOCKS_PROXY)
	return ''.join(str(v) for l in log for v in l.values())

def run_vpn_socks_proxy(client, **kwargs):
	"""
		required kwargs
			- name: the hostname / label for this socks proxy
			- ovpn: the path to the .ovpn file
			- ovpn_auth: the ovpn auth filename
			- port: the port to be dedicated to this vpn
	"""
	# Check required kwargs
	required_kwargs = ['name', 'ovpn', 'ovpn_auth', 'port']
	missing_kwargs = [k for k in required_kwargs if k not in kwargs]
	if missing_kwargs:
		raise Exception(f'Missing required kwargs: {", ".join(missing_kwargs)}')

	# Unpack kwargs
	name = kwargs.get('name')
	port = kwargs.get('port')
	ovpn = kwargs.get('ovpn')
	ovpn_auth = kwargs.get('ovpn_auth')

	print(f'Starting {name} with port {port}')
	
	# Start the container
	client.containers.run(
		VPN_SOCKS_PROXY,
		name = name,
		network = VPN_SOCKS_NET,
		volumes = ['/home/andrew/code/scratchpad/docker_proxy/vpn:/vpn'], # this volume contains .ovpn files + auth files
		devices = ['/dev/net/tun:/dev/net/tun'], # this device is necessary for openvpn to set up the tunnel
		cap_add = ['NET_ADMIN'], # this is the minimum capability to allow connection to vpn
		sysctls = {'net.ipv6.conf.all.disable_ipv6': 0}, # ipv6 is broken
		restart_policy = {'name': 'always'},
		environment = {
			'OVPN': ovpn,
			'OVPN_AUTH': ovpn_auth,
			'PORT': port
		},
		detach = True
	)

	# Connect to switch
	connect_to_switch(client, name, port)

###


### Run

import os, random

def start_random_proxies(client, limit=5):
	# Stop any running proxies
	stop_all_vpn_socks_proxies(client)
	
	# Get a choice of 5 random servers
	ovpns = os.listdir('./vpn/hma')
	ovpn_choice = [ovpns[random.randint(0, len(ovpns))] for _ in range(limit)]
	configs = [{
		'name': 'vpn_proxy_' + ''.join(c for c in '_'.join(ovpn.split('.')[:2]).lower() if c.isalnum()),
		'ovpn': f'hma/{ovpn}',
		'ovpn_auth': 'hma1',
		'port': i + 50100
	} for i, ovpn in enumerate(ovpn_choice)]

	# Start the proxies
	for config in configs:
		run_vpn_socks_proxy(client, **config)

###



if __name__ == '__main__':
	client = docker.from_env()
	# create_vpn_socks_net(client)
	# restart_vpn_socks_switch(client)
	start_random_proxies(client)