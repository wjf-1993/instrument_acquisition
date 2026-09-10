# test_filename.py - 测试 WT3000 文件名相关命令
import socket
import sys
import time

IP = "172.17.76.203"
PORT = 10001

def tmctl_send(sock, text):
    data = text.encode('ascii')
    sock.sendall(bytes([0x80, 0x00, 0x00, len(data)]) + data)

def tmctl_recv(sock, timeout=2):
    data = b""
    sock.settimeout(timeout)
    try:
        while True:
            data += sock.recv(4096)
    except:
        pass
    return data

def parse_tmctl(data):
    if not data:
        return ""
    parts, i = [], 0
    while i < len(data):
        if i + 4 <= len(data) and data[i] == 0x80:
            n = data[i + 3]
            parts.append(data[i+4:i+4+n].decode('ascii', errors='replace'))
            i += 4 + n
        else:
            parts.append(data[i:].decode('ascii', errors='replace'))
            break
    return "".join(parts)

def query(sock, cmd):
    tmctl_send(sock, cmd)
    time.sleep(0.3)
    return parse_tmctl(tmctl_recv(sock, 2)).strip()

if __name__ == "__main__":
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((IP, PORT))
    tmctl_recv(sock, 5)
    tmctl_send(sock, "anonymous")
    r = tmctl_recv(sock, 3)
    if b"password" in r:
        tmctl_send(sock, "")
        tmctl_recv(sock, 3)
    print(f"IDN: {query(sock, '*IDN?')}")
    sock.close()