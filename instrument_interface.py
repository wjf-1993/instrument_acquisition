# ============================================================
# instrument_interface.py — 仪器抽象接口层
# ============================================================
# 职责：定义所有仪器必须实现的统一接口（InstrumentBase），
#       以及标准化的数据容器（InstrumentData）。
#       上层模块（存储、报告、UI）仅依赖此接口，
#       不关心具体仪器型号和通信协议。
#
# 设计模式：策略模式 + 模板方法模式
#   - InstrumentBase 定义统一行为契约
#   - 各适配器继承并实现具体通信逻辑
#   - 上层通过 InstrumentBase 类型引用操作仪器
# ============================================================

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ============================================================
# 数据容器 — 标准化的仪器采集数据格式
# ============================================================

class InstrumentType(Enum):
    """仪器类型枚举"""
    OSCILLOSCOPE = "oscilloscope"      # 示波器
    POWER_ANALYZER = "power_analyzer"  # 功率分析仪


class ConnectionStatus(Enum):
    """连接状态枚举"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class WaveformChannel:
    """单个通道的波形数据"""
    channel_id: int                   # 通道编号
    channel_name: str = ""            # 通道名称
    voltage_data: List[float] = field(default_factory=list)   # 电压数据
    time_data: List[float] = field(default_factory=list)      # 时间轴数据
    sample_rate: float = 0.0          # 采样率 (Hz)
    voltage_range: float = 0.0        # 电压量程 (V)
    unit: str = "V"                   # 数据单位


@dataclass
class PowerMeasurement:
    """单个测量单元的功率参数"""
    element_id: int                   # 测量单元编号
    voltage: float = 0.0              # 电压 (V)
    current: float = 0.0              # 电流 (A)
    active_power: float = 0.0         # 有功功率 (W)
    reactive_power: float = 0.0       # 无功功率 (VAR)
    apparent_power: float = 0.0       # 视在功率 (VA)
    power_factor: float = 0.0         # 功率因数
    energy: float = 0.0               # 电能 (Wh)
    frequency: float = 0.0            # 频率 (Hz)
    extra: Dict[str, float] = field(default_factory=dict)  # 扩展参数


@dataclass
class InstrumentData:
    """
    标准化的仪器采集数据容器。
    所有适配器返回的数据必须是此格式，
    上层模块统一按此格式处理。
    """
    instrument_id: str                # 仪器标识（如 "dlm5000"）
    instrument_type: InstrumentType   # 仪器类型
    timestamp: float = 0.0            # 采集时间戳 (Unix timestamp)
    datetime_str: str = ""            # 采集时间字符串 (ISO格式)

    # 示波器数据
    waveform_channels: List[WaveformChannel] = field(default_factory=list)

    # 功率分析仪数据
    power_measurements: List[PowerMeasurement] = field(default_factory=list)

    # 原始数据引用（可选，用于调试或大数据存储）
    raw_data: Optional[bytes] = None
    raw_data_path: Optional[str] = None  # 原始数据文件路径

    # 元数据
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """初始化后自动填充时间戳"""
        if self.timestamp == 0.0:
            self.timestamp = time.time()
        if not self.datetime_str:
            self.datetime_str = datetime.fromtimestamp(
                self.timestamp
            ).isoformat()


# ============================================================
# 抽象基类 — 所有仪器适配器必须继承此类
# ============================================================

class InstrumentBase(ABC):
    """
    仪器抽象基类。

    定义了所有仪器必须实现的统一接口：
    - connect() / disconnect()：连接管理
    - read_data()：数据采集
    - configure()：参数配置
    - get_status()：状态查询
    - get_info()：仪器信息

    使用方式：
        adapter = DLM5000Adapter(config)
        adapter.connect()
        data = adapter.read_data()
        adapter.disconnect()
    """

    def __init__(self, instrument_id: str, instrument_type: InstrumentType):
        """
        初始化仪器基类。

        Args:
            instrument_id: 仪器唯一标识（对应 config.yaml 中的 key）
            instrument_type: 仪器类型
        """
        self._instrument_id = instrument_id
        self._instrument_type = instrument_type
        self._connection_status = ConnectionStatus.DISCONNECTED
        self._last_error: Optional[str] = None
        self._metadata: Dict[str, Any] = {}

    # ---------- 属性 ----------

    @property
    def instrument_id(self) -> str:
        """仪器唯一标识"""
        return self._instrument_id

    @property
    def instrument_type(self) -> InstrumentType:
        """仪器类型"""
        return self._instrument_type

    @property
    def connection_status(self) -> ConnectionStatus:
        """当前连接状态"""
        return self._connection_status

    @property
    def last_error(self) -> Optional[str]:
        """最后一次错误信息"""
        return self._last_error

    @property
    def is_connected(self) -> bool:
        """是否已连接"""
        return self._connection_status == ConnectionStatus.CONNECTED

    # ---------- 抽象方法（子类必须实现）----------

    @abstractmethod
    def connect(self) -> bool:
        """
        连接仪器。

        Returns:
            True 连接成功，False 连接失败
        """
        ...

    @abstractmethod
    def disconnect(self) -> bool:
        """
        断开仪器连接。

        Returns:
            True 断开成功，False 断开失败
        """
        ...

    @abstractmethod
    def read_data(self) -> InstrumentData:
        """
        从仪器读取一次采集数据。

        Returns:
            InstrumentData 标准化数据容器

        Raises:
            ConnectionError: 未连接或连接异常
            TimeoutError: 采集超时
        """
        ...

    @abstractmethod
    def configure(self, **params: Any) -> bool:
        """
        配置仪器参数。

        Args:
            **params: 参数键值对（具体参数因仪器而异）

        Returns:
            True 配置成功，False 配置失败
        """
        ...

    @abstractmethod
    def get_status(self) -> Dict[str, Any]:
        """
        获取仪器当前状态。

        Returns:
            状态信息字典，至少包含：
            - "connection": 连接状态
            - "instrument_id": 仪器标识
            - "instrument_type": 仪器类型
        """
        ...

    # ---------- 可选覆盖方法（模板方法）----------

    def get_info(self) -> Dict[str, str]:
        """
        获取仪器基本信息（设备型号、序列号等）。
        子类可覆盖以返回更详细的信息。

        Returns:
            仪器信息字典
        """
        return {
            "instrument_id": self._instrument_id,
            "instrument_type": self._instrument_type.value,
            "connection_status": self._connection_status.value,
        }

    def reset(self) -> bool:
        """
        重置仪器到默认状态。
        子类可覆盖以实现仪器重置功能。

        Returns:
            True 重置成功
        """
        self._last_error = None
        return True

    def self_test(self) -> bool:
        """
        仪器自检。
        子类可覆盖以实现仪器自检功能。

        Returns:
            True 自检通过
        """
        return self.is_connected

    # ---------- 内部辅助方法 ----------

    def _set_connected(self) -> None:
        """设置连接状态为已连接"""
        self._connection_status = ConnectionStatus.CONNECTED
        self._last_error = None

    def _set_disconnected(self) -> None:
        """设置连接状态为已断开"""
        self._connection_status = ConnectionStatus.DISCONNECTED

    def _set_error(self, error_msg: str) -> None:
        """设置错误状态"""
        self._connection_status = ConnectionStatus.ERROR
        self._last_error = error_msg

    def _require_connected(self) -> None:
        """
        检查是否已连接，未连接则抛出异常。
        在 read_data() 等需要连接的操作前调用。
        """
        if not self.is_connected:
            raise ConnectionError(
                f"仪器 [{self._instrument_id}] 未连接，"
                f"请先调用 connect() 方法"
            )

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"id={self._instrument_id} "
            f"type={self._instrument_type.value} "
            f"status={self._connection_status.value}>"
        )
