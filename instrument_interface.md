# 抽象接口层说明

> 文件：`instrument_interface.py`
> 依赖：无（零依赖核心模块）

## 1. 设计目标

定义所有仪器必须实现的统一接口，使上层模块（存储、报告、UI）完全解耦于具体仪器型号。

## 2. 核心类一览

| 类名 | 类型 | 说明 |
|------|------|------|
| `InstrumentBase` | 抽象基类 | 所有适配器必须继承 |
| `InstrumentData` | 数据容器 | 标准化的采集数据格式 |
| `WaveformChannel` | 数据容器 | 单通道波形数据 |
| `PowerMeasurement` | 数据容器 | 单测量单元功率参数 |
| `InstrumentType` | 枚举 | 仪器类型（示波器/功率分析仪） |
| `ConnectionStatus` | 枚举 | 连接状态 |

## 3. InstrumentBase 抽象基类

### 3.1 必须实现的抽象方法

```python
class InstrumentBase(ABC):
    @abstractmethod
    def connect(self) -> bool:
        """连接仪器，返回是否成功"""
    @abstractmethod
    def disconnect(self) -> bool:
        """断开连接，返回是否成功"""
    @abstractmethod
    def read_data(self) -> InstrumentData:
        """读取一次采集数据，返回标准化数据容器"""
    @abstractmethod
    def configure(self, **params) -> bool:
        """配置仪器参数，返回是否成功"""
    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """获取仪器当前状态"""
```

### 3.2 已提供的内置方法（子类可直接使用）

```python
instrument_id: str
instrument_type: InstrumentType
connection_status: ConnectionStatus
last_error: Optional[str]
is_connected: bool
_set_connected()
_set_disconnected()
_set_error(error_msg)
_require_connected()
get_info() -> Dict[str, str]
reset() -> bool
self_test() -> bool
```

### 3.3 创建新适配器的模板

```python
from instrument_interface import InstrumentBase, InstrumentData, InstrumentType

class MyNewInstrumentAdapter(InstrumentBase):
    def __init__(self, instrument_id: str, config: object):
        super().__init__(instrument_id, InstrumentType.POWER_ANALYZER)
        self._config = config
    def connect(self) -> bool:
        try:
            self._set_connected()
            return True
        except Exception as e:
            self._set_error(str(e))
            return False
    def disconnect(self) -> bool:
        self._set_disconnected()
        return True
    def read_data(self) -> InstrumentData:
        self._require_connected()
        return InstrumentData(
            instrument_id=self._instrument_id,
            instrument_type=InstrumentType.POWER_ANALYZER,
        )
    def configure(self, **params) -> bool:
        self._require_connected()
        return True
    def get_status(self) -> Dict[str, Any]:
        return {"connection": self._connection_status.value}
```

## 4. 数据容器详解

### 4.1 InstrumentData — 采集结果

```python
@dataclass
class InstrumentData:
    instrument_id: str
    instrument_type: InstrumentType
    timestamp: float
    datetime_str: str
    waveform_channels: List[WaveformChannel]
    power_measurements: List[PowerMeasurement]
    raw_data: Optional[bytes]
    raw_data_path: Optional[str]
    metadata: Dict[str, Any]
```

### 4.2 WaveformChannel — 波形通道

```python
@dataclass
class WaveformChannel:
    channel_id: int
    channel_name: str
    voltage_data: List[float]
    time_data: List[float]
    sample_rate: float
    voltage_range: float
    unit: str
```

### 4.3 PowerMeasurement — 功率测量

```python
@dataclass
class PowerMeasurement:
    element_id: int
    voltage: float
    current: float
    active_power: float
    reactive_power: float
    apparent_power: float
    power_factor: float
    energy: float
    frequency: float
    extra: Dict[str, float]
```

## 5. 上层模块使用方式

```python
from instrument_interface import InstrumentBase, InstrumentData

def process_data(instrument: InstrumentBase):
    if not instrument.is_connected:
        instrument.connect()
    data: InstrumentData = instrument.read_data()
    if data.waveform_channels:
        for ch in data.waveform_channels:
            print(f"通道{ch.channel_id}: {len(ch.voltage_data)}个点")
    if data.power_measurements:
        for m in data.power_measurements:
            print(f"单元{m.element_id}: {m.active_power}W")
```