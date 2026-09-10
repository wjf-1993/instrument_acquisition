#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# test_file_monitor.py — 文件名变更触发 + SQLite 存储
# 文件名变化 → 自动记录测量值到 CSV + SQLite
# 同名文件 → 不触发
# Ctrl+C 退出
import pyvisa, csv, math, time, sys, os
from datetime import datetime

IP = "172.17.76.203"
SCRIPT_DIR = sys.path[0]
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
CSV_PATH = os.path.join(SCRIPT_DIR, "monitor_output.csv")
DB_PATH = os.path.join(PROJECT_DIR, "data", "acquisition.db")

COLUMNS = [
    "序号", "示波器文件名", "采集时间",
    "CH1_Max(V)", "CH1_Min(V)", "CH1_RMS(V)", "CH1_PP(V)", "CH1_Freq(Hz)",
    "CH2_Max(A)", "CH2_Min(A)", "CH2_RMS(A)", "CH2_PP(A)", "CH2_Freq(Hz)",
    "CH3_Max(V)", "CH3_Min(V)", "CH3_RMS(V)", "CH3_PP(V)", "CH3_Freq(Hz)",
    "CH4_Max(V)", "CH4_Min(V)", "CH4_RMS(V)", "CH4_PP(V)", "CH4_Freq(Hz)",
]
CHANNELS = [1, 2, 3, 4]
MEAS = ["MAX", "MIN", "RMS", "AVER", "FREQ", "PERIOD", "RISE", "FALL", "PWIDTH", "NWIDTH", "DUTY"]
# 通道类型配置：V=电压, A=电流（用于数据库查看器显示单位）
CHANNEL_TYPES = {1: "V", 2: "A", 3: "V", 4: "V"}

# ── 连接 ──
rm = pyvisa.ResourceManager('@py')
inst = rm.open_resource(f"TCPIP::{IP}::INSTR")
inst.timeout = 5000
inst.read_termination = "\n"
inst.write_termination = "\n"
time.sleep(0.2)

def q(cmd):
    global inst, rm
    try:
        inst.write(cmd)
        time.sleep(0.03)
        return inst.read().strip()
    except:
        try: inst.close()
        except: pass
        try: rm.close()
        except: pass
        time.sleep(0.3)
        rm = pyvisa.ResourceManager('@py')
        inst = rm.open_resource(f"TCPIP::{IP}::INSTR")
        inst.timeout = 5000
        inst.read_termination = "\n"
        inst.write_termination = "\n"
        time.sleep(0.2)
        try:
            inst.write(cmd)
            time.sleep(0.03)
            return inst.read().strip()
        except:
            return None

def get_filename():
    resp = q(":FILE:SAVE:NAME?")
    if resp is None: return None
    if '"' in resp: return resp.split('"')[1]
    return resp.split()[-1]

def read_all():
    results = {}
    for ch in CHANNELS:
        for m in MEAS:
            try:
                resp = q(f":MEAS:CHAN{ch}:{m}:VAL?")
                if resp:
                    try: results[f"CH{ch}_{m}"] = float(resp.split()[-1])
                    except: results[f"CH{ch}_{m}"] = float('nan')
                else: results[f"CH{ch}_{m}"] = float('nan')
            except: results[f"CH{ch}_{m}"] = float('nan')
        # PP = MAX - MIN（DLM5000 不支持 PP 命令）
        mx = results.get(f"CH{ch}_MAX", float('nan'))
        mn = results.get(f"CH{ch}_MIN", float('nan'))
        if mx == mx and mn == mn:
            results[f"CH{ch}_PP"] = mx - mn
        else:
            results[f"CH{ch}_PP"] = float('nan')
    return results

def save_csv(record_count, filename, meas):
    rows = []
    try:
        with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
    except:
        header = COLUMNS
    rows = [r for r in rows if r[1] != filename]
    row = [record_count, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    for ch in CHANNELS:
        for m in MEAS:
            v = meas.get(f"CH{ch}_{m}", float('nan'))
            row.append("" if math.isnan(v) else f"{v:.6g}")
    rows.append(row)
    with open(CSV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerow(header)
        csv.writer(f).writerows(rows)

def save_db(record_count, filename, meas):
    """写入 SQLite 数据库（直接用真实测量值，不构建虚假波形数据）"""
    from data_storage import DataStorage
    from instrument_interface import InstrumentData, InstrumentType

    # 把所有测量值存入 metadata（包括计算的 PP）
    measurements_json = {}
    for key, val in meas.items():
        measurements_json[key] = val if val == val else None

    data = InstrumentData(
        instrument_id="dlm5000",
        instrument_type=InstrumentType.OSCILLOSCOPE,
        timestamp=time.time(),
        datetime_str=datetime.now().isoformat(),
        waveform_channels=[],  # 不传虚假波形数据
        power_measurements=[],
        metadata={
            "filename": filename,
            "source": "file_monitor",
            "measurements": measurements_json,
            "channel_types": CHANNEL_TYPES,
        },
    )
    storage.save_acquisition("dlm5000", data, record_count)

# ── 初始化 ──
with open(CSV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
    csv.writer(f).writerow(COLUMNS)

# 初始化数据库
sys.path.insert(0, PROJECT_DIR)
from data_storage import DataStorage
storage = DataStorage(DB_PATH)
if hasattr(storage, 'initialize'):
    storage.initialize()

print(f"文件监控 CSV={CSV_PATH}")
print(f"数据库   DB={DB_PATH}")
print(f"文件名变化时自动记录，Ctrl+C退出\n")

last_name = get_filename()
record_count = 0
print(f"  当前文件: {last_name}")

try:
    while True:
        time.sleep(1)
        name = get_filename()
        if name is None or name == last_name:
            continue

        # 文件名变化 → 记录
        record_count += 1
        print(f"  ✅ 记录 #{record_count}: {last_name} → {name}")
        meas = read_all()
        save_csv(record_count, name, meas)
        save_db(record_count, name, meas)
        for k, v in sorted(meas.items()):
            if v == v:
                print(f"       {k}: {v:.6g}")
        last_name = name

except KeyboardInterrupt:
    pass

try: inst.close(); rm.close()
except: pass
print(f"\n停止，共 {record_count} 条记录")
