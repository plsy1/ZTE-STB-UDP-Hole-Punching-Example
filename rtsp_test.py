import socket
import urllib.parse
import re
import time
import threading
import os

MAGIC = b'\x52\x7a\xe4\x64'

def main():
    # 1. Read address
    address_file = '/Users/y1/Desktop/test/address'
    try:
        with open(address_file, 'r') as f:
            url = f.read().strip()
            for line in url.split('\n'):
                if line.startswith('rtsp://'):
                    url = line.strip()
                    break
    except FileNotFoundError:
        print(f"Error: Address file not found at {address_file}")
        return
    
    print(f"RTSP URL: {url}")
    
    parsed_url = urllib.parse.urlparse(url)
    server_ip = parsed_url.hostname
    server_port = parsed_url.port if parsed_url.port else 554
    
    local_ip = get_local_ip(server_ip)
    print(f"Server IP: {server_ip}, Port: {server_port}")
    print(f"Local IP: {local_ip}")
    
    # 2. Setup UDP Socket for RTP (using port 21114 as in example)
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp_sock.bind(('', 21114))
        local_port = 21114
        print(f"Successfully bound UDP socket to port 21114")
    except socket.error:
        print(f"Port 21114 is busy, letting OS assign an ephemeral port...")
        udp_sock.bind(('', 0))
        local_port = udp_sock.getsockname()[1]
        print(f"Bound UDP socket to ephemeral port: {local_port}")
    
    # 3. Connect TCP for RTSP
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.connect((server_ip, server_port))
    tcp_local_port = tcp_sock.getsockname()[1]
    print(f"Connected to RTSP server via TCP. Local TCP port: {tcp_local_port}")
    
    cseq = 1
    session_id = None
    server_rtp_port = None
    content_base = url
    
    # --- DESCRIBE ---
    describe_req = (
        f"DESCRIBE {url} RTSP/1.0\r\n"
        f"Accept: application/sdp\r\n"
        f"x-NAT: {local_ip}:{tcp_local_port}\r\n"
        f"Timeshift: 1\r\n"
        f"x-BurstSize: 1048576\r\n"
        f"CSeq: {cseq}\r\n"
        f"User-Agent: HMTL RTSP 1.0; CTC/2.0\r\n"
        f"\r\n"
    )
    print(f"\n>>> Sending DESCRIBE:\n{describe_req}")
    tcp_sock.sendall(describe_req.encode())
    
    response = recv_all(tcp_sock)
    print(f"<<< Received DESCRIBE Response:\n{response}")
    
    # Parse Content-Base
    match = re.search(r'Content-Base:\s*([^\s\r\n]+)', response, re.IGNORECASE)
    if match:
        content_base = match.group(1).strip()
        print(f"Found Content-Base: {content_base}")
    else:
        content_base = f"rtsp://{parsed_url.netloc}{parsed_url.path}"
        if not content_base.endswith('/'):
            content_base += '/'
        print(f"Content-Base not found, using fallback: {content_base}")
    
    # Parse trackID from SDP
    track_id = "trackID=2"
    match = re.search(r'a=control:(trackID=\d+)', response)
    if match:
        track_id = match.group(1)
    elif "a=control:*" in response:
        track_id = "*"
        
    print(f"Found Track ID: {track_id}")
    
    # --- SETUP ---
    cseq += 1
    setup_url = content_base
    if not setup_url.endswith('/') and track_id != "*":
        setup_url += '/'
    if track_id != "*":
        setup_url += track_id
        
    setup_req = (
        f"SETUP {setup_url} RTSP/1.0\r\n"
        f"Transport: MP2T/RTP/UDP;unicast;client_address={local_ip};client_port={local_port}-{local_port+1};mode=PLAY\r\n"
        f"x-NAT: {local_ip}:{tcp_local_port}\r\n"
        f"CSeq: {cseq}\r\n"
        f"User-Agent: HMTL RTSP 1.0; CTC/2.0\r\n"
        f"\r\n"
    )
    print(f"\n>>> Sending SETUP:\n{setup_req}")
    tcp_sock.sendall(setup_req.encode())
    
    response = recv_all(tcp_sock)
    print(f"<<< Received SETUP Response:\n{response}")
    
    # Parse Session ID
    match = re.search(r'Session:\s*([^\s;]+)', response)
    if match:
        session_id = match.group(1)
    print(f"Session ID: {session_id}")
    
    # Parse Server RTP Port
    match = re.search(r'server_port=(\d+)', response)
    if match:
        server_rtp_port = int(match.group(1))
    print(f"Server RTP Port: {server_rtp_port}")
    
    if not server_rtp_port:
        print("Could not find server RTP port in SETUP response. Aborting.")
        return
        
    # --- UDP Hole Punching (ZTE Heartbeat) ---
    print(f"\n--- Performing UDP Hole Punching (ZTE Heartbeat) ---")
    heartbeat_payload = get_heartbeat_payload(local_ip)
    print(f"Sending ZTE heartbeat packet (84 bytes) from local port {local_port} to {server_ip}:{server_rtp_port}")
    udp_sock.sendto(heartbeat_payload, (server_ip, server_rtp_port))
    
    # --- PLAY ---
    cseq += 1
    play_url = content_base
    if not play_url.endswith('/'):
        play_url += '/'
        
    play_req = (
        f"PLAY {play_url} RTSP/1.0\r\n"
        f"Range: clock=end-\r\n"
        f"x-BurstSize: 1048576\r\n"
        f"Scale: 1.0\r\n"
        f"CSeq: {cseq}\r\n"
        f"User-Agent: HMTL RTSP 1.0; CTC/2.0\r\n"
        f"Session: {session_id}\r\n"
        f"\r\n"
    )
    print(f"\n>>> Sending PLAY:\n{play_req}")
    tcp_sock.sendall(play_req.encode())
    
    response = recv_all(tcp_sock)
    print(f"<<< Received PLAY Response:\n{response}")
    
    # --- Start Receiving Thread ---
    stop_event = threading.Event()
    # We use the SAME UDP socket to receive the RTP stream
    recv_thread = threading.Thread(target=receive_udp, args=(udp_sock, stop_event))
    recv_thread.start()
    
    # --- Keep-Alive Loop ---
    try:
        while True:
            time.sleep(30)
            cseq += 1
            keep_alive_req = (
                f"GET_PARAMETER {play_url} RTSP/1.0\r\n"
                f"CSeq: {cseq}\r\n"
                f"User-Agent: HMTL RTSP 1.0; CTC/2.0\r\n"
                f"Session: {session_id}\r\n"
                f"\r\n"
            )
            print(f"\n>>> Sending Keep-Alive (CSeq {cseq}):\n{keep_alive_req}")
            tcp_sock.sendall(keep_alive_req.encode())
            response = recv_all(tcp_sock)
            print(f"<<< Received Keep-Alive Response:\n{response}")
            
            # Send heartbeat again to keep NAT open
            udp_sock.sendto(heartbeat_payload, (server_ip, server_rtp_port))
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        stop_event.set()
        udp_sock.close()
        tcp_sock.close()
        recv_thread.join()

def get_local_ip(server_ip):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((server_ip, 1))
        IP = s.getsockname()[0]
    except Exception:
        IP = '127.0.0.1'
    finally:
        s.close()
    return IP

def get_heartbeat_payload(ip_str):
    # 'ZXV10STB' + \x7f\xff\xff\xff
    header = b'ZXV10STB\x7f\xff\xff\xff'
    # IP in hex
    ip_bytes = bytes(int(x) for x in ip_str.split('.'))
    # Padding
    padding = b'\x00' * 64
    return header + ip_bytes + MAGIC + padding

def recv_all(sock):
    sock.settimeout(5.0)
    data = b""
    try:
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\r\n\r\n" in data:
                match = re.search(rb'Content-Length:\s*(\d+)', data, re.IGNORECASE)
                if match:
                    content_length = int(match.group(1))
                    header_end = data.find(b"\r\n\r\n") + 4
                    while len(data) < header_end + content_length:
                        chunk = sock.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                break
    except socket.timeout:
        pass
    return data.decode('utf-8', errors='ignore')

def receive_udp(sock, stop_event):
    sock.settimeout(1.0)
    packet_count = 0
    start_time = time.time()
    
    print("Listening for UDP packets (RTP stream)...")
    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(2048)
            packet_count += 1
            if packet_count % 100 == 0 or packet_count == 1:
                print(f"[{time.time() - start_time:.2f}s] Received {packet_count} packets. Last from {addr}, size: {len(data)}")
        except socket.timeout:
            continue
        except Exception as e:
            if not stop_event.is_set():
                print(f"UDP Recv Error: {e}")
            break
    print(f"UDP Receiver stopped. Total packets: {packet_count}")

if __name__ == "__main__":
    main()
