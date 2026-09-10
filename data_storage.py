# ============================================================
# data_storage.py — 数据库存储模块
# ============================================================
# 职责：管理 SQLite 数据库的创建、数据写入、查询和备份。
#       只存储数值数据（示波器参数、功率分析仪测量值），
#       不存储波形原始数据。
#
# 数据库表结构：
#   - acquisition_records  : 采集记录主表（每次采集一条）
#   - oscilloscope_channels : 示波器通道数值参数
#   - power_measurements    : 功率分析仪测量值（通用，旧版兼容）
#   - power_data            : 功率分析仪17参数专用表（Ch1+Ch2）
#
# 依赖：sqlite3 (Python 标准库)
# ============================================================

from __future__ import annotations

import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from instrument_interface import InstrumentData, InstrumentType


class DataStorage:
    """
    数据库存储管理器。

    使用方式：
        storage = DataStorage("data/acquisition.db")
        storage.initialize()
        storage.save_acquisition(result)
        records = storage.query_records(limit=10)
    """

    def __init__(self, db_path: str = "data/acquisition.db"):
        """
        初始化存储管理器。

        Args:
            db_path: 数据库文件路径
        """
        self._db_path = db_path
        self._connection: Optional[sqlite3.Connection] = None
        self._logger = None

    def _get_logger(self):
        if self._logger is None:
            try:
                from logger import get_logger
                self._logger = get_logger(__name__)
            except ImportError:
                import logging
                self._logger = logging.getLogger(__name__)
        return self._logger

    # ============================================================
    # 数据库初始化
    # ============================================================

    def initialize(self) -> None:
        """
        初始化数据库，创建所有表。

        如果数据库文件不存在会自动创建。
        如果表已存在则跳过。
        """
        logger = self._get_logger()

        # 确保目录存在
        db_dir = Path(self._db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)

        conn = self._get_connection()
        cursor = conn.cursor()

        # --- 采集记录主表 ---
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

        # --- 示波器通道数值表 ---
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS oscilloscope_channels (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id       INTEGER NOT NULL,
                channel_id      INTEGER NOT NULL,
                channel_name    TEXT,
                sample_rate     REAL,
                voltage_range   REAL,
                max_voltage     REAL,
                min_voltage     REAL,
                avg_voltage     REAL,
                rms_voltage     REAL,
                peak_to_peak    REAL,
                data_points     INTEGER,
                FOREIGN KEY (record_id) REFERENCES acquisition_records(id)
            )
        """)

        # --- 功率分析仪测量值表（旧版通用，保留兼容） ---
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS power_measurements (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                record_id       INTEGER NOT NULL,
                element_id      INTEGER NOT NULL,
                voltage         REAL,
                current         REAL,
                active_power    REAL,
                reactive_power  REAL,
                apparent_power  REAL,
                power_factor    REAL,
                energy          REAL,
                frequency       REAL,
                extra_data      TEXT,
                FOREIGN KEY (record_id) REFERENCES acquisition_records(id)
            )
        """)

        # --- 功率分析仪17参数专用表 ---
        # Ch1: URMS, IRMS, P, LAMB, ITHD, IK3, IK5, IK7, IK9
        # Ch2: URMS, IRMS, P, IPPeak, IMPeak, F1, IDC, F2
        # Ch3 = Ch1 参数, Ch4 = Ch2 参数（Ch4显示名: F3, F4）
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

        # --- 创建索引加速查询 ---
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_records_timestamp
            ON acquisition_records(sync_timestamp)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_records_instrument
            ON acquisition_records(instrument_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_records_sequence
            ON acquisition_records(sequence_number)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_power_record
            ON power_measurements(record_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_power_data_record
            ON power_data(record_id)
        """)

        conn.commit()
        logger.info(f"数据库初始化完成: {self._db_path}")

    # ============================================================
    # 数据写入
    # ============================================================

    def save_acquisition(
        self,
        instrument_id: str,
        data: InstrumentData,
        sequence_number: int = 0,
        acquisition_time_ms: float = 0.0,
    ) -> int:
        """
        保存一次采集数据到数据库。

        Args:
            instrument_id: 仪器标识
            data: InstrumentData 采集数据
            sequence_number: 采集序号
            acquisition_time_ms: 采集耗时(ms)

        Returns:
            插入的记录ID
        """
        logger = self._get_logger()
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 1. 插入采集记录主表
            import json
            metadata_str = json.dumps(data.metadata, ensure_ascii=False) if data.metadata else "{}"
            # 从 metadata 中提取 filename
            filename = (data.metadata or {}).get("filename", "")

            cursor.execute("""
                INSERT INTO acquisition_records
                    (sequence_number, sync_timestamp, sync_datetime,
                     instrument_id, instrument_type, filename,
                     acquisition_time_ms, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sequence_number,
                data.timestamp,
                data.datetime_str,
                instrument_id,
                data.instrument_type.value,
                filename,
                acquisition_time_ms,
                metadata_str,
                datetime.now().isoformat(),
            ))

            record_id = cursor.lastrowid

            # 2. 根据仪器类型插入子表
            if data.instrument_type == InstrumentType.OSCILLOSCOPE:
                self._save_oscilloscope_data(cursor, record_id, data)
            elif data.instrument_type == InstrumentType.POWER_ANALYZER:
                self._save_power_data(cursor, record_id, data)
                self._save_power_data_v2(cursor, record_id, data)

            conn.commit()
            conn.close()
            logger.debug(
                f"[{instrument_id}] 数据已保存, record_id={record_id}"
            )
            return record_id

        except Exception as e:
            conn.rollback()
            conn.close()
            logger.error(f"[{instrument_id}] 数据保存失败: {e}")
            raise

    def save_acquisition_result(self, result: object) -> List[int]:
        """
        保存 AcquisitionResult 中所有仪器的数据。

        Args:
            result: AcquisitionResult 实例

        Returns:
            各仪器插入的记录ID列表
        """
        record_ids = []
        for inst_id, data in result.results.items():
            rid = self.save_acquisition(
                instrument_id=inst_id,
                data=data,
                sequence_number=result.sequence_number,
                acquisition_time_ms=result.acquisition_time_ms,
            )
            record_ids.append(rid)
        return record_ids

    def _save_oscilloscope_data(
        self, cursor, record_id: int, data: InstrumentData
    ) -> None:
        """保存示波器通道数值数据（跳过全零通道）"""
        # 优先从 metadata 获取真实测量值
        metadata = data.metadata or {}
        measurements = metadata.get("measurements", {})

        # 如果 waveform_channels 为空但有 metadata 测量值，从 metadata 构建
        if not data.waveform_channels and measurements:
            # 提取所有通道编号
            channels = set()
            for key in measurements:
                if key.startswith("CH") and "_" in key:
                    try:
                        ch_num = int(key.split("_")[0].replace("CH", ""))
                        channels.add(ch_num)
                    except:
                        pass

            for ch_id in sorted(channels):
                ch_prefix = f"CH{ch_id}_"
                max_v = measurements.get(f"{ch_prefix}MAX")
                min_v = measurements.get(f"{ch_prefix}MIN")
                rms_v = measurements.get(f"{ch_prefix}RMS")
                ptp_v = measurements.get(f"{ch_prefix}PP")
                avg_v = measurements.get(f"{ch_prefix}AVER")

                # 跳过全零/全空通道
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
                """, (
                    record_id,
                    ch_id,
                    f"CH{ch_id}",
                    0.0, 0.0,
                    max_v if max_v is not None else 0.0,
                    min_v if min_v is not None else 0.0,
                    avg_v if avg_v is not None else 0.0,
                    rms_v if rms_v is not None else 0.0,
                    ptp_v if ptp_v is not None else 0.0,
                    0,
                ))
            return

        for ch in data.waveform_channels:
            ch_id = ch.channel_id
            ch_prefix = f"CH{ch_id}_"

            # 从 metadata 取真实值，没有则从 voltage_data 计算
            max_v = measurements.get(f"{ch_prefix}MAX")
            min_v = measurements.get(f"{ch_prefix}MIN")
            rms_v = measurements.get(f"{ch_prefix}RMS")
            ptp_v = measurements.get(f"{ch_prefix}PP")
            avg_v = measurements.get(f"{ch_prefix}AVG")

            if max_v is None:
                max_v = max(ch.voltage_data) if ch.voltage_data else 0.0
            if min_v is None:
                min_v = min(ch.voltage_data) if ch.voltage_data else 0.0
            if rms_v is None:
                rms_v = (
                    (sum(v ** 2 for v in ch.voltage_data) / len(ch.voltage_data)) ** 0.5
                    if ch.voltage_data else 0.0
                )
            if ptp_v is None:
                ptp_v = max_v - min_v
            if avg_v is None:
                avg_v = (
                    sum(ch.voltage_data) / len(ch.voltage_data)
                    if ch.voltage_data else 0.0
                )

            # 跳过全零通道（无信号）
            if max_v == 0.0 and min_v == 0.0 and rms_v == 0.0:
                continue

            cursor.execute("""
                INSERT INTO oscilloscope_channels
                    (record_id, channel_id, channel_name,
                     sample_rate, voltage_range,
                     max_voltage, min_voltage, avg_voltage,
                     rms_voltage, peak_to_peak, data_points)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id,
                ch.channel_id,
                ch.channel_name,
                ch.sample_rate,
                ch.voltage_range,
                round(max_v, 6),
                round(min_v, 6),
                round(avg_v, 6),
                round(rms_v, 6),
                round(ptp_v, 6),
                len(ch.voltage_data),
            ))

    def _save_power_data(
        self, cursor, record_id: int, data: InstrumentData
    ) -> None:
        """保存功率分析仪测量数据（旧版通用表）"""
        import json
        for m in data.power_measurements:
            extra_str = json.dumps(m.extra, ensure_ascii=False) if m.extra else "{}"

            cursor.execute("""
                INSERT INTO power_measurements
                    (record_id, element_id,
                     voltage, current,
                     active_power, reactive_power, apparent_power,
                     power_factor, energy, frequency,
                     extra_data)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record_id,
                m.element_id,
                round(m.voltage, 6),
                round(m.current, 6),
                round(m.active_power, 6),
                round(m.reactive_power, 6),
                round(m.apparent_power, 6),
                round(m.power_factor, 6),
                round(m.energy, 6),
                round(m.frequency, 6),
                extra_str,
            ))

    def _save_power_data_v2(
        self, cursor, record_id: int, data: InstrumentData
    ) -> None:
        """
        保存功率分析仪17参数到专用表 power_data。

        从 metadata["power_values"] 读取17个参数值：
        {
            "ch1_urms": 230.5, "ch1_irms": 0.5, "ch1_p": 115.0, ...
            "ch2_urms": 230.5, "ch2_irms": 0.5, "ch2_p": 115.0, ...
        }
        如果 metadata 中没有 power_values，则跳过。
        """
        metadata = data.metadata or {}
        pv = metadata.get("power_values")
        if not pv:
            return

        # 34个参数列名
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
            v = pv.get(col)
            if v is not None and v == v:  # 非 NaN
                values.append(round(v, 6))
            else:
                values.append(None)

        placeholders = ",".join(["?"] * (1 + len(columns)))
        col_names = "record_id," + ",".join(columns)
        cursor.execute(
            f"INSERT INTO power_data ({col_names}) VALUES ({placeholders})",
            [record_id] + values,
        )

    # ============================================================
    # 数据查询
    # ============================================================

    def query_records(
        self,
        instrument_id: Optional[str] = None,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        查询采集记录。

        Args:
            instrument_id: 按仪器ID筛选（None=全部）
            start_time: 起始时间戳
            end_time: 结束时间戳
            limit: 返回条数
            offset: 偏移量

        Returns:
            记录字典列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        sql = "SELECT * FROM acquisition_records WHERE 1=1"
        params: list = []

        if instrument_id:
            sql += " AND instrument_id = ?"
            params.append(instrument_id)
        if start_time:
            sql += " AND sync_timestamp >= ?"
            params.append(start_time)
        if end_time:
            sql += " AND sync_timestamp <= ?"
            params.append(end_time)

        sql += " ORDER BY sync_timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor.execute(sql, params)
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return rows

    def query_power_data(
        self,
        record_id: Optional[int] = None,
        instrument_id: Optional[str] = None,
        element_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        查询功率测量数据。

        Args:
            record_id: 按记录ID筛选
            instrument_id: 按仪器ID筛选
            element_id: 按测量单元筛选
            limit: 返回条数

        Returns:
            功率数据字典列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        sql = """
            SELECT pm.*, ar.instrument_id, ar.sync_datetime
            FROM power_measurements pm
            JOIN acquisition_records ar ON pm.record_id = ar.id
            WHERE 1=1
        """
        params: list = []

        if record_id:
            sql += " AND pm.record_id = ?"
            params.append(record_id)
        if instrument_id:
            sql += " AND ar.instrument_id = ?"
            params.append(instrument_id)
        if element_id:
            sql += " AND pm.element_id = ?"
            params.append(element_id)

        sql += " ORDER BY ar.sync_timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return rows

    def query_power_data_v2(
        self,
        record_id: Optional[int] = None,
        instrument_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        查询功率分析仪17参数专用表。

        Args:
            record_id: 按记录ID筛选
            instrument_id: 按仪器ID筛选
            limit: 返回条数

        Returns:
            功率数据字典列表
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        sql = """
            SELECT pd.*, ar.instrument_id, ar.sync_datetime
            FROM power_data pd
            JOIN acquisition_records ar ON pd.record_id = ar.id
            WHERE 1=1
        """
        params: list = []

        if record_id:
            sql += " AND pd.record_id = ?"
            params.append(record_id)
        if instrument_id:
            sql += " AND ar.instrument_id = ?"
            params.append(instrument_id)

        sql += " ORDER BY ar.sync_timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return rows

    def query_oscilloscope_data(
        self,
        record_id: Optional[int] = None,
        instrument_id: Optional[str] = None,
        channel_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """查询示波器通道数值数据"""
        conn = self._get_connection()
        cursor = conn.cursor()

        sql = """
            SELECT oc.*, ar.instrument_id, ar.sync_datetime
            FROM oscilloscope_channels oc
            JOIN acquisition_records ar ON oc.record_id = ar.id
            WHERE 1=1
        """
        params: list = []

        if record_id:
            sql += " AND oc.record_id = ?"
            params.append(record_id)
        if instrument_id:
            sql += " AND ar.instrument_id = ?"
            params.append(instrument_id)
        if channel_id:
            sql += " AND oc.channel_id = ?"
            params.append(channel_id)

        sql += " ORDER BY ar.sync_timestamp DESC LIMIT ?"
        params.append(limit)

        cursor.execute(sql, params)
        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        conn.close()
        return rows

    def get_record_count(self, instrument_id: Optional[str] = None) -> int:
        """获取记录总数"""
        conn = self._get_connection()
        cursor = conn.cursor()

        if instrument_id:
            cursor.execute(
                "SELECT COUNT(*) FROM acquisition_records WHERE instrument_id = ?",
                (instrument_id,)
            )
        else:
            cursor.execute("SELECT COUNT(*) FROM acquisition_records")

        count = cursor.fetchone()[0]
        conn.close()
        return count

    def get_latest_records(self, n: int = 10) -> List[Dict[str, Any]]:
        """获取最近N条采集记录"""
        return self.query_records(limit=n)

    # ============================================================
    # 数据备份
    # ============================================================

    def backup(self, backup_path: Optional[str] = None) -> str:
        """
        备份数据库文件。

        Args:
            backup_path: 备份路径（None则自动生成）

        Returns:
            备份文件路径
        """
        logger = self._get_logger()

        if backup_path is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{self._db_path}.backup_{timestamp}"

        # 确保备份目录存在
        Path(backup_path).parent.mkdir(parents=True, exist_ok=True)

        # SQLite 备份API（安全备份，无需停机）
        conn = self._get_connection()
        backup_conn = sqlite3.connect(backup_path)
        conn.backup(backup_conn)
        backup_conn.close()
        conn.close()

        logger.info(f"数据库已备份: {backup_path}")
        return backup_path

    # ============================================================
    # 数据清理
    # ============================================================

    def delete_records_before(self, timestamp: float) -> int:
        """删除指定时间之前的所有记录"""
        conn = self._get_connection()
        cursor = conn.cursor()

        # 先查要删除的record_id
        cursor.execute(
            "SELECT id FROM acquisition_records WHERE sync_timestamp < ?",
            (timestamp,)
        )
        record_ids = [row[0] for row in cursor.fetchall()]

        if not record_ids:
            return 0

        # 删除子表数据
        placeholders = ",".join("?" * len(record_ids))
        cursor.execute(
            f"DELETE FROM oscilloscope_channels WHERE record_id IN ({placeholders})",
            record_ids,
        )
        cursor.execute(
            f"DELETE FROM power_measurements WHERE record_id IN ({placeholders})",
            record_ids,
        )
        cursor.execute(
            f"DELETE FROM power_data WHERE record_id IN ({placeholders})",
            record_ids,
        )
        # 删除主表
        cursor.execute(
            f"DELETE FROM acquisition_records WHERE id IN ({placeholders})",
            record_ids,
        )

        conn.commit()
        conn.close()
        self._get_logger().info(
            f"已删除 {len(record_ids)} 条记录（时间戳 < {timestamp}）"
        )
        return len(record_ids)

    def vacuum(self) -> None:
        """压缩数据库（回收已删除数据的空间）"""
        conn = self._get_connection()
        conn.execute("VACUUM")
        conn.close()
        self._get_logger().info("数据库已压缩")

    # ============================================================
    # 连接管理
    # ============================================================

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接（每次创建新连接，支持多线程）"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        # 启用WAL模式，提升并发读写性能
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def close(self) -> None:
        """关闭数据库连接"""
        if self._connection:
            self._connection.close()
            self._connection = None
            self._get_logger().info("数据库连接已关闭")

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __repr__(self):
        return f"<DataStorage path={self._db_path}>"
