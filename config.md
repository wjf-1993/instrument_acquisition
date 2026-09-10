# 配置参数详解

> 文件：`config.yaml`（配置）、`config.py`（解析器）

## 1. 配置文件结构

```yaml
config.yaml
├── logging              # 日志配置
├── instruments          # 仪器连接配置
│   ├── dlm5000         #   DLM5000 示波器
│   ├── wt3000          #   WT3000 功率分析仪
│   └── wt1804e         #   WT1804E 功率分析仪
├── acquisition         # 采集调度配置
├── storage             # 数据存储配置
└── report              # 报告生成配置
```

## 2. 日志配置

```yaml
logging:
  level: "INFO"
  log_dir: "logs"
  log_file: "acquisition.log"
  max_bytes: 10485760
  backup_count: 5
  console_output: true
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `level` | string | `"INFO"` | DEBUG / INFO / WARNING / ERROR / CRITICAL |
| `log_dir` | string | `"logs"` | 日志文件存储目录 |
| `log_file` | string | `"acquisition.log"` | 日志文件名 |
| `max_bytes` | int | `10485760` | 单个日志文件最大字节数，超过自动轮转 |
| `backup_count` | int | `5` | 保留的历史日志文件数量 |
| `console_output` | bool | `true` | 是否同时在控制台输出日志 |

## 3. 仪器配置

### 3.1 DLM5000 示波器

```yaml
instruments:
  dlm5000:
    enabled: true
    name: "DLM5000"
    connection_type: "visa"
    visa_resource: "TCPIP0::192.168.1.100::INSTR"
    timeout: 5000
    channels: [1, 2, 3, 4]
    sample_rate: 1000000
    record_length: 100000
    voltage_range: 5.0
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `enabled` | bool | 是否启用此仪器 |
| `name` | string | 仪器显示名称 |
| `connection_type` | string | 固定为 `"visa"` |
| `visa_resource` | string | PyVISA 资源字符串 |
| `timeout` | int | 连接超时（毫秒） |
| `channels` | list[int] | 默认采集通道列表 |
| `sample_rate` | int | 默认采样率（Hz） |
| `record_length` | int | 默认记录长度（点数） |
| `voltage_range` | float | 默认电压量程（V） |

**VISA 资源字符串格式：**
```
TCPIP0::<IP地址>::INSTR     # Ethernet 连接
USB0::<厂商ID>::<型号>::<序列号>::INSTR  # USB 连接
GPIB0::<板卡号>::<地址>::INSTR  # GPIB 连接
```

### 3.2 WT3000 功率分析仪

```yaml
instruments:
  wt3000:
    enabled: true
    name: "WT3000"
    connection_type: "socket"
    host: "192.168.1.101"
    port: 10001
    timeout: 5000
    password: ""
    protocol:
      header_size: 4
      encoding: "ascii"
    measurement_items:
      - "V"
      - "A"
      - "W"
      - "VAR"
      - "PF"
      - "WH"
      - "FREQ"
    elements: [1, 2, 3, 4]
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `connection_type` | string | 固定为 `"socket"`（WT3000 不用 VISA） |
| `host` | string | 仪器 IP 地址 |
| `port` | int | 通信端口（默认 10001） |
| `password` | string | 连接密码（无密码则留空） |
| `protocol.header_size` | int | 消息头字节数（固定为 4） |
| `protocol.encoding` | string | 消息编码（固定为 "ascii"） |
| `measurement_items` | list[str] | 要采集的测量项 |
| `elements` | list[int] | 测量单元编号 |

**可用测量项：**
| 代码 | 说明 | 单位 |
|------|------|------|
| `V` | 电压 | V |
| `A` | 电流 | A |
| `W` | 有功功率 | W |
| `VAR` | 无功功率 | VAR |
| `VA` | 视在功率 | VA |
| `PF` | 功率因数 | — |
| `WH` | 电能 | Wh |
| `FREQ` | 频率 | Hz |

### 3.3 WT1804E 功率分析仪

```yaml
instruments:
  wt1804e:
    enabled: true
    name: "WT1804E"
    connection_type: "visa"
    visa_resource: "TCPIP0::192.168.1.102::INSTR"
    timeout: 5000
    measurement_items:
      - "V"
      - "A"
      - "W"
      - "VAR"
      - "PF"
      - "WH"
      - "FREQ"
      - "IH"
      - "VH"
    elements: [1, 2, 3, 4]
```

> WT1804E 额外支持 `IH`（谐波电流）和 `VH`（谐波电压）测量项。

## 4. 采集调度配置

```yaml
acquisition:
  mode: "sync"
  interval: 1.0
  max_retries: 3
  retry_delay: 1.0
  timeout_per_instrument: 10
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `mode` | string | `"sync"` | `"sync"`=并发同步 / `"sequential"`=顺序 |
| `interval` | float | `1.0` | 采集间隔（秒） |
| `max_retries` | int | `3` | 单台仪器失败最大重试次数 |
| `retry_delay` | float | `1.0` | 重试间隔（秒） |
| `timeout_per_instrument` | int | `10` | 单台仪器采集超时（秒） |

## 5. 数据存储配置

```yaml
storage:
  db_type: "sqlite"
  db_path: "data/acquisition.db"
  waveform_format: "hdf5"
  waveform_dir: "data/waveforms"
  auto_backup: true
  backup_interval: 3600
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `db_type` | string | 数据库类型（当前仅支持 sqlite） |
| `db_path` | string | 数据库文件路径 |
| `waveform_format` | string | 波形数据格式（hdf5 / csv） |
| `waveform_dir` | string | 波形文件存储目录 |
| `auto_backup` | bool | 是否自动备份数据库 |
| `backup_interval` | int | 备份间隔（秒） |

## 6. 报告生成配置

```yaml
report:
  template_dir: "templates"
  output_dir: "data/reports"
  default_format: "docx"
  auto_timestamp: true
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `template_dir` | string | 报告模板目录 |
| `output_dir` | string | 报告输出目录 |
| `default_format` | string | 默认报告格式（docx / xlsx / pdf） |
| `auto_timestamp` | bool | 文件名是否自动添加时间戳 |

## 7. 在代码中使用配置

```python
from config import load_config, get_config

config = load_config("config.yaml")
print(config.logging.level)
print(config.instruments["dlm5000"].visa_resource)
print(config.acquisition.mode)
config = get_config()
```