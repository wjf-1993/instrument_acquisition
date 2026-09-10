# _write_script.py
content = r'''
import sys
import time
try:
    import vxi11
except ImportError:
    print("错误：请先安装 vxi11")
    sys.exit(1)

def query_cmd(inst, cmd):
    try:
        inst.write(cmd + "\n")
        time.sleep(0.5)
        try:
            inst.read_raw()
        except Exception:
            pass
        time.sleep(0.2)
        raw = inst.read_raw()
        if raw:
            return raw.decode('ascii', errors='replace').strip()
    except Exception:
        pass
    return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python test_power_filename.py <IP>")
        sys.exit(1)
    ip = sys.argv[1]
    inst = vxi11.Instrument(ip)
    inst.timeout = 5000
    idn = inst.ask("*IDN?")
    print(f"连接成功：{idn.strip()}")
    inst.close()
'''
with open(r'test_power_filename.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('文件已写入')