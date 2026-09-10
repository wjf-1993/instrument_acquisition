#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# multi_device_monitor.py — 多设备统一监控入口
# =================================================================
# 支持: 多台示波器 + 多台功率分析仪同时监控
# 每台设备一个独立线程，数据统一写入同一个 SQLite 数据库
# CSV 按设备分别导出
#
# 启动方式：
#   cd instrument_acquisition
#   python multi_device_monitor.py
#
# 新增设备：只需在 DEVICES 列表中添加一条配置即可
# =================================================================

import csv
import json
import math
import os
import sqlite3
import struct
import sys
import threading
import time
from datetime import datetime

# Windows 控制台 UTF-8 输出支持
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ============================================================
# 设备配置 — 从 devices.json 读取，或使用内置默认列表
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEVICES_JSON = os.path.join(SCRIPT_DIR, "devices.json")

# 内置默认设备列表（当 devices.json 不存在时使用）
_DEFAULT_DEVICES = [
    {
        "id":    "EP11-652",
        "type":  "power_analyzer",
        "ip":    "172.17.76.201",
        "model": "WT1804E",
        "enabled": True,
    },
    {
        "id":    "EP11-545",
        "type":  "power_analyzer",
        "ip":    "172.17.76.203",
        "model": "WT3000",
        "enabled": True,
    },
    {
        "id":    "EP11-035",
        "type":  "oscilloscope",
        "ip":    "172.17.76.202",
        "model": "DLM5000",
        "enabled": True,
    },
]

def load_devices():
    """从 devices.json 加载设备列表，不存在则使用默认列表"""
    if os.path.exists(DEVICES_JSON):
        try:
            with open(DEVICES_JSON, 'r', encoding='utf-8') as f:
                config = json.load(f)
            devices = config.get("devices", [])
            # 只返回启用的设备
            return [d for d in devices if d.get("enabled", True)]
        except Exception as e:
            print(f"  ⚠️ 读取 {DEVICES_JSON} 失败: {e}，使用默认配置")
    return [d for d in _DEFAULT_DEVICES if d.get("enabled", True)]

def save_devices(devices):
    """保存设备列表到 devices.json"""
    try:
        with open(DEVICES_JSON, 'w', encoding='utf-8') as f:
            json.dump({"devices": devices}, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print(f"  ❌ 保存 {DEVICES_JSON} 失败: {e}")
        return False

# ── 全局路径 ──
DB_PATH = os.path.join(SCRIPT_DIR, "data", "acquisition.db")
CSV_DIR = os.path.join(SCRIPT_DIR, "output", "csv")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
STATUS_FILE = os.path.join(LOG_DIR, "device_status.json")

# ── 设备状态上报 ──
device_states = {}  # {device_id: {state, detail, record_count, last_update, error_msg}}
state_lock = threading.Lock()


def update_device_state(device_id, state, detail="", error_msg=None):
    """线程安全地更新设备状态并写入文件（多进程安全：先读-合并-写）"""
    entry = {
        "state": state,
        "detail": detail,
        "record_count": _get_counter(device_id),
        "last_update": time.time(),
        "error_msg": error_msg,
    }
    os.makedirs(LOG_DIR, exist_ok=True)
    tmp = STATUS_FILE + ".tmp"
    try:
        # 读取已有状态（其他进程可能已写入）
        all_states = {}
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, 'r', encoding='utf-8') as f:
                    all_states = json.load(f)
            except (json.JSONDecodeError, Exception):
                pass
        # 合并：更新本设备条目，清理 60 秒未更新的过期条目
        now = time.time()
        stale = [k for k, v in all_states.items()
                 if k != device_id and now - v.get("last_update", 0) > 60]
        for k in stale:
            del all_states[k]
        all_states[device_id] = entry
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(all_states, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATUS_FILE)
    except Exception:
        pass

# ── 功率分析仪参数配置 ──
ACQUIRE_ITEMS = [
    # Ch1 (ITEM 1-9)
    (1,  "URMS,1",    "URMS",    "V",  1),
    (2,  "IRMS,1",    "IRMS",    "A",  1),
    (3,  "P,1",       "P",       "W",  1),
    (4,  "LAMB,1",    "LAMB",    "",   1),
    (5,  "ITHD,1",    "ITHD",    "%",  1),
    (6,  "IHDFk,1,3", "IK3",     "%",  1),
    (7,  "IHDFk,1,5", "IK5",     "%",  1),
    (8,  "IHDFk,1,7", "IK7",     "%",  1),
    (9,  "IHDFk,1,9", "IK9",     "%",  1),
    # Ch2 (ITEM 10-17)
    (10, "URMS,2",    "URMS",    "V",  2),
    (11, "IRMS,2",    "IRMS",    "A",  2),
    (12, "P,2",       "P",       "W",  2),
    (13, "IPPeak,2",  "IPPeak",  "A",  2),
    (14, "IMPeak,2",  "IMPeak",  "A",  2),
    (15, "F1,2",      "F1",      "A",  2),
    (16, "IDC,2",     "IDC",     "A",  2),
    (17, "F2,2",      "F2",      "%",  2),
    # Ch3 = Ch1 参数 (ITEM 18-26)
    (18, "URMS,3",    "URMS",    "V",  3),
    (19, "IRMS,3",    "IRMS",    "A",  3),
    (20, "P,3",       "P",       "W",  3),
    (21, "LAMB,3",    "LAMB",    "",   3),
    (22, "ITHD,3",    "ITHD",    "%",  3),
    (23, "IHDFk,3,3", "IK3",     "%",  3),
    (24, "IHDFk,3,5", "IK5",     "%",  3),
    (25, "IHDFk,3,7", "IK7",     "%",  3),
    (26, "IHDFk,3,9", "IK9",     "%",  3),
    # Ch4 = Ch2 参数 (ITEM 27-34)
    (27, "URMS,4",    "URMS",    "V",  4),
    (28, "IRMS,4",    "IRMS",    "A",  4),
    (29, "P,4",       "P",       "W",  4),
    (30, "IPPeak,4",  "IPPeak",  "A",  4),
    (31, "IMPeak,4",  "IMPeak",  "A",  4),
    (32, "F3",        "F3",      "A",  4),
    (33, "IDC,4",     "IDC",     "A",  4),
    (34, "F4",        "F4",      "%",  4),
]

WT3000_PARAM_MAP = {
    "URMS,1": "U,1", "IRMS,1": "I,1", "P,1": "P,1",
    "LAMB,1": "LAMB,1", "ITHD,1": "ITHD,1",
    "IHDFk,1,3": "IHDF,1,3", "IHDFk,1,5": "IHDF,1,5",
    "IHDFk,1,7": "IHDF,1,7", "IHDFk,1,9": "IHDF,1,9",
    "URMS,2": "U,2", "IRMS,2": "I,2", "P,2": "P,2",
    "IPPeak,2": "IPP,2", "IMPeak,2": "IMP,2",
    "F1,2": "F1,2", "IDC,2": "F3,2", "F2,2": "F2,2",
    "URMS,3": "U,3", "IRMS,3": "I,3", "P,3": "P,3",
    "LAMB,3": "LAMB,3", "ITHD,3": "ITHD,3",
    "IHDFk,3,3": "IHDF,3,3", "IHDFk,3,5": "IHDF,3,5",
    "IHDFk,3,7": "IHDF,3,7", "IHDFk,3,9": "IHDF,3,9",
    "URMS,4": "U,4", "IRMS,4": "I,4", "P,4": "P,4",
    "IPPeak,4": "IPP,4", "IMPeak,4": "IMP,4",
    "F3": "F3", "IDC,4": "IDC,4", "F4": "F4",
}

# ── 示波器参数配置 ──
OSC_CHANNELS = [1, 2, 3, 4]
OSC_MEAS = ["MAX", "MIN", "RMS", "AVER", "FREQ", "PERIOD", "RISE", "FALL", "PWIDTH", "NWIDTH", "DUTY"]
OSC_CHANNEL_TYPES = {1: "V", 2: "A", 3: "V", 4: "V"}
OSC_COLUMNS = [
    "序号", "示波器文件名", "采集时间",
    "CH1_Max(V)", "CH1_Min(V)", "CH1_RMS(V)", "CH1_PP(V)", "CH1_Freq(Hz)",
    "CH2_Max(A)", "CH2_Min(A)", "CH2_RMS(A)", "CH2_PP(A)", "CH2_Freq(Hz)",
    "CH3_Max(V)", "CH3_Min(V)", "CH3_RMS(V)", "CH3_PP(V)", "CH3_Freq(Hz)",
    "CH4_Max(V)", "CH4_Min(V)", "CH4_RMS(V)", "CH4_PP(V)", "CH4_Freq(Hz)",
]

# ── 全局状态 ──
db_lock = threading.Lock()
record_counters = {}  # {device_id: count}
counter_lock = threading.Lock()
stop_event = threading.Event()


def _get_counter(device_id):
    with counter_lock:
        return record_counters.get(device_id, 0)


def _inc_counter(device_id):
    with counter_lock:
        record_counters[device_id] = record_counters.get(device_id, 0) + 1
        return record_counters[device_id]


def _set_counter(device_id, value):
    with counter_lock:
        record_counters[device_id] = value


# ============================================================
# 数据库操作（线程安全）
# ============================================================

def init_db():
    """初始化数据库和表"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS acquisition_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sequence_number INTEGER NOT NULL,
            sync_timestamp REAL NOT NULL,
            sync_datetime TEXT NOT NULL,
            instrument_id TEXT NOT NULL,
            instrument_type TEXT NOT NULL,
            filename TEXT,
            acquisition_time_ms REAL,
            metadata TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS oscilloscope_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            channel_id INTEGER NOT NULL,
            channel_name TEXT,
            sample_rate REAL,
            voltage_range REAL,
            max_voltage REAL,
            min_voltage REAL,
            avg_voltage REAL,
            rms_voltage REAL,
            peak_to_peak REAL,
            data_points INTEGER,
            FOREIGN KEY (record_id) REFERENCES acquisition_records(id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS power_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            record_id INTEGER NOT NULL,
            ch1_urms REAL, ch1_irms REAL, ch1_p REAL,
            ch1_lamb REAL, ch1_ithd REAL,
            ch1_ik3 REAL, ch1_ik5 REAL, ch1_ik7 REAL, ch1_ik9 REAL,
            ch2_urms REAL, ch2_irms REAL, ch2_p REAL,
            ch2_ippeak REAL, ch2_impeak REAL,
            ch2_f1 REAL, ch2_idc REAL, ch2_f2 REAL,
            ch3_urms REAL, ch3_irms REAL, ch3_p REAL,
            ch3_lamb REAL, ch3_ithd REAL,
            ch3_ik3 REAL, ch3_ik5 REAL, ch3_ik7 REAL, ch3_ik9 REAL,
            ch4_urms REAL, ch4_irms REAL, ch4_p REAL,
            ch4_ippeak REAL, ch4_impeak REAL,
            ch4_f3 REAL, ch4_idc REAL, ch4_f4 REAL,
            FOREIGN KEY (record_id) REFERENCES acquisition_records(id)
        )
    """)
    # 补加缺失的列（旧数据库可能只有17列）
    cursor.execute("PRAGMA table_info(power_data)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    missing_cols = [
        ("ch3_urms", "REAL"), ("ch3_irms", "REAL"), ("ch3_p", "REAL"),
        ("ch3_lamb", "REAL"), ("ch3_ithd", "REAL"),
        ("ch3_ik3", "REAL"), ("ch3_ik5", "REAL"), ("ch3_ik7", "REAL"), ("ch3_ik9", "REAL"),
        ("ch4_urms", "REAL"), ("ch4_irms", "REAL"), ("ch4_p", "REAL"),
        ("ch4_ippeak", "REAL"), ("ch4_impeak", "REAL"),
        ("ch4_f3", "REAL"), ("ch4_idc", "REAL"), ("ch4_f4", "REAL"),
    ]
    for col_name, col_type in missing_cols:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE power_data ADD COLUMN {col_name} {col_type}")
    conn.commit()
    conn.close()


def save_oscilloscope_to_db(instrument_id, record_count, filename, meas):
    """示波器数据写入数据库"""
    measurements_json = {}
    for key, val in meas.items():
        measurements_json[key] = val if val == val else None

    now = datetime.now()
    metadata = json.dumps({
        "filename": filename,
        "source": "multi_device_monitor",
        "measurements": measurements_json,
        "channel_types": OSC_CHANNEL_TYPES,
    }, ensure_ascii=False)

    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO acquisition_records
                    (sequence_number, sync_timestamp, sync_datetime,
                     instrument_id, instrument_type, filename,
                     acquisition_time_ms, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (record_count, now.timestamp(), now.isoformat(),
                  instrument_id, "oscilloscope", filename,
                  0.0, metadata, now.isoformat()))
            record_id = cursor.lastrowid

            for ch in OSC_CHANNELS:
                ch_prefix = f"CH{ch}_"
                max_v = measurements_json.get(f"{ch_prefix}MAX")
                min_v = measurements_json.get(f"{ch_prefix}MIN")
                rms_v = measurements_json.get(f"{ch_prefix}RMS")
                ptp_v = measurements_json.get(f"{ch_prefix}PP")
                avg_v = measurements_json.get(f"{ch_prefix}AVER")
                if max_v is None and min_v is None and rms_v is None:
                    continue
                if max_v == 0.0 and min_v == 0.0 and rms_v == 0.0:
                    continue
                cursor.execute("""
                    INSERT INTO oscilloscope_channels
                        (record_id, channel_id, channel_name,
                         sample_rate, voltage_range,
                         max_voltage, min_voltage, avg_voltage,
                         rms_voltage, peak_to_peak, data_points)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (record_id, ch, f"CH{ch}", 0.0, 0.0,
                      max_v if max_v else 0.0,
                      min_v if min_v else 0.0,
                      avg_v if avg_v else 0.0,
                      rms_v if rms_v else 0.0,
                      ptp_v if ptp_v else 0.0,
                      0))
            conn.commit()
            print(f"     💾 DB record_id={record_id}")
        except Exception as e:
            print(f"     ❌ DB写入失败: {e}")
            conn.rollback()
        finally:
            conn.close()


def save_power_to_db(instrument_id, record_count, results, model="", filename=""):
    """功率分析仪数据写入数据库"""
    power_values = {}
    for name, val, unit, ch in results:
        key = f"ch{ch}_{name.lower()}"
        power_values[key] = val if val == val else None

    now = datetime.now()
    metadata = json.dumps({
        "power_values": power_values,
        "source": "multi_device_monitor",
        "model": model,
    }, ensure_ascii=False)

    columns = [
        "ch1_urms", "ch1_irms", "ch1_p", "ch1_lamb", "ch1_ithd",
        "ch1_ik3", "ch1_ik5", "ch1_ik7", "ch1_ik9",
        "ch2_urms", "ch2_irms", "ch2_p", "ch2_ippeak", "ch2_impeak",
        "ch2_f1", "ch2_idc", "ch2_f2",
        "ch3_urms", "ch3_irms", "ch3_p", "ch3_lamb", "ch3_ithd",
        "ch3_ik3", "ch3_ik5", "ch3_ik7", "ch3_ik9",
        "ch4_urms", "ch4_irms", "ch4_p", "ch4_ippeak", "ch4_impeak",
        "ch4_f3", "ch4_idc", "ch4_f4",
    ]

    with db_lock:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO acquisition_records
                    (sequence_number, sync_timestamp, sync_datetime,
                     instrument_id, instrument_type, filename,
                     acquisition_time_ms, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (record_count, now.timestamp(), now.isoformat(),
                  instrument_id, "power_analyzer", filename,
                  0.0, metadata, now.isoformat()))
            record_id = cursor.lastrowid

            values = []
            for col in columns:
                v = power_values.get(col)
                values.append(round(v, 6) if v is not None and v == v else None)

            placeholders = ",".join(["?"] * (1 + len(columns)))
            col_names = "record_id," + ",".join(columns)
            cursor.execute(
                f"INSERT INTO power_data ({col_names}) VALUES ({placeholders})",
                [record_id] + values,
            )
            conn.commit()
            print(f"     💾 DB record_id={record_id}")
        except Exception as e:
            print(f"     ❌ DB写入失败: {e}")
            conn.rollback()
        finally:
            conn.close()


# ============================================================
# CSV 存储
# ============================================================

def save_osc_csv(instrument_id, record_count, filename, meas):
    """示波器数据写入CSV"""
    os.makedirs(CSV_DIR, exist_ok=True)
    csv_path = os.path.join(CSV_DIR, f"{instrument_id}_oscilloscope.csv")
    rows = []
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
    except:
        header = OSC_COLUMNS
    rows = [r for r in rows if len(r) > 1 and r[1] != filename]
    row = [record_count, filename, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    for ch in OSC_CHANNELS:
        for m in OSC_MEAS:
            v = meas.get(f"CH{ch}_{m}", float('nan'))
            row.append("" if math.isnan(v) else f"{v:.6g}")
    rows.append(row)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerow(header)
        csv.writer(f).writerows(rows)


def save_pwr_csv(instrument_id, record_count, results):
    """功率分析仪数据写入CSV"""
    os.makedirs(CSV_DIR, exist_ok=True)
    csv_path = os.path.join(CSV_DIR, f"{instrument_id}_power.csv")
    columns = ["序号", "采集时间"]
    for name, _, unit, _ in results:
        columns.append(f"{name}({unit})" if unit else name)
    rows = []
    try:
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
    except:
        header = columns
    row = [record_count, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    for name, val, unit, _ in results:
        row.append("" if math.isnan(val) else f"{val:.6g}")
    rows.append(row)
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerow(columns)
        csv.writer(f).writerows(rows)


# ============================================================
# 功率分析仪通信层（从 test_power_file_monitor.py 复用）
# ============================================================

def parse_ieee4882_block(raw):
    if not raw or raw[0:1] != b'#':
        return None
    n_digits = int(chr(raw[1]))
    length_str = raw[2:2 + n_digits].decode('ascii')
    data_length = int(length_str)
    header_length = 2 + n_digits
    return raw[header_length:header_length + data_length]


def parse_float32_be(data):
    count = len(data) // 4
    return [struct.unpack('>f', data[i*4:(i+1)*4])[0] for i in range(count)]


def is_valid(v):
    if v != v or abs(v) > 1e30:
        return False
    return True


class WT1800Adapter:
    """WT1800/WT1802E/WT1804E — VXI-11 协议"""
    def __init__(self, ip, timeout=30000):
        self.ip = ip
        self.timeout = timeout
        self.instr = None
        self.model = ""
        self.original_items = {}

    def connect(self):
        import vxi11
        self.instr = vxi11.Instrument(self.ip)
        self.instr.timeout = self.timeout
        self.instr.write("*IDN?\n")
        time.sleep(0.3)
        raw = self.instr.read_raw()
        self.model = raw.decode('ascii').strip()
        self._drain_errors()
        try:
            self._send(":SYSTEM:BEEP:STAT OFF")
            time.sleep(0.1)
        except:
            pass
        self._save_original_items()
        self._drain_errors()
        return True

    def disconnect(self):
        if self.instr:
            try: self.instr.close()
            except: pass
            self.instr = None

    def reconnect(self):
        """断开并重新连接，保留原始 ITEM 配置"""
        self.disconnect()
        time.sleep(1)
        self.connect()
        self.configure_items(ACQUIRE_ITEMS)

    def _clear(self):
        """清空缓冲区 — 用极短超时避免阻塞轮询"""
        old = self.instr.timeout
        self.instr.timeout = 0.5
        try:
            while True:
                raw = self.instr.read_raw()
                if raw is None or len(raw) == 0:
                    break
        except:
            pass
        finally:
            self.instr.timeout = old

    def _drain_errors(self):
        """排空设备错误队列，防止弹窗"""
        old = self.instr.timeout
        self.instr.timeout = 2.0
        try:
            for _ in range(20):
                self._send(":SYSTEM:ERROR?")
                time.sleep(0.1)
                try:
                    raw = self.instr.read_raw()
                    resp = raw.decode('ascii').strip() if raw else ""
                except:
                    break
                # 设备返回 "+0,No error" 或类似表示无错误
                if resp.startswith("+0") or "no error" in resp.lower():
                    break
        except:
            pass
        finally:
            self.instr.timeout = old

    def _send(self, cmd):
        self.instr.write(f"{cmd}\n")

    def _query(self, cmd):
        self._clear()
        self._send(cmd)
        time.sleep(0.3)
        raw = self.instr.read_raw()
        if raw and raw[0:1] != b'#':
            return raw.decode('ascii').strip()
        return None

    def _query_quick(self, cmd, timeout=3.0):
        """短超时查询 — 用于轮询等非关键操作，避免30s阻塞"""
        old = self.instr.timeout
        self.instr.timeout = timeout
        try:
            self._clear()
            self._send(cmd)
            time.sleep(0.3)
            raw = self.instr.read_raw()
            if raw and raw[0:1] != b'#':
                return raw.decode('ascii').strip()
            return None
        finally:
            self.instr.timeout = old

    def _save_original_items(self):
        self.original_items = {}
        # 先查询当前配置的 ITEM 数量，避免查询不存在的 ITEM 触发 Error 113
        resp = self._query_quick(":NUMERIC:NORMAL:NUMBER?", timeout=3.0)
        try:
            num_items = int(resp) if resp else 0
        except:
            num_items = 0
        for i in range(1, num_items + 1):
            resp = self._query_quick(f":NUMERIC:NORMAL:ITEM{i}?", timeout=3.0)
            if resp is None:
                break
            parts = resp.split()
            if len(parts) >= 2:
                self.original_items[i] = parts[-1]

    def _restore_original_items(self):
        for idx, param in self.original_items.items():
            try:
                self._send(f":NUMERIC:NORMAL:ITEM{idx} {param}")
                time.sleep(0.05)
            except:
                pass

    def configure_items(self, items):
        num = len(items)
        self._send(f":NUMERIC:NORMAL:NUMBER {num}")
        time.sleep(0.2)
        for idx, param, _, _, _ in items:
            try:
                self._send(f":NUMERIC:NORMAL:ITEM{idx} {param}")
                time.sleep(0.1)
            except:
                pass
        time.sleep(0.5)

    def read_file_free(self):
        resp = self._query_quick(":FILE:FREE?", timeout=3.0)
        if resp:
            try: return int(resp)
            except: pass
        return None

    def get_filename(self):
        """获取当前保存文件名（WT1804E支持）"""
        resp = self._query_quick(":FILE:SAVE:NAME?", timeout=3.0)
        if resp:
            # 解析响应格式: :FILE:SAVE:NAME "N25"
            try:
                # 去掉前缀，提取引号内的内容
                if '"' in resp:
                    filename = resp.split('"')[1]
                    return filename if filename else None
            except:
                pass
        return None

    def switch_to_usb(self):
        for drive in ["USB,0", "USB,1", "USB"]:
            try:
                self._send(f":FILE:DRIVE {drive}")
                time.sleep(0.2)
                # 注意：WT1804E 不支持 :FILE:DRIVE?，用 :FILE:PATH? 代替
                resp = self._query_quick(":FILE:PATH?", timeout=3.0)
                if resp and "USB" in resp.upper():
                    return resp
            except:
                continue
        self._send("*CLS")
        time.sleep(0.1)
        return None

    def acquire(self, items, timeout=5.0):
        old = self.instr.timeout
        self.instr.timeout = timeout
        try:
            self._clear()
            self._send(":NUMERIC:VALUE?")
            time.sleep(0.5)
            raw = self.instr.read_raw()
            if not raw:
                return None
            if raw[0:1] != b'#':
                text = raw.decode('ascii').strip()
                vals = [float(s.strip()) for s in text.split(',')]
            else:
                data = parse_ieee4882_block(raw)
                vals = parse_float32_be(data) if data else []
            results = []
            for i, (_, _, col_name, unit, ch) in enumerate(items):
                v = vals[i] if i < len(vals) else float('nan')
                v = v if is_valid(v) else float('nan')
                results.append((col_name, v, unit, ch))
            return results
        except Exception as e:
            print(f"  ❌ 采集异常: {e}")
            return None
        finally:
            self.instr.timeout = old


class WT3000Adapter:
    """WT3000 — TCP Socket 10001 + TMCTL 协议"""
    def __init__(self, ip, port=10001):
        self.ip = ip
        self.port = port
        self.sock = None
        self.model = ""
        self.original_items = {}

    def connect(self):
        import socket as _sock
        self.sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect((self.ip, self.port))
        # 读欢迎帧
        self._recv_frame(timeout=5)
        self._tmctl_send("anonymous")
        resp = self._recv_frame(timeout=5) or ""
        if "password" in resp.lower():
            self._tmctl_send("")
            resp = self._recv_frame(timeout=5) or ""
        if "ready" not in resp.lower():
            raise Exception("TMCTL 登录失败")
        resp = self._query("*IDN?")
        self.model = resp
        self._drain_errors()
        try:
            self._send(":SYSTEM:BEEP:STAT OFF")
            time.sleep(0.1)
            self._recv_frame(timeout=1)
        except:
            pass
        self._save_original_items()
        self._drain_errors()
        return True

    def disconnect(self):
        if self.sock:
            try: self.sock.close()
            except: pass
            self.sock = None

    def reconnect(self):
        """断开并重新连接，保留原始 ITEM 配置"""
        self.disconnect()
        time.sleep(1)
        self.connect()
        self.configure_items(ACQUIRE_ITEMS)

    def _tmctl_send(self, text):
        text_bytes = text.encode('ascii')
        length = len(text_bytes)
        header = bytes([0x80, 0x00, 0x00, length])
        self.sock.sendall(header + text_bytes)

    def _recv(self, timeout=3):
        """兼容旧调用——直接返回原始 bytes（内部转发到 _recv_frame）"""
        frame = self._recv_frame(timeout=timeout)
        return frame.encode("ascii", errors="replace") if frame else b""

    def _recv_frame(self, timeout=5.0):
        """读取一个完整的 TMCTL 数据帧（4字节头 + payload），跳过空帧，返回纯文本。"""
        import socket as _sock
        self.sock.settimeout(timeout)
        header = b""
        while len(header) < 4:
            try:
                chunk = self.sock.recv(4 - len(header))
            except _sock.timeout:
                return None
            if not chunk:
                return None
            header += chunk
        if header[0] != 0x80:
            payload = header[1:]
            text = payload.decode("ascii", errors="replace")
            return text
        length = header[3]
        payload = b""
        while len(payload) < length:
            try:
                chunk = self.sock.recv(length - len(payload))
            except _sock.timeout:
                return None
            if not chunk:
                return None
            payload += chunk
        text = payload.decode("ascii", errors="replace").strip()
        if not text:
            return self._recv_frame(timeout)  # 跳过空帧
        return text

    def _drain_errors(self):
        """排空设备错误队列，防止弹窗"""
        for _ in range(20):
            self._tmctl_send(":SYSTEM:ERROR?")
            time.sleep(0.1)
            resp = self._recv_frame(timeout=2) or ""
            if not resp or resp.startswith("+0") or "no error" in resp.lower():
                break

    def _parse_tmctl(self, data):
        if not data:
            return ""
        results = []
        i = 0
        while i < len(data):
            if i + 4 <= len(data) and data[i] == 0x80:
                length = data[i + 3]
                text = data[i + 4:i + 4 + length]
                if text:
                    results.append(text.decode('ascii', errors='replace'))
                i += 4 + length
            else:
                results.append(data[i:].decode('ascii', errors='replace'))
                break
        # 直接拼接，不用分隔符（避免数值被错误分割，如 10.23|9E+00）
        return "".join(results)

    def _send(self, cmd):
        self._tmctl_send(cmd)

    def _query(self, cmd):
        self._tmctl_send(cmd)
        time.sleep(0.3)
        frame = self._recv_frame(timeout=3)
        return frame if frame else ""

    def _query_quick(self, cmd, timeout=3.0):
        self._tmctl_send(cmd)
        frame = self._recv_frame(timeout=timeout)
        return frame if frame else ""

    def _save_original_items(self):
        self.original_items = {}
        # 先查询当前配置的 ITEM 数量，避免查询不存在的 ITEM 触发 Error 113
        resp = self._query(":NUMERIC:NORMAL:NUMBER?")
        try:
            num_items = int(resp.strip()) if resp else 0
        except:
            num_items = 0
        for i in range(1, num_items + 1):
            resp = self._query(f":NUMERIC:NORMAL:ITEM{i}?")
            if not resp:
                break
            parts = resp.replace(":", " ").split()
            param_parts = []
            for p in parts:
                if p.upper() not in ("NUM", "ITEM", "NUM:ITEM") and not p.isdigit():
                    param_parts.append(p)
            if param_parts:
                self.original_items[i] = ",".join(param_parts)

    def _restore_original_items(self):
        for idx, param in self.original_items.items():
            try:
                self._send(f":NUMERIC:NORMAL:ITEM{idx} {param}")
                time.sleep(0.05)
                self._recv_frame(timeout=1)
            except:
                pass

    def configure_items(self, items):
        num = len(items)
        self._send(f":NUMERIC:NORMAL:NUMBER {num}")
        time.sleep(0.2)
        self._recv_frame(timeout=1)
        for idx, param, _, _, _ in items:
            short_name = WT3000_PARAM_MAP.get(param, param)
            self._send(f":NUMERIC:NORMAL:ITEM{idx} {short_name}")
            time.sleep(0.1)
            self._recv_frame(timeout=1)
        time.sleep(0.5)

    def read_file_free(self):
        resp = self._query(":FILE:FREE?")
        if resp:
            try: return int(resp.strip())
            except: pass
        return None

    def switch_to_usb(self):
        # 清除设备端积压的未读响应，避免 -410 Query INTERRUPTED
        self._send("*CLS")
        time.sleep(0.2)
        self._recv_frame(timeout=1)
        for drive in ["USB,0", "USB,1", "USB"]:
            try:
                self._send(f":FILE:DRIVE {drive}")
                time.sleep(0.3)
                self._recv_frame(timeout=0.5)  # 读走 :FILE:DRIVE 的响应/错误
                self._send(":FILE:PATH?")
                time.sleep(0.3)
                resp = self._recv_frame(timeout=3)
                if resp and "USB" in resp.upper():
                    return resp
            except:
                continue
        self._send("*CLS")
        time.sleep(0.1)
        self._recv_frame(timeout=1)
        return None

    def get_filename(self):
        """获取当前保存文件名（WT3000支持 :STORE:FILE:NAME? 和 :IMAGE:SAVE:NAME?）"""
        # 尝试数值/存储文件名
        resp = self._query(":STORE:FILE:NAME?")
        if resp and '"' in resp:
            try:
                filename = resp.split('"')[1]
                if filename:
                    return filename
            except:
                pass
        # 尝试图片文件名
        resp = self._query(":IMAGE:SAVE:NAME?")
        if resp and '"' in resp:
            try:
                filename = resp.split('"')[1]
                if filename:
                    return filename
            except:
                pass
        return None

    def acquire(self, items):
        try:
            self._tmctl_send(":NUMERIC:VALUE?")
            # TMCTL 数据帧 payload 上限 255 字节，34 个 ITEM 的值会被拆成
            # 多帧返回，必须循环读取累积，直到拿到足够的数值为止。
            want = len(items)
            chunks = []
            deadline = time.time() + 5.0
            vals = []
            while time.time() < deadline:
                frame = self._recv_frame(timeout=1.0)
                if frame:
                    chunks.append(frame)
                    text = ",".join(chunks)
                    vals = [float(s.strip()) for s in text.split(',') if s.strip()]
                    if len(vals) >= want:
                        break
                else:
                    # 一帧都不来有可能是连接断了，直接放弃
                    if not chunks and self.sock is None:
                        return None
                    # 已拿到部分值但长时间没有后续帧，视为响应结束
                    if chunks:
                        break
            if not vals:
                return None
            results = []
            for i, (_, _, col_name, unit, ch) in enumerate(items):
                v = vals[i] if i < len(vals) else float('nan')
                v = v if is_valid(v) else float('nan')
                results.append((col_name, v, unit, ch))
            return results
        except Exception as e:
            print(f"  ❌ 采集异常: {e}")
            return None


def detect_power_device(ip):
    """自动检测功率分析仪类型"""
    import socket as _sock
    # 试 VXI-11
    try:
        import vxi11
        instr = vxi11.Instrument(ip)
        instr.timeout = 5000
        instr.write("*IDN?\n")
        time.sleep(0.5)
        raw = instr.read_raw()
        if raw:
            model = raw.decode('ascii').strip()
            instr.close()
            if "YOKOGAWA" in model:
                return "vxi11", model
    except:
        pass
    # 试 TCP TMCTL
    try:
        sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((ip, 10001))
        data = b""
        sock.settimeout(3)
        try:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except:
            pass
        sock.close()
        if b"username" in data:
            return "tmctl", "WT3000"
    except:
        pass
    return None, None


# ============================================================
# 示波器通信层（从 test_file_monitor.py 复用）
# ============================================================

class OscilloscopeAdapter:
    """DLM5000 — pyvisa TCPIP"""
    def __init__(self, ip):
        self.ip = ip
        self.rm = None
        self.inst = None

    def connect(self):
        import pyvisa
        self.rm = pyvisa.ResourceManager('@py')
        self.inst = self.rm.open_resource(f"TCPIP0::{self.ip}::INSTR")
        self.inst.timeout = 5000
        self.inst.read_termination = "\n"
        self.inst.write_termination = "\n"
        time.sleep(0.2)
        return True

    def disconnect(self):
        try:
            if self.inst: self.inst.close()
            if self.rm: self.rm.close()
        except:
            pass

    def query(self, cmd):
        try:
            self.inst.write(cmd)
            time.sleep(0.03)
            return self.inst.read().strip()
        except:
            # 自动重连
            try: self.inst.close()
            except: pass
            try: self.rm.close()
            except: pass
            time.sleep(0.3)
            import pyvisa
            self.rm = pyvisa.ResourceManager('@py')
            self.inst = self.rm.open_resource(f"TCPIP0::{self.ip}::INSTR")
            self.inst.timeout = 5000
            self.inst.read_termination = "\n"
            self.inst.write_termination = "\n"
            time.sleep(0.2)
            try:
                self.inst.write(cmd)
                time.sleep(0.03)
                return self.inst.read().strip()
            except:
                return None

    def get_filename(self):
        resp = self.query(":FILE:SAVE:NAME?")
        if resp is None:
            return None
        if '"' in resp:
            return resp.split('"')[1]
        return resp.split()[-1]

    def read_file_free(self):
        """读取存储空间剩余字节数"""
        resp = self.query(":FILE:FREE?")
        if resp:
            try:
                # 解析如 ":FILE:FREE 346.29222E+06" 格式的响应
                parts = resp.split()
                if len(parts) >= 2:
                    return int(float(parts[-1]))
            except:
                pass
        return None

    def read_all(self):
        results = {}
        for ch in OSC_CHANNELS:
            for m in OSC_MEAS:
                try:
                    resp = self.query(f":MEAS:CHAN{ch}:{m}:VAL?")
                    if resp:
                        try: results[f"CH{ch}_{m}"] = float(resp.split()[-1])
                        except: results[f"CH{ch}_{m}"] = float('nan')
                    else: results[f"CH{ch}_{m}"] = float('nan')
                except: results[f"CH{ch}_{m}"] = float('nan')
            mx = results.get(f"CH{ch}_MAX", float('nan'))
            mn = results.get(f"CH{ch}_MIN", float('nan'))
            if mx == mx and mn == mn:
                results[f"CH{ch}_PP"] = mx - mn
            else:
                results[f"CH{ch}_PP"] = float('nan')
        return results


# ============================================================
# 设备监控线程
# ============================================================

def monitor_oscilloscope(device):
    """示波器监控线程：存储空间变化触发"""
    device_id = device["id"]
    ip = device["ip"]
    tag = f"[{device_id}]"

    adapter = OscilloscopeAdapter(ip)
    # 启动重试：连接失败循环重试，直到成功或收到停止信号
    while not stop_event.is_set():
        update_device_state(device_id, "connecting", ip)
        print(f"  {tag} 连接示波器 {ip}...", flush=True)
        try:
            adapter.connect()
            break
        except Exception as e:
            update_device_state(device_id, "connecting", ip, error_msg=str(e))
            print(f"  {tag} ❌ 连接失败: {e}，5秒后重试...", flush=True)
            for _ in range(5):
                if stop_event.is_set():
                    return
                time.sleep(1)
    else:
        return

    print(f"  {tag} ✅ 连接成功", flush=True)
    update_device_state(device_id, "connected", "DLM5000")
    _set_counter(device_id, 0)

    try:
        # 初始化空间监控
        last_free = adapter.read_file_free()
        storage_ready = last_free is not None and last_free > 0
        if storage_ready:
            print(f"  {tag} 💾 初始空间: {last_free/1024/1024:.1f} MB")
        else:
            print(f"  {tag} ⚠️ 未检测到存储，等待插入...")
            last_free = 0

        # 冷却期设置
        cooldown = 0
        CONSECUTIVE_ERRORS = 0
        storage_check_counter = 0

        while not stop_event.is_set():
            time.sleep(1)
            
            if cooldown > 0:
                cooldown -= 1
                if cooldown <= 0:
                    # 冷却结束，同步一次空间值
                    free = adapter.read_file_free()
                    if free is not None:
                        last_free = free
                continue
            
            # 尝试读取当前空间
            free = adapter.read_file_free()
            
            # 存储状态检测
            if free is None or free == 0:
                if storage_ready:
                    # 存储从有到无，可能被拔出
                    print(f"  {tag} ⚠️ 存储已移除，等待...")
                    storage_ready = False
                    last_free = 0
                CONSECUTIVE_ERRORS += 1
                if CONSECUTIVE_ERRORS >= 3:
                    # 不退出，继续等待存储插入
                    CONSECUTIVE_ERRORS = 0
                continue
            else:
                CONSECUTIVE_ERRORS = 0
                if not storage_ready:
                    # 存储从无到有，检测到插入
                    storage_ready = True
                    last_free = free
                    print(f"  {tag} 🔌 检测到存储! ({free/1024/1024:.1f} MB)")
                    continue
            
            # 计算空间变化
            diff = last_free - free

            # DEBUG：记录微小变化（不触发采集），用于观察仪器后台写入波动
            if 0 < diff < 100:
                print(f"  {tag} 📊 空间微小变化: {diff:,} bytes，未触发采集")

            # 过滤异常值：保存的文件一般不超过 100MB，单次写入通常 > 100B
            if abs(diff) > 100 * 1024 * 1024:
                print(f"  {tag} ⚠️ 空间变化异常 ({diff:,} bytes)，跳过")
                last_free = free
                continue

            # 空间减少，触发采集
            if diff >= 100:
                count = _inc_counter(device_id)
                now_str = datetime.now().strftime("%H:%M:%S")
                print(f"  {tag} 🔔 [{now_str}] 空间变化: -{diff/1024:.1f} KB")

                t0 = time.time()
                meas = adapter.read_all()
                
                # 使用当前文件名作为标识
                name = adapter.get_filename() or f"IMG_{count:04d}"

                save_osc_csv(device_id, count, name, meas)
                save_oscilloscope_to_db(device_id, count, name, meas)
                update_device_state(device_id, "connected", "DLM5000")
                print(f"  {tag} ✅ #{count} 耗时 {time.time()-t0:.2f}s", flush=True)
                
                last_free = free
                cooldown = 3  # 3秒冷却期
            else:
                last_free = free

    except Exception as e:
        update_device_state(device_id, "error", error_msg=str(e))
        print(f"  {tag} ❌ 异常: {e}", flush=True)
    finally:
        adapter.disconnect()
        update_device_state(device_id, "not_running")
        print(f"  {tag} 已断开", flush=True)


def _try_reconnect(adapter, tag, max_retries=3):
    """尝试重新连接设备，成功返回 True"""
    for attempt in range(1, max_retries + 1):
        print(f"  {tag} 🔄 重连中 ({attempt}/{max_retries})...")
        try:
            adapter.reconnect()
            print(f"  {tag} ✅ 重连成功")
            return True
        except Exception as e:
            print(f"  {tag} ⚠️ 重连失败: {e}")
            if attempt < max_retries:
                time.sleep(3 * attempt)
    print(f"  {tag} ❌ 重连放弃，{max_retries} 次均失败")
    return False


def _acquire_with_retry(adapter, items, tag, max_retries=2):
    """带重试的采集，失败时自动重连后重试"""
    for attempt in range(1, max_retries + 1):
        results = adapter.acquire(items)
        if results is not None:
            return results
        if attempt < max_retries:
            print(f"  {tag} ⚠️ 采集失败，重连后重试 ({attempt}/{max_retries})")
            if not _try_reconnect(adapter, tag, max_retries=2):
                break
    return None


def monitor_power_analyzer(device):
    """功率分析仪监控线程：IMAGE SAVE 触发"""
    device_id = device["id"]
    ip = device["ip"]
    tag = f"[{device_id}]"
    POLL_INTERVAL = 1.0       # 轮询间隔（秒），0.5s 对 VXI-11 负担过重
    CONSECUTIVE_ERRORS = 0    # 连续错误计数
    MAX_CONSECUTIVE_ERRORS = 3  # 连续错误达到此值触发重连

    # 根据设备型号选择适配器（跳过不可靠的自动检测）
    model_name = device.get("model", "").upper()
    if "WT3000" in model_name:
        adapter = WT3000Adapter(ip)
        print(f"  {tag} 🔍 WT3000 系列 (TCP TMCTL) @ {ip}", flush=True)
    else:
        adapter = WT1800Adapter(ip)
        print(f"  {tag} 🔍 WT1800 系列 (VXI-11) @ {ip}", flush=True)

    # 启动重试：连接失败循环重试，直到成功或收到停止信号
    while not stop_event.is_set():
        update_device_state(device_id, "connecting", ip)
        print(f"  {tag} 连接设备 {ip}...", flush=True)
        try:
            adapter.connect()
            break
        except Exception as e:
            update_device_state(device_id, "connecting", ip, error_msg=str(e))
            print(f"  {tag} ❌ 连接失败: {e}，5秒后重试...", flush=True)
            for _ in range(5):
                if stop_event.is_set():
                    return
                time.sleep(1)
    else:
        return

    try:
        # USB 检测
        usb_drive = adapter.switch_to_usb()
        if usb_drive:
            print(f"  {tag} ✅ USB: {usb_drive}", flush=True)
            free = adapter.read_file_free()
            if free is not None:
                print(f"  {tag} 💾 剩余: {free/1024/1024:.1f} MB", flush=True)
            else:
                free = 0
        else:
            print(f"  {tag} ⚠️ 未检测到USB，等待插入...", flush=True)
            free = 0

        # 配置
        adapter.configure_items(ACQUIRE_ITEMS)
    except Exception as e:
        update_device_state(device_id, "error", error_msg=str(e))
        print(f"  {tag} ❌ 初始化异常: {e}，5秒后重试...", flush=True)
        adapter.disconnect()
        time.sleep(5)
        return

    update_device_state(device_id, "connected", adapter.model)

    _set_counter(device_id, 0)
    last_free = free
    last_filename = ""  # 记录上一次的文件名，用于去重
    cooldown = 0
    usb_check_counter = 0
    usb_ready = usb_drive is not None

    print(f"  {tag} ✅ 监控中 (轮询间隔 {POLL_INTERVAL}s)...")

    while not stop_event.is_set():
        try:
            time.sleep(POLL_INTERVAL)
            if cooldown > 0:
                cooldown -= POLL_INTERVAL
                if cooldown <= 0:
                    # 冷却结束，同步一次空间值（设备可能还在写文件）
                    free = adapter.read_file_free()
                    if free is not None:
                        last_free = free
                continue

            if not usb_ready:
                usb_check_counter += POLL_INTERVAL
                if usb_check_counter >= 3.0:
                    usb_check_counter = 0
                    usb_drive = adapter.switch_to_usb()
                    if usb_drive:
                        usb_ready = True
                        free = adapter.read_file_free()
                        if free is not None:
                            last_free = free
                            print(f"  {tag} 🔌 检测到U盘! ({free/1024/1024:.1f} MB)")
                    else:
                        # 直接重连，恢复设备干净状态后再检测
                        print(f"  {tag} 🔄 USB检测失败，重连设备...")
                        if _try_reconnect(adapter, tag):
                            usb_drive = adapter.switch_to_usb()
                            if usb_drive:
                                usb_ready = True
                                free = adapter.read_file_free()
                                if free is not None:
                                    last_free = free
                                    print(f"  {tag} 🔌 检测到U盘! ({free/1024/1024:.1f} MB)")
                    continue

            free = adapter.read_file_free()
            if free is None:
                CONSECUTIVE_ERRORS += 1
                # 清除设备端未读响应，避免下轮 -410 Query INTERRUPTED
                adapter._send("*CLS")
                time.sleep(0.1)
                adapter._recv(0.5)
                if CONSECUTIVE_ERRORS >= MAX_CONSECUTIVE_ERRORS:
                    print(f"  {tag} ⚠️ 连续 {CONSECUTIVE_ERRORS} 次读取失败")
                    if _try_reconnect(adapter, tag):
                        CONSECUTIVE_ERRORS = 0
                        usb_drive = adapter.switch_to_usb()
                        if usb_drive:
                            usb_ready = True
                        free = adapter.read_file_free()
                        if free is not None:
                            last_free = free
                    else:
                        update_device_state(device_id, "error", error_msg="重连失败")
                        CONSECUTIVE_ERRORS = 0
                continue

            CONSECUTIVE_ERRORS = 0  # 读取成功，重置计数

            diff = last_free - free
            # 过滤异常值：IMAGE SAVE 文件一般不超过 100MB
            if diff > 100 * 1024 * 1024:
                print(f"  {tag} ⚠️ 空间变化异常 ({diff:,} bytes)，跳过")
                last_free = free
                continue
            if diff >= 100:
                count = _inc_counter(device_id)
                now_str = datetime.now().strftime("%H:%M:%S")
                print(f"  {tag} 🔔 [{now_str}] IMAGE SAVE! 空间变化: {diff:,} bytes")

                # 立即更新 last_free，防止获取文件名期间重复触发
                last_free = free
                t0 = time.time()
                results = _acquire_with_retry(adapter, ACQUIRE_ITEMS, tag)

                if results:
                    print(f"  {tag} ✅ #{count}", end="")
                    for ch_num in [1, 2, 3, 4]:
                        ch_results = [(n, v, u) for n, v, u, c in results if c == ch_num]
                        if not ch_results:
                            continue
                        print(f"\n     Ch{ch_num}:", end="")
                        for name, val, unit in ch_results:
                            if val == val:
                                u = f"{unit}" if unit else ""
                                print(f"  {name}={val:g}{u}", end="")
                            else:
                                print(f"  {name}=N/A", end="")
                    print()

                    # 获取文件名（WT1804E/WT3000都支持）
                    filename = ""
                    if hasattr(adapter, 'get_filename'):
                        try:
                            for _ in range(5):
                                time.sleep(0.2)
                                filename = adapter.get_filename() or ""
                                if filename:
                                    break
                            if filename:
                                print(f"  {tag} 📁 文件名: {filename}")
                            else:
                                # 获取不到时用时间戳代替
                                filename = datetime.now().strftime("SAVE_%H%M%S")
                                print(f"  {tag} 📁 时间戳文件名: {filename}")
                        except Exception as e:
                            print(f"  {tag} ⚠️ 获取文件名失败: {e}")

                    save_pwr_csv(device_id, count, results)
                    save_power_to_db(device_id, count, results, adapter.model, filename)
                    update_device_state(device_id, "connected", adapter.model)
                    print(f"  {tag} 耗时 {time.time()-t0:.2f}s", flush=True)
                else:
                    print(f"  {tag} ❌ #{count} 采集失败（重试后仍失败）")

                cooldown = 5.0  # 延长冷却期到5秒
            else:
                last_free = free

        except Exception as e:
            CONSECUTIVE_ERRORS += 1
            update_device_state(device_id, "error", error_msg=str(e))
            print(f"  {tag} ❌ 异常: {e}", flush=True)
            if CONSECUTIVE_ERRORS >= MAX_CONSECUTIVE_ERRORS:
                update_device_state(device_id, "reconnecting", adapter.model)
                if _try_reconnect(adapter, tag):
                    CONSECUTIVE_ERRORS = 0
                    usb_drive = adapter.switch_to_usb()
                    if usb_drive:
                        usb_ready = True
                else:
                    update_device_state(device_id, "error", error_msg="重连失败")
                    CONSECUTIVE_ERRORS = 0

    try:
        adapter._restore_original_items()
    except:
        pass
    adapter.disconnect()
    update_device_state(device_id, "not_running")
    print(f"  {tag} 已断开", flush=True)


# ============================================================
# 主程序
# ============================================================

def _start_thread(dev):
    """为设备启动监控线程"""
    if dev["type"] == "oscilloscope":
        t = threading.Thread(target=monitor_oscilloscope, args=(dev,), daemon=True, name=f"mon-{dev['id']}")
    else:
        t = threading.Thread(target=monitor_power_analyzer, args=(dev,), daemon=True, name=f"mon-{dev['id']}")
    t.start()
    return t


def main(device_filter=None):
    print("=" * 60)
    print("  多设备统一监控系统")
    print(f"  数据库: {DB_PATH}")
    print(f"  CSV目录: {CSV_DIR}")
    print(f"  脚本目录: {SCRIPT_DIR}")
    print("=" * 60)

    # 加载设备列表
    DEVICES = load_devices()

    # 按设备 ID 过滤
    if device_filter:
        DEVICES = [d for d in DEVICES if d["id"] == device_filter]
        if not DEVICES:
            print(f"  ❌ 未找到设备: {device_filter}")
            return
        print(f"\n🔍 单设备模式: {device_filter}")

    print(f"\n📋 设备列表 ({len(DEVICES)} 台):")
    for i, dev in enumerate(DEVICES):
        type_name = "示波器" if dev["type"] == "oscilloscope" else "功率分析仪"
        print(f"  {i+1}. [{dev['id']}] {dev['model']} ({type_name}) @ {dev['ip']}")

    # 初始化数据库
    init_db()
    os.makedirs(CSV_DIR, exist_ok=True)

    # 启动设备线程
    thread_map = {}  # device_id -> (thread, device)
    restart_counts = {}  # device_id -> 连续重启次数
    RESTART_COOLDOWN = 30  # 重启冷却时间（秒），连续重启间隔递增
    MAX_RESTARTS = 20      # 最大连续重启次数

    for dev in DEVICES:
        t = _start_thread(dev)
        thread_map[dev["id"]] = (t, dev)
        restart_counts[dev["id"]] = 0

    print(f"\n{'=' * 60}")
    print(f"  🔄 所有设备已启动，Ctrl+C 停止（含线程看门狗）")
    print(f"{'=' * 60}\n")

    try:
        watchdog_tick = 0
        while True:
            time.sleep(5)
            watchdog_tick += 5

            # 每 5 秒检查一次线程存活状态
            for dev_id, (t, dev) in list(thread_map.items()):
                if stop_event.is_set():
                    break
                if not t.is_alive():
                    count = restart_counts[dev_id]
                    if count >= MAX_RESTARTS:
                        print(f"  [{dev_id}] ❌ 已重启 {count} 次，放弃重启")
                        continue
                    wait = min(RESTART_COOLDOWN * (2 ** min(count, 4)), 300)
                    print(f"  [{dev_id}] ⚠️ 监控线程已退出，{wait}秒后重启 (第{count+1}次)...")
                    time.sleep(wait)
                    if stop_event.is_set():
                        break
                    new_t = _start_thread(dev)
                    thread_map[dev_id] = (new_t, dev)
                    restart_counts[dev_id] = count + 1
                    print(f"  [{dev_id}] 🔄 已重启监控线程")

            # 每 10 分钟重置重启计数（说明设备已稳定运行）
            if watchdog_tick >= 600:
                watchdog_tick = 0
                for dev_id in restart_counts:
                    if restart_counts[dev_id] > 0:
                        t, _ = thread_map[dev_id]
                        if t.is_alive():
                            restart_counts[dev_id] = 0

    except KeyboardInterrupt:
        pass

    stop_event.set()
    print(f"\n⏹️ 停止监控...")
    time.sleep(2)

    # 清理本进程在状态文件中的条目
    if device_filter:
        with state_lock:
            device_states.pop(device_filter, None)
            try:
                with open(STATUS_FILE + ".tmp", 'w', encoding='utf-8') as f:
                    json.dump(device_states, f, ensure_ascii=False, indent=2)
                os.replace(STATUS_FILE + ".tmp", STATUS_FILE)
            except Exception:
                pass
    else:
        try:
            os.remove(STATUS_FILE)
        except FileNotFoundError:
            pass

    with counter_lock:
        counters_snapshot = dict(record_counters)
    total = sum(counters_snapshot.values())
    print(f"\n📊 统计:")
    for dev_id, count in counters_snapshot.items():
        print(f"  {dev_id}: {count} 条")
    print(f"  总计: {total} 条")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="多设备统一监控系统")
    parser.add_argument("--device", default=None, help="只监控指定设备 ID")
    args = parser.parse_args()
    main(device_filter=args.device)
