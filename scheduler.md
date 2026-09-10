# 调度器使用说明

> 文件：`acquisition_scheduler.py`
> 依赖：`instrument_interface.py`

## 1. 核心功能

| 功能 | 说明 |
|------|------|
| 并发采集 | 线程池同时采集多台仪器 |
| 时间戳同步 | 所有仪器共享统一的采集时间戳 |
| 资源竞争管控 | 可配置线程数、单台超时 |
| 异常隔离 | 单台故障不影响其他仪器 |
| 重试机制 | 可配置重试次数和间隔 |
| 回调分发 | 采集结果通过回调通知订阅者 |
| 任务控制 | 启动/停止/暂停/恢复/单次采集 |

## 2. 两种采集模式

### 同步模式（sync）— 推荐

```python
scheduler = AcquisitionScheduler(mode="sync")
```

**特点：**
- 多台仪器**同时**采集（ThreadPoolExecutor 并发）
- 所有仪器共享**同一个时间戳**
- 采集速度快，适合需要时间对齐的场景

### 顺序模式（sequential）

```python
scheduler = AcquisitionScheduler(mode="sequential")
```

**特点：**
- 逐台仪器依次采集
- 每台仪器使用独立的实际采集时间戳
- 适合采集间隔要求不严格的场景

## 3. 使用方式

### 3.1 基本使用

```python
from acquisition_scheduler import AcquisitionScheduler
from adapters import DLM5000Adapter, WT1804EAdapter

scheduler = AcquisitionScheduler(
    mode="sync",
    interval=1.0,
    max_retries=3,
    retry_delay=1.0,
    timeout_per_instrument=10,
)
scheduler.register_instrument(dlm5000_adapter)
scheduler.register_instrument(wt1804e_adapter)
scheduler.add_callback(my_data_handler)
scheduler.start()
scheduler.stop()
```

### 3.2 单次采集

```python
result = scheduler.acquire_once()
print(f"成功: {result.success_count}, 失败: {result.error_count}")
```

### 3.3 暂停/恢复

```python
scheduler.start()
scheduler.pause()
scheduler.resume()
scheduler.stop()
```

### 3.4 回调函数

```python
def on_data_received(result: AcquisitionResult):
    print(f"采集 #{result.sequence_number}")
    print(f"时间: {result.sync_datetime}")
    print(f"耗时: {result.acquisition_time_ms}ms")
    for inst_id, data in result.results.items():
        if data.waveform_channels:
            save_waveform(data)
        if data.power_measurements:
            save_power_data(data)
    for inst_id, error in result.errors.items():
        print(f"[{inst_id}] 失败: {error}")
scheduler.add_callback(on_data_received)
```

## 4. AcquisitionResult 结果容器

```python
@dataclass
class AcquisitionResult:
    sequence_number: int
    sync_timestamp: float
    sync_datetime: str
    results: Dict[str, InstrumentData]
    errors: Dict[str, str]
    acquisition_time_ms: float
    is_success: bool
    success_count: int
    error_count: int
```

## 5. 时间戳同步机制

```python
def _do_acquisition(self):
    sync_timestamp = time.time()
    results = self._acquire_sync(sync_timestamp)
    data = instrument.read_data()
    data.timestamp = sync_timestamp
    data.datetime_str = ...
```

**为什么重要：**
- 示波器和功率分析仪的数据需要时间对齐才能联合分析
- 并发采集的各仪器完成时间不同（可能差几毫秒到几百毫秒）
- 统一时间戳确保数据在时间维度上是对齐的

## 6. 异常处理策略

| 异常场景 | 处理方式 |
|----------|----------|
| 单台仪器采集超时 | 跳过该仪器，记录错误，继续其他仪器 |
| 单台仪器连接断开 | 重试 N 次，仍失败则记录错误 |
| 所有仪器都失败 | 记录错误，等待下一轮采集 |
| 回调函数异常 | 捕获异常，不影响其他回调和下一轮采集 |
| 调度线程异常 | 记录错误，调度器进入 ERROR 状态 |

## 7. 统计信息

```python
stats = scheduler.statistics
# {"state": "running", "mode": "sync", "total_acquisitions": 100, ...}
```