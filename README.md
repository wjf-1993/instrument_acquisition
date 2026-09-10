# Trae Solo 仪器数据采集系统

> 基于 Trae Solo 平台，以单智能体分步开发模式，完成日本横河 DLM5000 示波器、WT3000/WT1804E 功率分析仪的数据采集、分类存储、可视化展示、自动报告生成全流程。

---

## 项目总览

|项目|说明|
|-|-|
|**平台**|Trae Solo IDE|
|**语言**|Python 3.10+|
|**目标仪器**|Yokogawa DLM5000（示波器）、WT3000（功率分析仪）、WT1804E（功率分析仪）|
|**开发策略**|单Agent + 独立功能模块，逐个完成、测试稳定再推进|
|**预计周期**|25-30 天|

---

## 项目架构

```
instrument_acquisition/
│
├── main.py                      # 主调度入口
├── instrument_interface.py       # 抽象接口层
├── acquisition_scheduler.py      # 并发调度器
├── config.py                    # 配置解析
├── config.yaml                  # 配置文件
├── logger.py                    # 统一日志
│
├── adapters/                    # 仪器适配器
│   ├── __init__.py
│   ├── dlm5000_adapter.py
│   ├── wt3000_adapter.py
│   └── wt1804e_adapter.py
│
├── data/                        # 数据存储
├── logs/                        # 日志文件
├── templates/                   # 报告模板
├── tests/                       # 单元测试
│
└── docs/                        # 项目文档
```

---

## 核心设计原则

### 1. 抽象接口层（InstrumentBase）

所有仪器适配器继承统一的抽象基类 `InstrumentBase`，上层模块只依赖接口，不关心具体仪器型号。

### 2. 并发采集调度器

解决多仪器同时采集时的资源竞争和时间戳同步问题。

### 3. 分文件单一职责

每个 `.py` 文件只负责一个功能，问题出现时精准定位。

---

## 快速运行

```bash
pip install pyyaml pyvisa pyvisa-py
python main.py --demo
python main.py --once
python main.py
```

---

## 依赖清单

|库名|用途|安装|
|-|-|-|
|pyyaml|配置文件解析|`pip install pyyaml`|
|pyvisa|仪器通信|`pip install pyvisa`|
|pyvisa-py|PyVISA后端|`pip install pyvisa-py`|