# 架构设计详解

## 1. 整体架构图

```
┌─────────────────────────────────────────────────────────┐
│                      main.py 主调度                       │
│  加载配置 → 初始化日志 → 创建适配器 → 启动调度器         │
└──────────────┬──────────────────────────────┬────────────┘
               │                              │
       ┌───────▼───────┐              ┌───────▼───────┐
       │  config.yaml  │              │   logger.py   │
       │  config.py    │              │  统一日志管理  │
       └───────────────┘              └───────────────┘
               │
       ┌───────▼───────────────────────────────────────────┐
       │         acquisition_scheduler.py 调度器            │
       │  ┌─────────┐  ┌──────────┐  ┌────────────────┐   │
       │  │线程池管理│  │时间戳同步│  │ 回调分发(观察者)│   │
       │  └────┬────┘  └──────────┘  └───────┬────────┘   │
       └───────┼──────────────────────────────┼────────────┘
               │                              │
       ┌───────▼──────────────────────────────▼────────────┐
       │        instrument_interface.py 抽象接口层          │
       │              InstrumentBase (抽象基类)              │
       │   connect() / disconnect() / read_data()           │
       │   configure() / get_status()                       │
       └───┬──────────────┬──────────────┬─────────────────┘
           │              │              │
   ┌───────▼──────┐ ┌────▼───────┐ ┌────▼──────────┐
   │ DLM5000      │ │ WT3000     │ │ WT1804E       │
   │ Adapter      │ │ Adapter    │ │ Adapter       │
   │ (SCPI/VISA)  │ │ (Socket)   │ │ (SCPI/VISA)   │
   └──────┬───────┘ └────┬───────┘ └────┬──────────┘
          │              │              │
   ┌──────▼──────────────▼──────────────▼──────────────┐
   │              物理仪器 (Ethernet/USB/GPIB)          │
   │  DLM5000示波器  WT3000功率分析仪  WT1804E功率分析仪  │
   └───────────────────────────────────────────────────┘
```

## 2. 设计模式

### 2.1 策略模式 + 适配器模式

**问题：** 三台仪器的通信协议完全不同（SCPI vs 自定义Socket），上层模块如何统一调用？

**方案：** 定义 `InstrumentBase` 抽象基类，每种仪器实现各自的适配器。

```
上层模块（存储/UI/报告）
    ↓ 只依赖 InstrumentBase 接口
InstrumentBase
    ↓ 具体实现
DLM5000Adapter / WT3000Adapter / WT1804EAdapter
```

**好处：**
- 上层模块代码不需要修改即可支持新仪器
- 更换仪器只需新增一个 Adapter 文件
- 单元测试可以用 Mock 替换真实仪器

### 2.2 观察者模式（发布/订阅）

**问题：** 采集到的数据如何传递给存储模块和UI模块，且两者互不干扰？

**方案：** 调度器通过回调机制分发数据。

```
AcquisitionScheduler
    ├── callback → data_storage.save(result)    # 存储模块订阅
    ├── callback → ui.update_display(result)     # UI模块订阅
    └── callback → report.check_trigger(result)  # 报告模块订阅
```

### 2.3 模板方法模式

**问题：** 采集流程中有哪些步骤是固定的，哪些是可定制的？

**方案：** 调度器定义采集流程骨架，适配器实现具体细节。

```
调度器固定流程:
  1. 生成统一时间戳        ← 调度器负责
  2. 并发/顺序采集         ← 调度器负责
  3. 覆盖为同步时间戳      ← 调度器负责
  4. 重试机制              ← 调度器负责
  5. 结果汇总              ← 调度器负责

适配器可定制:
  - 具体通信协议           ← 各适配器实现
  - 数据解析方式           ← 各适配器实现
  - 参数配置命令           ← 各适配器实现
```

## 3. 数据流

### 3.1 采集数据流

```
物理仪器 → 适配器.read_data() → InstrumentData → 调度器(时间戳同步) → 回调分发
                                                                      ├── data_storage
                                                                      ├── ui_display
                                                                      └── report_generator
```

### 3.2 标准化数据容器

所有适配器返回统一的 `InstrumentData` 格式：

```python
InstrumentData
├── instrument_id: str              # 仪器标识
├── instrument_type: InstrumentType # 仪器类型（示波器/功率分析仪）
├── timestamp: float                # 统一时间戳
├── waveform_channels: []           # 示波器数据
│   └── WaveformChannel
│       ├── channel_id: int
│       ├── voltage_data: [float]
│       └── time_data: [float]
├── power_measurements: []          # 功率分析仪数据
│   └── PowerMeasurement
│       ├── element_id: int
│       ├── voltage: float
│       ├── current: float
│       ├── active_power: float
│       └── ...
└── metadata: {}                    # 扩展元数据
```

## 4. 模块依赖关系

```
main.py
  ├── config.py          ← 无依赖
  ├── logger.py          ← 无依赖
  ├── acquisition_scheduler.py
  │     └── instrument_interface.py  ← 无依赖
  └── adapters/
        ├── dlm5000_adapter.py
        │     └── instrument_interface.py
        │     └── pyvisa (外部库)
        ├── wt3000_adapter.py
        │     └── instrument_interface.py
        │     └── socket (标准库)
        └── wt1804e_adapter.py
              └── instrument_interface.py
              └── pyvisa (外部库)
```

**关键特点：**
- `instrument_interface.py` 是零依赖的核心模块
- 适配器之间完全独立，互不引用
- 上层模块只依赖抽象接口，不依赖具体适配器

## 5. 线程模型

```
主线程
  └── main.py 启动
        ├── 加载配置 (主线程)
        ├── 初始化日志 (主线程)
        └── scheduler.start()
              └── 后台调度线程 (daemon)
                    ├── 生成同步时间戳
                    ├── ThreadPoolExecutor
                    │     ├── Thread-1: DLM5000.read_data()
                    │     ├── Thread-2: WT3000.read_data()
                    │     └── Thread-3: WT1804E.read_data()
                    ├── 收集结果
                    ├── 通知回调 (在调度线程中顺序执行)
                    └── sleep(interval) → 下一轮
```

**线程安全保证：**
- 仪器注册/注销使用 `threading.Lock`
- 调度器状态变更使用 `threading.Lock`
- 各适配器的通信资源（VISA/Socket）由各自独立管理，无共享状态