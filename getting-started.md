# 快速开始指南

## 1. 环境准备

### 1.1 Python 版本

要求 Python 3.10 或更高版本。

```bash
python --version
```

### 1.2 安装依赖

```bash
pip install pyyaml
pip install pyvisa pyvisa-py
```

### 1.3 验证安装

```bash
python -c "import yaml; print('pyyaml OK')"
python -c "import pyvisa; print('pyvisa OK')"
```

## 2. 项目结构确认

```
instrument_acquisition/
├── config.yaml
├── main.py
├── instrument_interface.py
├── acquisition_scheduler.py
├── config.py
├── logger.py
└── adapters/
    ├── __init__.py
    ├── dlm5000_adapter.py
    ├── wt3000_adapter.py
    └── wt1804e_adapter.py
```

## 3. 演示模式运行（无需仪器）

```bash
cd instrument_acquisition
python main.py --demo
```

## 4. 连接真实仪器

### 4.1 网络配置

```bash
ping 192.168.1.100
ping 192.168.1.101
ping 192.168.1.102
```

### 4.2 修改配置文件

```yaml
instruments:
  dlm5000:
    visa_resource: "TCPIP0::实际IP::INSTR"
  wt3000:
    host: "实际IP"
  wt1804e:
    visa_resource: "TCPIP0::实际IP::INSTR"
```

### 4.3 单次采集测试

```bash
python main.py --once
```

### 4.4 持续采集

```bash
python main.py
```

## 5. 第零阶段：通信 PoC 验证

### 5.1 验证 DLM5000（PyVISA + SCPI）

```python
import pyvisa
rm = pyvisa.ResourceManager()
inst = rm.open_resource("TCPIP0::192.168.1.100::INSTR")
inst.timeout = 5000
print(inst.query("*IDN?"))
inst.close()
rm.close()
```

### 5.2 验证 WT3000（自定义 Socket）

```python
import socket
import struct
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(5)
sock.connect(("192.168.1.101", 10001))
msg = "IDENT"
msg_bytes = msg.encode("ascii")
header = struct.pack(">I", (1 << 31) | len(msg_bytes))
sock.sendall(header + msg_bytes)
response = sock.recv(4096)
print(response)
sock.close()
```

### 5.3 验证 WT1804E（PyVISA + SCPI）

```python
import pyvisa
rm = pyvisa.ResourceManager()
inst = rm.open_resource("TCPIP0::192.168.1.102::INSTR")
inst.timeout = 5000
print(inst.query("*IDN?"))
inst.write(":MEASURE:NUMERIC:FORMat ASCII")
print(inst.query(':MEASURE:VALUE? 1,"V"'))
inst.close()
rm.close()
```

### 5.4 PoC 验收标准

| 仪器 | 验收条件 |
|------|----------|
| DLM5000 | `*IDN?` 返回型号信息，可读取波形数据 |
| WT3000 | `IDENT` 返回型号信息，可读取测量值 |
| WT1804E | `*IDN?` 返回型号信息，可读取测量值 |

## 6. 常见问题

### Q: PyVISA 找不到仪器？

```bash
python -c "import pyvisa; rm = pyvisa.ResourceManager(); print(rm.list_resources())"
```

### Q: WT3000 连接超时？

1. 确认端口号（默认 10001）
2. 检查是否有防火墙阻挡
3. 确认仪器已开启 Ethernet 通信功能

## 7. 下一步

1. ✅ 抽象接口层 + 适配器（已完成）
2. ✅ 并发采集调度器（已完成）
3. ⏳ 数据存储模块（data_storage.py）
4. ⏳ 报告生成模块（report_generator.py）
5. ⏳ UI 界面（ui_design.py）
6. ⏳ 全流程整合测试