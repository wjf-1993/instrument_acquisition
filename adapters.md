# 适配器开发指南

> 目录：`adapters/`
> 核心文件：`__init__.py`、`dlm5000_adapter.py`、`wt3000_adapter.py`、`wt1804e_adapter.py`

## 1. 适配器总览

| 适配器 | 仪器 | 通信方式 | 通信协议 | 依赖 |
|--------|------|----------|----------|------|
| `DLM5000Adapter` | DLM5000 示波器 | Ethernet/USB | SCPI (标准) | pyvisa |
| `WT3000Adapter` | WT3000 功率分析仪 | Ethernet | 自定义Socket | socket (标准库) |
| `WT1804EAdapter` | WT1804E 功率分析仪 | Ethernet/USB/GPIB | SCPI (标准) | pyvisa |

## 2. 三台仪器的通信协议差异

这是本项目最关键的技术点——三台仪器的通信方式完全不同：

```
DLM5000  ──→  PyVISA ──→  SCPI标准命令  ──→  简单
WT1804E  ──→  PyVISA ──→  SCPI标准命令  ──→  简单
WT3000   ──→  Socket ──→  自定义编码协议  ──→  复杂！
```

### WT3000 的特殊协议

WT3000 **不使用标准SCPI**，而是使用 Yokogawa 自定义的 Socket 编码协议：

```
消息格式：
┌──────────────────┬─────────────────────┐
│  4字节消息头      │     消息体           │
│                  │                     │
│  Bit31: 标志位    │  ASCII 编码的命令    │
│  Bit0-30: 长度   │                     │
│  (大端序)         │                     │
└──────────────────┴─────────────────────┘

标志位：
  1 = 最后一条消息
  0 = 后续还有消息（长消息会分片）
```

## 3. DLM5000 适配器

### 文件：`adapters/dlm5000_adapter.py`

### 通信参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 连接方式 | PyVISA | 通过 VISA 资源字符串连接 |
| 资源格式 | `TCPIP0::192.168.1.100::INSTR` | Ethernet 连接 |
| 协议 | SCPI | 标准可编程仪器命令 |
| 数据格式 | IEEE 488.2 二进制块 | 波形数据传输 |

### 核心 SCPI 命令

```python
"*IDN?"
":ACQ:STOP"
":ACQ:START"
":ACQ:SRAT <rate>"
":ACQ:MLEN <length>"
":ACQ:RANG <voltage>"
":CHAN<n>:DATA?"
":CHAN<n>:VDIV?"
":CHAN<n>:VOFF?"
":TRIG:SOUR <source>"
":TRIG:LEV <level>"
":TIM:SCAL <timebase>"
"*OPC?"
```

### 数据读取流程

```
1. :ACQ:STOP          → 停止当前采集
2. :ACQ:RANG 5.0      → 设置电压量程
3. :ACQ:SRAT 1000000  → 设置采样率
4. :ACQ:MLEN 100000   → 设置记录长度
5. :ACQ:START         → 开始采集
6. *OPC?              → 等待采集完成
7. :CHAN1:DATA?       → 读取通道1波形
8. 解析二进制数据      → 转换为电压值
```

## 4. WT3000 适配器

### 文件：`adapters/wt3000_adapter.py`

### 通信参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 连接方式 | 原生 Socket | TCP 直连 |
| 默认端口 | 10001 | Yokogawa 默认端口 |
| 协议 | 自定义编码 | 非 SCPI！ |
| 认证 | 密码（可选） | 首次连接需发送密码 |

### 消息编解码

```python
def _encode_message(self, message: str, is_last: bool = True) -> bytes:
    message_bytes = message.encode("ascii")
    length = len(message_bytes)
    flag = 1 if is_last else 0
    header_value = (flag << 31) | length
    header = struct.pack(">I", header_value)
    return header + message_bytes

def _decode_message(self, data: bytes) -> str:
    header_value = struct.unpack(">I", data[:4])[0]
    is_last = bool(header_value >> 31)
    message_length = header_value & 0x7FFFFFFF
    body = data[4:4 + message_length]
    return body.decode("ascii")
```

### 核心命令

```python
"IDENT"
"PASSWORD:<password>"
"STATUS?"
"MEASURE:V:<element>"
"MEASURE:A:<element>"
"MEASURE:W:<element>"
"MEASURE:VAR:<element>"
"MEASURE:PF:<element>"
"MEASURE:WH:<element>"
"MEASURE:FREQ:<element>"
"CURRENT:RANGE <value>"
"VOLTAGE:RANGE <value>"
"WIRING <mode>"
```

### 注意事项

- WT3000 可能将长响应分多个包发送，需要循环接收直到 `is_last=True`
- 连接超时和读取超时需要分别设置
- 密码认证失败会返回非 "OK" 响应

## 5. WT1804E 适配器

### 文件：`adapters/wt1804e_adapter.py`

### 通信参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 连接方式 | PyVISA | 通过 VISA 资源字符串连接 |
| 资源格式 | `TCPIP0::192.168.1.102::INSTR` | Ethernet 连接 |
| 协议 | SCPI | 标准协议 |
| 数值格式 | ASCII | 便于解析 |

### 核心 SCPI 命令

```python
"*IDN?"
":MEASURE:NUMERIC:FORMat ASCII"
":MEASURE:VALUE? <element>,\"<item>\""
":SENSE:VOLTAGE:RANGE <value>"
":SENSE:CURRENT:RANGE <value>"
":INPUT:WIRING <mode>"
":SENSE:AVER:COUN <n>"
":SYNChronize:SOURce <source>"
":SENSE:LFILter:STATe ON|OFF"
```

### 与 WT3000 的对比

| 特性 | WT3000 | WT1804E |
|------|--------|---------|
| 通信库 | socket (标准库) | pyvisa |
| 协议 | 自定义编码 | 标准 SCPI |
| 数据读取 | `MEASURE:V:1` | `:MEASURE:VALUE? 1,"V"` |
| 连接认证 | 需要密码 | 不需要 |
| 响应格式 | 需自定义解码 | 标准 ASCII |
| 开发难度 | 较高 | 较低 |

## 6. 工厂函数

### 文件：`adapters/__init__.py`

```python
from adapters import create_adapter
adapter = create_adapter("dlm5000", config)
adapter = create_adapter("wt3000", config)
adapter = create_adapter("wt1804e", config)
```

### 添加新仪器的步骤

1. 在 `adapters/` 下创建新文件（如 `new_instrument_adapter.py`）
2. 继承 `InstrumentBase`，实现 5 个抽象方法
3. 在 `adapters/__init__.py` 中注册到 `adapter_map`
4. 在 `config.yaml` 中添加仪器配置