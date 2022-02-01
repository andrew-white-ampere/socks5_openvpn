import docker

### Network

SOCKS_NET = 'ovpn_socks_net'
ovpn_socks_IPAM = docker.types.IPAMConfig(
	pool_configs = [docker.types.IPAMPool(
		subnet = "10.58.0.0/16",
		iprange  =  "10.58.255.255/16",
		gateway = "10.58.0.1"
	)]
)

def get_ovpn_socks_net(client):
	try:
		return client.networks.get(SOCKS_NET)
	except docker.errors.NotFound:
		return None

def create_ovpn_socks_net(client):
	if get_ovpn_socks_net(client):
		print(f'Network {SOCKS_NET} already exists.\nSkipping.')
		return
	client.networks.create(
		SOCKS_NET,
		driver = 'bridge',
		ipam = ovpn_socks_IPAM
	)

def connect_to_socks_net(client, container):
	socks_net = get_ovpn_socks_net(client)
	if not socks_net:
		print(f'Cannot connect {container} to socks_net, it doesn''t exist')
	socks_net.connect(
		container,
		aliases = [container.name]
	)

###

### Switch

ovpn_socks_SWITCH = 'ovpn_socks_switch'

def build_ovpn_socks_switch(client):
	image, log = client.images.build(
		path='./dockerfiles', 
		dockerfile='Dockerfile-switch', 
		tag=ovpn_socks_SWITCH
	)
	return ''.join(str(v) for l in log for v in l.values())

def rebiuld_ovpn_socks_switch(client):
	print(f'Rebuilding {ovpn_socks_SWITCH}')
	stop_ovpn_socks_switch(client)
	log = build_ovpn_socks_switch(client)
	print(log)

def get_ovpn_socks_switch(client):
	try:
		return client.containers.get(ovpn_socks_SWITCH)
	except docker.errors.NotFound:
		print(f'{ovpn_socks_SWITCH} not found!')
		return None

def stop_ovpn_socks_switch(client):
	print(f'Stopping {ovpn_socks_SWITCH}')
	socks_switch = get_ovpn_socks_switch(client)
	if not socks_switch:
		return
	socks_switch.stop(timeout=1)
	socks_switch.remove()



def restart_ovpn_socks_switch(client):
	# Check if switch already running, if so, stop then retry
	if any(c.name == ovpn_socks_SWITCH for c in client.containers.list()):
		stop_ovpn_socks_switch(client)
		return restart_ovpn_socks_switch(client)

	# If socks_net does not exist, abort
	socks_net = get_ovpn_socks_net(client)
	if not socks_net:
		print(f'{SOCKS_NET} network does not exist.\nExiting')
		exit(1)

	print('Starting vpn socks switch')

	# Start the container
	switch = client.containers.run(
		ovpn_socks_SWITCH,
		name = ovpn_socks_SWITCH,
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
	switch = get_ovpn_socks_switch(client)
	# First ensure that any existing goproxy process on the port is terminated
	switch.exec_run(f"kill -9 $(lsof -t -i:{port} -sTCP:LISTEN | awk '/goproxy/{{print $1}}' | uniq)")
	# Then set up proxy chain to container
	switch.exec_run(f"./goproxy/proxy socks --max-conns-rate=0 -t tcp -p '0.0.0.0:{port}' -T tcp -P {name}:{port} --log /tmp/{name}.log", detach=True)

def disconnect_all_proxies(client):
	print('Disconnecting all proxies from switch')
	switch = get_ovpn_socks_switch(client)
	if not switch:
		print('No switch instance running. Skipping.')
		return
	# Kill any process that is listening on a tcp socket that has "proxy" in the program name
	code, log = switch.exec_run(r"""/bin/sh -c "kill -9 $(netstat -tulpn | awk '/proxy/{split($7, a, \"/\"); print a[1];}')" """)
	print(log)

###

### ovpn 

OVPN_SOCKS_PROXY = 'ovpn_socks_proxy'

def stop_all_ovpn_socks_proxies(client):
	print(f'Stopping vpn proxy containers')
	proxies = [c for c in client.containers.list() if c.name.startswith('vpn_proxy_')]
	for proxy in proxies:
		print(f'Stopping {proxy.name}')
		proxy.stop(timeout=1)
		proxy.remove()

def build_ovpn_socks_proxy(client):
	image, log = client.images.build(path='./dockerfiles', dockerfile='Dockerfile-vpn', tag=OVPN_SOCKS_PROXY)
	return ''.join(str(v) for l in log for v in l.values())

def rebiuld_ovpn_socks_proxy(client):
	try:
		client.images.get(OVPN_SOCKS_PROXY)
		print(f'Removing {OVPN_SOCKS_PROXY}')
		client.images.remove(OVPN_SOCKS_PROXY)
		log = build_ovpn_socks_proxy(client)
	except docker.errors.ImageNotFound:
		log = build_ovpn_socks_proxy(client)
	print(log)

def run_ovpn_socks_proxy(client, **kwargs):
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
		OVPN_SOCKS_PROXY,
		name = name,
		network = SOCKS_NET,
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


### wg

WG_SOCKS_PROXY = 'wg_socks_proxy'

def stop_all_wg_socks_proxies(client):
	print(f'Stopping vpn proxy containers')
	proxies = [c for c in client.containers.list() if c.name.startswith('wg_proxy_')]
	for proxy in proxies:
		print(f'Stopping {proxy.name}')
		proxy.stop(timeout=1)
		proxy.remove()

def build_wg_socks_proxy(client):
	image, log = client.images.build(path='./dockerfiles', dockerfile='Dockerfile-wg', tag=WG_SOCKS_PROXY)
	return ''.join(str(v) for l in log for v in l.values())

def rebiuld_wg_socks_proxy(client):
	try:
		client.images.get(WG_SOCKS_PROXY)
		print(f'Removing {WG_SOCKS_PROXY}')
		client.images.remove(WG_SOCKS_PROXY)
		log = build_wg_socks_proxy(client)
	except docker.errors.ImageNotFound:
		log = build_wg_socks_proxy(client)
	print(log)

def run_wg_socks_proxy(client, **kwargs):
	"""
		required kwargs
			- name: the hostname / label for this socks proxy
			- wg_conf: the path to the wireguard .conf file
			- port: the port to be dedicated to this vpn
	"""
	# Check required kwargs
	required_kwargs = ['name', 'wg_conf', 'port']
	missing_kwargs = [k for k in required_kwargs if k not in kwargs]
	if missing_kwargs:
		raise Exception(f'Missing required kwargs: {", ".join(missing_kwargs)}')

	# Unpack kwargs
	name = kwargs.get('name')
	port = kwargs.get('port')
	wg_conf = kwargs.get('wg_conf')

	print(f'Starting {name} with port {port}')
	
	# Start the container
	client.containers.run(
		WG_SOCKS_PROXY,
		name = name,
		network = SOCKS_NET,
		volumes = ['/home/andrew/code/scratchpad/docker_proxy/vpn:/vpn'], # this volume contains wg conf file
		cap_add = ['NET_ADMIN'], # this is the minimum capability to allow connection to vpn
		sysctls = {'net.ipv6.conf.all.disable_ipv6': 0, 'net.ipv4.conf.all.src_valid_mark': 1}, # ipv6 is broken
		restart_policy = {'name': 'always'},
		environment = {
			'WG_CONF': wg_conf,
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
	stop_all_ovpn_socks_proxies(client)
	
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
		run_ovpn_socks_proxy(client, **config)

def restart_system(client):
	# Stop any running vpn containers
	stop_all_ovpn_socks_proxies(client)
	# Restart switch
	restart_ovpn_socks_switch(client)

###



if __name__ == '__main__':
	client = docker.from_env()
	# restart_system(client)
	# stop_ovpn_socks_switch(client)
	# create_ovpn_socks_net(client)
	# # restart_ovpn_socks_switch(client)
	# stop_all_ovpn_socks_proxies(client)
	# disconnect_all_proxies(client)
	# # rebiuld_ovpn_socks_switch(client)
	
	# # build_ovpn_socks_proxy(client)
	# # start_random_proxies(client, limit=11)
	# # config = {
	# # 	'name': 'vpn_proxy_uk',
	# # 	'ovpn': f'hma/UK.TCP.ovpn',
	# # 	'ovpn_auth': 'hma1',
	# # 	'port': 50100
	# # }
	# # run_ovpn_socks_proxy(client, **config)
	# restart_ovpn_socks_switch(client)
	# wg_1 = client.containers.get('docker_proxy_wg_1_1')
	# connect_to_socks_net(client, wg_1)
	# connect_to_switch(client, 'docker_proxy_wg_1_1', 50102)]
	wg_2 = client.containers.get('docker_proxy_wg_2_1')
	connect_to_socks_net(client, wg_2)
	connect_to_switch(client, 'docker_proxy_wg_2_1', 50104)
	wg_3 = client.containers.get('docker_proxy_wg_3_1')
	connect_to_socks_net(client, wg_3)
	connect_to_switch(client, 'docker_proxy_wg_3_1', 50105)
	wg_4 = client.containers.get('docker_proxy_wg_4_1')
	connect_to_socks_net(client, wg_4)
	connect_to_switch(client, 'docker_proxy_wg_4_1', 50106)
	wg_5 = client.containers.get('docker_proxy_wg_5_1')
	connect_to_socks_net(client, wg_5)
	connect_to_switch(client, 'docker_proxy_wg_5_1', 50107)