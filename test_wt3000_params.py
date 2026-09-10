# test_wt3000_params.py - WT3000 参数探测
import sys
import time
import socket

def tmctl_query(sock, cmd, timeout=3):
    def tmctl_send(text):
        text_bytes = text.encode('ascii')
        length = len(text_bytes)
        header = bytes([0x80, 0x00, 0x00, length])
        sock.sendall(header + text_bytes)
    def recv_tmctl(timeout_sec):
        data = b""
        sock.settimeout(timeout_sec)
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except:
            pass
        return data
    def parse_tmctl(data):
        if not data:
            return ""
        results = []
        i = 0
        while i < len(data):
            if i + 4 <= len(data) and data[i] == 0x80:
                length = data[i + 3]
                text = data[i + 4:i + 4 + length]
                if text:
                    results.append(text.decode('ascii', errors='replace'))
                i += 4 + length
            else:
                break
        return "".join(results)
    tmctl_send(cmd)
    time.sleep(0.5)
    data = recv_tmctl(timeout)
    return parse_tmctl(data).strip()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python test_wt3000_params.py <IP>")
        sys.exit(1)
    ip = sys.argv[1]
    print(f"连接到 WT3000 @ {ip}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect((ip, 10001))
    sock.close()
    print("完成")