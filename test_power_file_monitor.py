#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# test_power_file_monitor.py — 功率分析仪 IMAGE SAVE 触发采集
# =================================================================
# 支持: WT1800 系列 (VXI-11) + WT3000 系列 (TCP TMCTL)
# 触发: IMAGE SAVE → U 盘空间变化 → 自动采集
# 依赖：pip install python-vxi11
# =================================================================

import math
import os
import re
import socket
import struct
import sys
import time
import csv
from datetime import datetime

# ── 配置（可修改） ──
IP = "172.17.76.211"
TIMEOUT = 15000
POLL_INTERVAL = 0.5
MIN_SPACE_CHANGE = 100

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(SCRIPT_DIR, "power_file_monitor_output.csv")
# 数据库路径：统一到 db_viewer.py 所在项目的 data 目录
# db_viewer.py 位于 instrument_acquisition/，数据库在 instrument_acquisition/data/acquisition.db
_PROJECT_DIR = os.path.dirname(SCRIPT_DIR) if os.path.basename(SCRIPT_DIR).startswith("test") else SCRIPT_DIR
DB_PATH = os.path.join(_PROJECT_DIR, "data", "acquisition.db")

# ── 固定采集参数 ──
# 格式: (ITEM索引, SCPI参数, 列名, 单位, 通道)
ACQUIRE_ITEMS = [
    # ---- Channel 1 (ITEM 1-9) ----
    (1,  "URMS,1",    "URMS",    "V",  1),
    (2,  "IRMS,1",    "IRMS",    "A",  1),
    (3,  "P,1",       "P",       "W",  1),
    (4,  "LAMB,1",    "LAMB",    "",   1),
    (5,  "ITHD,1",    "ITHD",    "%",  1),
    (6,  "IHDF,1,3", "IK3",     "%",  1),
    (7,  "IHDF,1,5", "IK5",     "%",  1),
    (8,  "IHDF,1,7", "IK7",     "%",  1),
    (9,  "IHDF,1,9", "IK9",     "%",  1),
    # ---- Channel 2 (ITEM 10-17) ----
    (10, "URMS,2",    "URMS",    "V",  2),
    (11, "IRMS,2",    "IRMS",    "A",  2),
    (12, "P,2",       "P",       "W",  2),
    (13, "IPP,2",     "IPPeak",  "A",  2),
    (14, "IMP,2",     "IMPeak",  "A",  2),
    (15, "F1",        "F1",      "A",  2),
    (16, "IDC,2",     "IDC",     "A",  2),
    (17, "F2",        "F2",      "%",  2),
    # ---- Channel 3 = Ch1 参数 (ITEM 18-26) ----
    (18, "URMS,3",    "URMS",    "V",  3),
    (19, "IRMS,3",    "IRMS",    "A",  3),
    (20, "P,3",       "P",       "W",  3),
    (21, "LAMB,3",    "LAMB",    "",   3),
    (22, "ITHD,3",    "ITHD",    "%",  3),
    (23, "IHDF,3,3",  "IK3",     "%",  3),
    (24, "IHDF,3,5",  "IK5",     "%",  3),
    (25, "IHDF,3,7",  "IK7",     "%",  3),
    (26, "IHDF,3,9",  "IK9",     "%",  3),
    # ---- Channel 4 = Ch2 参数 (ITEM 27-34) ----
    (27, "URMS,4",    "URMS",    "V",  4),
    (28, "IRMS,4",    "IRMS",    "A",  4),
    (29, "P,4",       "P",       "W",  4),
    (30, "IPP,4",     "IPPeak",  "A",  4),
    (31, "IMP,4",     "IMPeak",  "A",  4),
    (32, "F3",        "F3",      "A",  4),
    (33, "IDC,4",     "IDC",     "A",  4),
    (34, "F4",        "F4",      "%",  4),
]

# WT3000 参数名映射（短名）
WT3000_PARAM_MAP = {
    "URMS,1":    "U,1",    "IRMS,1":    "I,1",    "P,1":       "P,1",
    "LAMB,1":    "LAMB,1", "ITHD,1":    "ITHD,1",
    "IHDFk,1,3": "IHDF,1,3", "IHDFk,1,5": "IHDF,1,5",
    "IHDFk,1,7": "IHDF,1,7", "IHDFk,1,9": "IHDF,1,9",
    "URMS,2":    "U,2",    "IRMS,2":    "I,2",    "P,2":       "P,2",
    "IPPeak,2":  "IPP,2",  "IMPeak,2":  "IMP,2",
    "F1,2":      "F1,2",   "IMN,2":     "F3,2",   "F2,2":      "F2,2",
    # Ch3 = Ch1 参数
    "URMS,3":    "U,3",    "IRMS,3":    "I,3",    "P,3":       "P,3",
    "LAMB,3":    "LAMB,3", "ITHD,3":    "ITHD,3",
    "IHDFk,3,3": "IHDF,3,3", "IHDFk,3,5": "IHDF,3,5",
    "IHDFk,3,7": "IHDF,3,7", "IHDFk,3,9": "IHDF,3,9",
    # Ch4 = Ch2 参数
    "URMS,4":    "U,4",    "IRMS,4":    "I,4",    "P,4":       "P,4",
    "IPPeak,4":  "IPP,4",  "IMPeak,4":  "IMP,4",
    "F3,4":      "F3,4",   "IDC,4":     "IDC,4",   "F4,4":      "F4,4",
}


# ============================================================
# 通信层
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


def detect_device_type(ip):
    """自动检测设备类型：先试 VXI-11，再试 TCP TMCTL"""
    # 试 VXI-11 (WT1800 系列)
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

    # 试 TCP TMCTL (WT3000 系列)
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
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
        except socket.timeout:
            pass
        sock.close()
        if b"username" in data:
            return "tmctl", "WT3000"
    except:
        pass

    return None, None


# ============================================================
# WT1800 适配器 (VXI-11)
# ============================================================

class WT1800Adapter:
    """WT1800/WT1802E/WT1804E — VXI-11 协议"""

    def __init__(self, ip, timeout=15000):
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
        print(f"  ✅ 连接成功: {self.model}")
        self._save_original_items()
        return True

    def disconnect(self):
        if self.instr:
            try:
                self.instr.close()
            except:
                pass

    def _clear(self):
        try:
            while True:
                raw = self.instr.read_raw()
                if raw is None or len(raw) == 0:
                    break
        except:
            pass

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

    def _save_original_items(self):
        self.original_items = {}
        for i in range(1, 20):
            resp = self._query(f":NUMERIC:NORMAL:ITEM{i}?")
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
        """配置数据输出项"""
        print(f"  ⚙️  配置 :NUMERIC:NORMAL:ITEM...")
        # NUMBER 必须 >= 最大 ITEM 索引，否则 ITEM37 无法读取
        num = max(idx for idx, _, _, _, _ in items)
        self._send(f":NUMERIC:NORMAL:NUMBER {num}")
        time.sleep(0.2)

        for idx, param, _, _, _ in items:
            try:
                self._send(f":NUMERIC:NORMAL:ITEM{idx} {param}")
                time.sleep(0.1)
            except:
                pass
        time.sleep(0.5)

        # 验证
        failed = []
        for idx, param, name, unit, ch in items:
            resp = self._query(f":NUMERIC:NORMAL:ITEM{idx}?")
            col = f"{name}({unit})" if unit else name
            if resp:
                parts = resp.split()
                actual = parts[-1] if len(parts) >= 2 else resp
                if actual.upper().replace(" ", "") == param.upper().replace(" ", ""):
                    print(f"  ✅ ITEM{idx:2d} [{col}] → {actual}")
                else:
                    print(f"  ❌ ITEM{idx:2d} [{col}] 期望 {param} → 实际 {actual}")
                    failed.append((idx, param))
            else:
                print(f"  ❌ ITEM{idx:2d} [{col}] 无响应")
                failed.append((idx, param))

        if failed:
            print(f"\n  ⚠️ {len(failed)} 个参数配置失败")
        else:
            print(f"\n  ✅ {len(items)} 个参数全部配置成功")
        return len(failed) == 0

    def read_file_free(self):
        resp = self._query(":FILE:FREE?")
        if resp:
            try:
                return int(resp)
            except:
                pass
        return None

    def switch_to_usb(self):
        for drive in ["USB,0", "USB,1", "USB"]:
            try:
                self._send(f":FILE:DRIVE {drive}")
                time.sleep(0.2)
                resp = self._query(":FILE:DRIVE?")
                if resp and "USB" in resp.upper():
                    return resp
            except:
                continue
        self._send("*CLS")
        time.sleep(0.1)
        return None

    def acquire(self, items):
        """一次读取全部值"""
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


# ============================================================
# WT3000 适配器 (TCP TMCTL)
# ============================================================

class WT3000Adapter:
    """WT3000 — TCP Socket 10001 + TMCTL 协议"""

    def __init__(self, ip, port=10001):
        self.ip = ip
        self.port = port
        self.sock = None
        self.model = ""
        self.original_items = {}

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect((self.ip, self.port))

        # TMCTL 登录
        data = self._recv(5)
        self._tmctl_send("anonymous")
        data = self._recv()
        if b"password" in data:
            self._tmctl_send("")
            data = self._recv()
        if b"ready" not in data:
            raise Exception("TMCTL 登录失败")

        # 识别
        resp = self._query("*IDN?")
        self.model = resp
        print(f"  ✅ 连接成功: {resp}")
        self._save_original_items()
        return True

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
            except:
                pass

    def _tmctl_send(self, text):
        text_bytes = text.encode('ascii')
        length = len(text_bytes)
        header = bytes([0x80, 0x00, 0x00, length])
        self.sock.sendall(header + text_bytes)

    def _recv(self, timeout=3):
        data = b""
        self.sock.settimeout(timeout)
        try:
            while True:
                chunk = self.sock.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        return data

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
        return "|".join(results)

    def _send(self, cmd):
        self._tmctl_send(cmd)

    def _query(self, cmd):
        self._tmctl_send(cmd)
        time.sleep(0.3)
        data = self._recv()
        return self._parse_tmctl(data)

    def _save_original_items(self):
        self.original_items = {}
        for i in range(1, 20):
            resp = self._query(f":NUMERIC:NORMAL:ITEM{i}?")
            if not resp:
                break
            # 响应格式: ":NUM:ITEM U,1,TOT" 或 ":NUM:ITEM2 I,1,TOT"
            parts = resp.replace(":", " ").split()
            # 找到参数部分（跳过 NUM ITEM 等关键词）
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
                self._recv(1)
            except:
                pass

    def configure_items(self, items):
        """配置数据输出项（使用 WT3000 短名）"""
        print(f"  ⚙️  配置 :NUMERIC:NORMAL:ITEM（WT3000 短名）...")
        num = len(items)
        self._send(f":NUMERIC:NORMAL:NUMBER {num}")
        time.sleep(0.2)
        self._recv(1)

        for idx, param, _, _, _ in items:
            short_name = WT3000_PARAM_MAP.get(param, param)
            self._send(f":NUMERIC:NORMAL:ITEM{idx} {short_name}")
            time.sleep(0.1)
            self._recv(1)
        time.sleep(0.5)

        # 验证
        failed = []
        for idx, param, name, unit, ch in items:
            resp = self._query(f":NUMERIC:NORMAL:ITEM{idx}?")
            col = f"{name}({unit})" if unit else name
            short_name = WT3000_PARAM_MAP.get(param, param)
            if resp and short_name.split(",")[0].upper() in resp.upper():
                print(f"  ✅ ITEM{idx:2d} [{col}] → {resp}")
            else:
                print(f"  ❌ ITEM{idx:2d} [{col}] 期望 {short_name} → 实际 {resp}")
                failed.append((idx, param))

        if failed:
            print(f"\n  ⚠️ {len(failed)} 个参数配置失败")
        else:
            print(f"\n  ✅ {len(items)} 个参数全部配置成功")
        return len(failed) == 0

    def read_file_free(self):
        resp = self._query(":FILE:FREE?")
        if resp:
            try:
                return int(resp.strip())
            except:
                pass
        return None

    def switch_to_usb(self):
        for drive in ["USB,0", "USB,1", "USB"]:
            self._send(f":FILE:DRIVE {drive}")
            time.sleep(0.2)
            resp = self._query(":FILE:DRIVE?")
            if resp and "USB" in resp.upper():
                return resp
        self._send("*CLS")
        time.sleep(0.1)
        self._recv(1)
        return None

    def acquire(self, items):
        """一次读取全部值"""
        try:
            self._tmctl_send(":NUMERIC:VALUE?")
            time.sleep(0.5)
            data = self._recv()
            text = self._parse_tmctl(data)
            if not text:
                return None
            vals = [float(s.strip()) for s in text.split(',')]

            results = []
            for i, (_, _, col_name, unit, ch) in enumerate(items):
                v = vals[i] if i < len(vals) else float('nan')
                v = v if is_valid(v) else float('nan')
                results.append((col_name, v, unit, ch))
            return results
        except Exception as e:
            print(f"  ❌ 采集异常: {e}")
            return None


# ============================================================
# CSV 存储
# ============================================================

def save_csv(record_count, results):
    columns = ["序号", "采集时间"]
    for name, _, unit, _ in results:
        columns.append(f"{name}({unit})" if unit else name)

    rows = []
    try:
        with open(CSV_PATH, 'r', encoding='utf-8-sig') as f:
            reader = csv.reader(f)
            header = next(reader)
            rows = list(reader)
    except:
        header = columns

    row = [record_count, datetime.now().strftime("%Y-%m-%d %H:%M:%S")]
    for name, val, unit, _ in results:
        row.append("" if math.isnan(val) else f"{val:.6g}")
    rows.append(row)

    with open(CSV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
        csv.writer(f).writerow(columns)
        csv.writer(f).writerows(rows)


# ============================================================
# SQLite 存储
# ============================================================

def save_db(record_count, results, instrument_id, model=""):
    """将采集数据写入 SQLite 数据库（power_data 17参数表）"""
    import sqlite3
    import json

    # 确保数据库目录存在
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    # 构建 power_values 字典
    power_values = {}
    for name, val, unit, ch in results:
        key = f"ch{ch}_{name.lower()}"
        power_values[key] = val if val == val else None  # NaN → None

    now = datetime.now()
    timestamp = now.timestamp()
    datetime_str = now.isoformat()
    metadata = {
        "power_values": power_values,
        "source": "power_file_monitor",
        "model": model,
    }

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    cursor = conn.cursor()

    try:
        # 确保表存在
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS power_data (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id       INTEGER NOT NULL,
                ch1_urms        REAL, ch1_irms        REAL, ch1_p           REAL,
                ch1_lamb        REAL, ch1_ithd        REAL,
                ch1_ik3         REAL, ch1_ik5         REAL, ch1_ik7         REAL, ch1_ik9         REAL,
                ch2_urms        REAL, ch2_irms        REAL, ch2_p           REAL,
                ch2_ippeak      REAL, ch2_impeak      REAL,
                ch2_f1          REAL, ch2_idc         REAL, ch2_f2          REAL,
                ch3_urms        REAL, ch3_irms        REAL, ch3_p           REAL,
                ch3_lamb        REAL, ch3_ithd        REAL,
                ch3_ik3         REAL, ch3_ik5         REAL, ch3_ik7         REAL, ch3_ik9         REAL,
                ch4_urms        REAL, ch4_irms        REAL, ch4_p           REAL,
                ch4_ippeak      REAL, ch4_impeak      REAL,
                ch4_f3          REAL, ch4_idc         REAL, ch4_f4          REAL,
                FOREIGN KEY (record_id) REFERENCES acquisition_records(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS acquisition_records (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                sequence_number INTEGER NOT NULL,
                sync_timestamp  REAL NOT NULL,
                sync_datetime   TEXT NOT NULL,
                instrument_id   TEXT NOT NULL,
                instrument_type TEXT NOT NULL,
                filename        TEXT,
                acquisition_time_ms REAL,
                metadata        TEXT,
                created_at      TEXT NOT NULL
            )
        """)
        conn.commit()

        # 插入主表
        metadata_str = json.dumps(metadata, ensure_ascii=False)
        cursor.execute("""
            INSERT INTO acquisition_records
                (sequence_number, sync_timestamp, sync_datetime,
                 instrument_id, instrument_type, filename,
                 acquisition_time_ms, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            record_count,
            timestamp,
            datetime_str,
            instrument_id,
            "power_analyzer",
            "",
            0.0,
            metadata_str,
            now.isoformat(),
        ))
        record_id = cursor.lastrowid

        # 插入 power_data 表
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
        print(f"     💾 已写入数据库 record_id={record_id}")
    except Exception as e:
        print(f"     ❌ 数据库写入失败: {e}")
        conn.rollback()
    finally:
        conn.close()


# ============================================================
# 主程序
# ============================================================

def main():
    print("=" * 60)
    print("  功率分析仪 — IMAGE SAVE 触发采集")
    print(f"  IP: {IP}")
    print(f"  CSV: {CSV_PATH}")
    print("=" * 60)

    # 显示采集参数
    for ch_num in [1, 2, 3, 4]:
        ch_items = [it for it in ACQUIRE_ITEMS if it[4] == ch_num]
        if ch_items:
            print(f"\n📋 Channel {ch_num} ({len(ch_items)} 个参数):")
            for idx, param, name, unit, _ in ch_items:
                col = f"{name}({unit})" if unit else name
                print(f"   ITEM{idx:2d}: {param:<12} → {col}")

    # 自动检测设备类型
    print(f"\n📡 连接功率分析仪...")
    dev_type, model = detect_device_type(IP)
    if not dev_type:
        print(f"❌ 无法连接 {IP}（VXI-11 和 TCP 10001 均失败）")
        return

    # 创建对应适配器
    if dev_type == "vxi11":
        adapter = WT1800Adapter(IP, TIMEOUT)
        print(f"  🔍 检测到 WT1800 系列 (VXI-11)")
    else:
        adapter = WT3000Adapter(IP)
        print(f"  🔍 检测到 WT3000 系列 (TCP TMCTL)")

    try:
        adapter.connect()
    except Exception as e:
        print(f"❌ 连接失败: {e}")
        return

    # USB 检测
    print(f"\n⚙️  检测 USB 驱动器...")
    usb_drive = adapter.switch_to_usb()
    if usb_drive:
        print(f"  ✅ 已切换到: {usb_drive}")
    else:
        print(f"  ⚠️ 未检测到 USB（可随时插入，自动检测）")

    free = adapter.read_file_free()
    if free is not None:
        print(f"  💾 剩余空间: {free:,} bytes ({free/1024/1024:.1f} MB)")
    else:
        print(f"  ❌ 无法读取空间")
        adapter.disconnect()
        return

    # 配置数据输出项
    adapter.configure_items(ACQUIRE_ITEMS)

    # 监控循环
    print(f"\n{'=' * 60}")
    print(f"  🔄 监控中... 按 IMAGE SAVE 触发采集")
    print(f"  Ctrl+C 退出")
    print(f"{'=' * 60}\n")

    record_count = 0
    last_free = free
    cooldown = 0
    usb_check_counter = 0
    usb_ready = usb_drive is not None

    try:
        while True:
            time.sleep(POLL_INTERVAL)
            if cooldown > 0:
                cooldown -= POLL_INTERVAL
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
                            print(f"  🔌 检测到 U 盘! ({free/1024/1024:.1f} MB)")
                        continue

            free = adapter.read_file_free()
            if free is None:
                continue

            diff = last_free - free
            if diff >= MIN_SPACE_CHANGE:
                record_count += 1
                now_str = datetime.now().strftime("%H:%M:%S")
                print(f"  🔔 [{now_str}] 检测到 IMAGE SAVE! 空间变化: {diff:,} bytes")

                t0 = time.time()
                results = adapter.acquire(ACQUIRE_ITEMS)

                if results:
                    print(f"  ✅ #{record_count} [{now_str}]")
                    for ch_num in [1, 2, 3, 4]:
                        ch_results = [(n, v, u) for n, v, u, c in results if c == ch_num]
                        if not ch_results:
                            continue
                        print(f"     Ch{ch_num}:", end="")
                        for name, val, unit in ch_results:
                            if val == val:
                                u = f"{unit}" if unit else ""
                                print(f"  {name}={val:g}{u}", end="")
                            else:
                                print(f"  {name}=N/A", end="")
                        print()

                    save_csv(record_count, results)
                    save_db(record_count, results, IP, adapter.model)
                    print(f"     耗时 {time.time()-t0:.2f}s\n")
                else:
                    print(f"  ❌ #{record_count} 采集失败\n")

                last_free = free
                cooldown = 3.0
            else:
                last_free = free

    except KeyboardInterrupt:
        pass

    print(f"\n🔄 恢复原始配置...")
    adapter._restore_original_items()
    print(f"  ✅ 已恢复")
    adapter.disconnect()
    print(f"\n停止，共 {record_count} 条记录")


if __name__ == "__main__":
    main()
