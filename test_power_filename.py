# test_power_filename.py - 验证仪器 ITEM32/34 配置和采集流程
import sys, time, json
try:
    import vxi11
except ImportError:
    print("错误：请先安装 vxi11")
    sys.exit(1)

def _clear(inst):
    try:
        while True:
            raw = inst.read_raw()
            if raw is None or len(raw) == 0:
                break
    except: pass

def _query_str(inst, cmd):
    _clear(inst)
    inst.write(cmd + "\n")
    time.sleep(0.3)
    try:
        raw = inst.read_raw()
        if raw:
            return raw.decode('ascii').strip()
    except: pass
    return None

def _send(inst, cmd):
    inst.write(cmd + "\n")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python test_power_filename.py <IP>")
        sys.exit(1)
    ip = sys.argv[1]
    print(f"连接到 {ip}...")
    inst = vxi11.Instrument(ip)
    inst.timeout = 15000
    print(f"连接成功: {inst.ask('*IDN?').strip()}")
    inst.close()