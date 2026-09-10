#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# db_viewer.py — 数据库可视化 Web 应用（Streamlit）
# ============================================================
# 启动方式：
#   cd instrument_acquisition
#   streamlit run db_viewer.py
#
# 功能：
#   - 查看所有采集记录（按时间倒序）
#   - 按仪器 ID / 时间范围筛选
#   - 选中删除示波器数据
#   - 查看示波器通道 / 功率分析仪数据
#   - 导出筛选结果为 CSV
#   - 自动生成报告（上传 BOM + 选择数据 → 生成 .xlsx）
# ============================================================

import csv
import io
import json
import os
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# 数据库路径
DB_PATH = Path(__file__).parent / "data" / "acquisition.db"
# 报告生成模块路径
REPORT_GENERATOR_PATH = Path(__file__).parent / "auto_report_generator.py"


# ============================================================
# 勾选状态持久化工具函数（解决 data_editor 勾选点了又跳回的问题）
# ============================================================
def build_selection_column(df_display, id_col, session_sel_key):
    """
    基于 session_state 中持久化的「上一轮最终选中的 id 集合」，
    给 df_display 增加「选择」布尔列（TRUE=选中）。

    ⚠️ 本函数刻意不读 st.session_state[widget_key] 里的 edited_rows 增量！
    原因：edited_rows 是 data_editor 前端 widget 的"补丁缓存"，Streamlit
    在 st.data_editor() 渲染时会自动把它应用到传入的 DataFrame 上，返回的
    edited_df 里的「选择」列已经是「用户最终勾选的真值」。我们只需要：
      1. 初始渲染：用上一轮保存的最终 selected_ids 重建选择列；
      2. 每轮结束：从 edited_df 的「选择」列直接提取选中 ids，再存回
         session_state。
    这样完全绕开 edited_rows 合并/双重应用等各种坑。

    参数:
        df_display     : 准备传给 st.data_editor 的 DataFrame（未含「选择」列）
        id_col         : 唯一标识列名（"id" 或 "record_id"）
        session_sel_key: session_state 中保存选中 id 列表的 key

    返回:
        df_with_sel  : 已插入「选择」列（index 0）的 DataFrame
    """
    df = df_display.copy()
    saved_ids = set(st.session_state.get(session_sel_key, []) or [])
    df.insert(0, "选择", df[id_col].isin(saved_ids))
    return df


# ============================================================
# 带世代计数器的 widget key 生成器（仅在“全选/取消全选/选择范围/删除”时重置）
# ============================================================
def fresh_editor_key(base: str) -> str:
    """
    返回当前世代的 widget key（如 "rec_selector_gen_0"）。

    ⚠️ 关键设计（本次修正的核心）：
        - 用户点击复选框（勾选/取消）时，key 必须【保持稳定】，让 Streamlit
          的 data_editor 自己持久化 edited_rows 增量——这样快速连续勾选几行，
          不会因为组件被重建而丢失某一次点击（这正是“点几个后其中一个突然
          变回不选”的根因）。
        - 只有当“全选 / 取消全选 / 选择范围 / 删除”这类【整体改写】选择状态的
          按钮被点击时，才调用 bump_editor_key() 让世代号 +1，强制创建新
          widget、丢弃旧的 edited_rows 缓存，从而把 session_state 里的新选择
          干净地当作初始值渲染，避免“按钮改了 session_state 但旧 edited_rows
          又把它打回原样”的冲突。

    因此：勾选交互路径里【不再】调用 bump_editor_key()（历史上曾在此 bump，
    导致 React/glide 组件频繁重建、快速点击丢勾）。bump 仅存在于按钮/删除逻辑。

    使用方式：
        st.data_editor(..., key=fresh_editor_key("rec_selector"))
        按钮整体修改选择后：
            bump_editor_key("rec_selector")
    """
    counter_key = f"__gen_{base}"
    generation = st.session_state.get(counter_key, 0)
    return f"{base}_gen_{generation}"


def bump_editor_key(base: str) -> None:
    """把 base 对应的世代号 +1，下次 fresh_editor_key() 返回新 key。"""
    counter_key = f"__gen_{base}"
    st.session_state[counter_key] = int(st.session_state.get(counter_key, 0)) + 1


def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables():
    """确保数据库中存在所有需要的表和列（自动补建）"""
    if not DB_PATH.exists():
        return
    conn = get_connection()
    cursor = conn.cursor()

    try:
        # 1. 先创建 acquisition_records（其他表依赖它）
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='acquisition_records'")
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE acquisition_records (
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
        else:
            cursor.execute("PRAGMA table_info(acquisition_records)")
            ar_cols = {row[1] for row in cursor.fetchall()}
            if "filename" not in ar_cols:
                cursor.execute("ALTER TABLE acquisition_records ADD COLUMN filename TEXT")
            if "metadata" not in ar_cols:
                cursor.execute("ALTER TABLE acquisition_records ADD COLUMN metadata TEXT")

        # 2. 确保 power_data 表及列存在
        all_power_cols = [
            ("ch1_urms", "REAL"), ("ch1_irms", "REAL"), ("ch1_p", "REAL"),
            ("ch1_lamb", "REAL"), ("ch1_ithd", "REAL"),
            ("ch1_ik3", "REAL"), ("ch1_ik5", "REAL"), ("ch1_ik7", "REAL"), ("ch1_ik9", "REAL"),
            ("ch2_urms", "REAL"), ("ch2_irms", "REAL"), ("ch2_p", "REAL"),
            ("ch2_ippeak", "REAL"), ("ch2_impeak", "REAL"),
            ("ch2_f1", "REAL"), ("ch2_idc", "REAL"), ("ch2_f2", "REAL"),
            ("ch3_urms", "REAL"), ("ch3_irms", "REAL"), ("ch3_p", "REAL"),
            ("ch3_lamb", "REAL"), ("ch3_ithd", "REAL"),
            ("ch3_ik3", "REAL"), ("ch3_ik5", "REAL"), ("ch3_ik7", "REAL"), ("ch3_ik9", "REAL"),
            ("ch4_urms", "REAL"), ("ch4_irms", "REAL"), ("ch4_p", "REAL"),
            ("ch4_ippeak", "REAL"), ("ch4_impeak", "REAL"),
            ("ch4_f3", "REAL"), ("ch4_idc", "REAL"), ("ch4_f4", "REAL"),
        ]
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='power_data'")
        if not cursor.fetchone():
            col_defs = ", ".join(f"{col} {typ}" for col, typ in all_power_cols)
            cursor.execute(f"""
                CREATE TABLE power_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id INTEGER NOT NULL,
                    {col_defs},
                    FOREIGN KEY (record_id) REFERENCES acquisition_records(id)
                )
            """)
            print("✅ 创建 power_data 表")
        else:
            # 检查是否需要从 IMN 迁移到 IDC
            cursor.execute("PRAGMA table_info(power_data)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if "ch2_imn" in existing_cols or "ch4_imn" in existing_cols:
                print("🔄 检测到旧数据（IMN），正在迁移到 IDC...")
                if "ch2_imn" in existing_cols and "ch2_idc" not in existing_cols:
                    cursor.execute("ALTER TABLE power_data ADD COLUMN ch2_idc REAL")
                    cursor.execute("UPDATE power_data SET ch2_idc = ch2_imn WHERE ch2_imn IS NOT NULL")
                if "ch4_imn" in existing_cols and "ch4_idc" not in existing_cols:
                    cursor.execute("ALTER TABLE power_data ADD COLUMN ch4_idc REAL")
                    cursor.execute("UPDATE power_data SET ch4_idc = ch4_imn WHERE ch4_imn IS NOT NULL")
                for col in ["ch2_imn", "ch4_imn"]:
                    try:
                        cursor.execute(f"ALTER TABLE power_data DROP COLUMN {col}")
                    except Exception:
                        pass
                print("✅ 数据迁移完成")
            # 检查是否需要从 ch4_f1/ch4_f2 迁移到 ch4_f3/ch4_f4
            cursor.execute("PRAGMA table_info(power_data)")
            existing_cols = {row[1] for row in cursor.fetchall()}
            if ("ch4_f1" in existing_cols or "ch4_f2" in existing_cols) and \
               ("ch4_f3" not in existing_cols or "ch4_f4" not in existing_cols):
                print("🔄 检测到旧数据（ch4_f1/f2），正在迁移到 ch4_f3/f4...")
                if "ch4_f1" in existing_cols and "ch4_f3" not in existing_cols:
                    cursor.execute("ALTER TABLE power_data ADD COLUMN ch4_f3 REAL")
                    cursor.execute("UPDATE power_data SET ch4_f3 = ch4_f1 WHERE ch4_f1 IS NOT NULL")
                if "ch4_f2" in existing_cols and "ch4_f4" not in existing_cols:
                    cursor.execute("ALTER TABLE power_data ADD COLUMN ch4_f4 REAL")
                    cursor.execute("UPDATE power_data SET ch4_f4 = ch4_f2 WHERE ch4_f2 IS NOT NULL")
                print("✅ 数据迁移完成")
            # 补加所有缺失的列（忽略已存在的列）
            for col_name, col_type in all_power_cols:
                try:
                    cursor.execute(f"ALTER TABLE power_data ADD COLUMN {col_name} {col_type}")
                except Exception:
                    pass  # 列已存在，跳过

        # 3. 创建 oscilloscope_channels 表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='oscilloscope_channels'")
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE oscilloscope_channels (
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

        # 4. 创建 power_measurements 表（兼容旧数据）
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='power_measurements'")
        if not cursor.fetchone():
            cursor.execute("""
                CREATE TABLE power_measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id INTEGER NOT NULL,
                    element_id INTEGER NOT NULL,
                    voltage REAL,
                    current REAL,
                    active_power REAL,
                    reactive_power REAL,
                    apparent_power REAL,
                    power_factor REAL,
                    energy REAL,
                    frequency REAL,
                    extra_data TEXT,
                    FOREIGN KEY (record_id) REFERENCES acquisition_records(id)
                )
            """)

        conn.commit()
    except Exception as e:
        print(f"⚠️ 数据库初始化失败: {e}")
        conn.rollback()
    finally:
        conn.close()


def load_records(instrument_id=None, start_time=None, end_time=None):
    conn = get_connection()
    sql = "SELECT * FROM acquisition_records WHERE 1=1"
    params = []
    if instrument_id:
        sql += " AND instrument_id = ?"
        params.append(instrument_id)
    if start_time:
        sql += " AND sync_timestamp >= ?"
        params.append(start_time)
    if end_time:
        sql += " AND sync_timestamp <= ?"
        params.append(end_time)
    sql += " ORDER BY sync_timestamp DESC"
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def load_oscilloscope_data(record_id=None, instrument_id=None):
    conn = get_connection()
    sql = """
        SELECT oc.*, ar.instrument_id, ar.sync_datetime, ar.filename, ar.metadata
        FROM oscilloscope_channels oc
        JOIN acquisition_records ar ON oc.record_id = ar.id
        WHERE 1=1
    """
    params = []
    if record_id:
        sql += " AND oc.record_id = ?"
        params.append(record_id)
    if instrument_id:
        sql += " AND ar.instrument_id = ?"
        params.append(instrument_id)
    sql += " ORDER BY ar.sync_timestamp DESC"
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def load_power_data(record_id=None, instrument_id=None):
    conn = get_connection()
    sql = """
        SELECT pm.*, ar.instrument_id, ar.sync_datetime, ar.filename
        FROM power_measurements pm
        JOIN acquisition_records ar ON pm.record_id = ar.id
        WHERE 1=1
    """
    params = []
    if record_id:
        sql += " AND pm.record_id = ?"
        params.append(record_id)
    if instrument_id:
        sql += " AND ar.instrument_id = ?"
        params.append(instrument_id)
    sql += " ORDER BY ar.sync_timestamp DESC"
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def load_power_data_v2(record_id=None, instrument_id=None):
    """查询功率分析仪17参数专用表 power_data"""
    conn = get_connection()
    sql = """
        SELECT pd.*, ar.instrument_id, ar.sync_datetime, ar.filename, ar.metadata
        FROM power_data pd
        JOIN acquisition_records ar ON pd.record_id = ar.id
        WHERE 1=1
    """
    params = []
    if record_id:
        sql += " AND pd.record_id = ?"
        params.append(record_id)
    if instrument_id:
        sql += " AND ar.instrument_id = ?"
        params.append(instrument_id)
    sql += " ORDER BY ar.sync_timestamp DESC"
    df = pd.read_sql_query(sql, conn, params=params)
    conn.close()
    return df


def get_instrument_ids():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT instrument_id FROM acquisition_records ORDER BY instrument_id")
    ids = [row[0] for row in cursor.fetchall()]
    conn.close()
    return ids


def get_record_count():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM acquisition_records")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def export_oscilloscope_csv(instrument_id=None, start_time=None, end_time=None):
    """导出示波器数据为报告生成器需要的 CSV 格式"""
    df_osc = load_oscilloscope_data(instrument_id=instrument_id)
    if df_osc.empty:
        return None

    rows = []
    for _, row in df_osc.iterrows():
        meta = row.get("metadata", "{}")
        if isinstance(meta, str):
            try: meta = json.loads(meta)
            except: meta = {}
        measurements = meta.get("measurements", {})

        rows.append({
            "filename": row.get("filename", ""),
            "MAX": measurements.get(f"CH{row['channel_id']}_MAX"),
            "MIN": measurements.get(f"CH{row['channel_id']}_MIN"),
            "RMS": measurements.get(f"CH{row['channel_id']}_RMS"),
            "PP": measurements.get(f"CH{row['channel_id']}_PP"),
            "AVER": measurements.get(f"CH{row['channel_id']}_AVER"),
        })

    # 过滤掉全空行
    rows = [r for r in rows if any(v is not None for v in r.values())]
    return rows


# ============================================================
# 页面配置
# ============================================================

st.set_page_config(
    page_title="仪器数据采集系统",
    page_icon="📊",
    layout="wide",
)

st.title("📊 仪器数据采集系统 — 数据库查看器")

if not DB_PATH.exists():
    st.error(f"数据库文件不存在: {DB_PATH}")
    st.info("请先运行数据采集程序生成数据库。")
    sys.exit(0)

# 自动补建缺失的表
ensure_tables()

# ============================================================
# 侧边栏
# ============================================================

with st.sidebar:
    st.header("🔍 筛选条件")

    instrument_ids = get_instrument_ids()
    if instrument_ids:
        selected_instrument = st.selectbox("仪器 ID", options=["全部"] + instrument_ids, index=0)
    else:
        selected_instrument = "全部"
        st.warning("暂无数据")

    st.subheader("时间范围")
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("起始日期", value=None)
    with col2:
        end_date = st.date_input("结束日期", value=None)

    start_ts = datetime.combine(start_date, datetime.min.time()).timestamp() if start_date else None
    end_ts = datetime.combine(end_date, datetime.max.time()).timestamp() if end_date else None

    st.divider()
    total_count = get_record_count()
    st.metric("总记录数", f"{total_count}")

    if st.button("🔄 刷新数据"):
        st.rerun()

    st.divider()
    st.header("🗑️ 数据管理")

    st.subheader("按时间清理")
    cleanup_date = st.date_input("删除此日期之前的数据", value=None, key="cleanup_date")
    if cleanup_date and st.button("⚠️ 执行清理", type="secondary", key="btn_cleanup"):
        cleanup_ts = datetime.combine(cleanup_date, datetime.max.time()).timestamp()
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM acquisition_records WHERE sync_timestamp < ?", (cleanup_ts,))
        del_count = cursor.fetchone()[0]
        if del_count == 0:
            st.info(f"{cleanup_date} 之前没有数据。")
        else:
            cursor.execute("SELECT id FROM acquisition_records WHERE sync_timestamp < ?", (cleanup_ts,))
            ids = [row[0] for row in cursor.fetchall()]
            ph = ",".join("?" * len(ids))
            cursor.execute(f"DELETE FROM oscilloscope_channels WHERE record_id IN ({ph})", ids)
            cursor.execute(f"DELETE FROM power_measurements WHERE record_id IN ({ph})", ids)
            cursor.execute(f"DELETE FROM power_data WHERE record_id IN ({ph})", ids)
            cursor.execute(f"DELETE FROM acquisition_records WHERE id IN ({ph})", ids)
            conn.commit()
            conn.close()
            st.success(f"已删除 {del_count} 条记录")
            st.rerun()

    st.subheader("按仪器清理")
    if instrument_ids:
        del_instrument = st.selectbox("选择仪器", options=instrument_ids, key="del_inst")
        if st.button(f"⚠️ 删除 {del_instrument} 的所有数据", type="secondary", key="btn_del_inst"):
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM acquisition_records WHERE instrument_id = ?", (del_instrument,))
            ids = [row[0] for row in cursor.fetchall()]
            if not ids:
                st.info(f"{del_instrument} 没有数据。")
            else:
                ph = ",".join("?" * len(ids))
                cursor.execute(f"DELETE FROM oscilloscope_channels WHERE record_id IN ({ph})", ids)
                cursor.execute(f"DELETE FROM power_measurements WHERE record_id IN ({ph})", ids)
                cursor.execute(f"DELETE FROM power_data WHERE record_id IN ({ph})", ids)
                cursor.execute(f"DELETE FROM acquisition_records WHERE id IN ({ph})", ids)
                conn.commit()
                conn.close()
                st.success(f"已删除 {del_instrument} 的 {len(ids)} 条记录")
                st.rerun()

    # ⚠️ 「清空数据库」已禁用：风险过高，避免误点导致 1357 条历史数据（CSV 无法再恢复的 filename/测量值）
    # 不可逆丢失。如确需清空，请在命令行直接操作 SQLite。

# ============================================================
# 主区域 — 4 个标签页
# ============================================================

inst_filter = selected_instrument if selected_instrument != "全部" else None
df_records = load_records(inst_filter, start_ts, end_ts)

tab_data, tab_osc, tab_pwr, tab_report, tab_devices, tab_monitor = st.tabs([
    "📋 采集记录", "📈 示波器数据", "⚡ 功率分析仪数据", "📝 报告生成", "⚙️ 设备管理", "🔴 监控控制"
])

# ── 标签1：采集记录 ──
with tab_data:
    st.subheader(f"📋 采集记录（{len(df_records)} 条）")
    if df_records.empty:
        st.info("没有符合条件的记录。")
    else:
        display_df = df_records.copy()
        display_df.insert(0, "行号", range(1, len(display_df) + 1))
        if "sync_datetime" in display_df.columns:
            display_df["sync_datetime"] = pd.to_datetime(display_df["sync_datetime"], format="ISO8601", errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
        if "created_at" in display_df.columns:
            display_df["created_at"] = pd.to_datetime(display_df["created_at"], format="ISO8601", errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")

        show_cols = [c for c in ["行号", "filename", "instrument_id", "instrument_type",
                                 "sync_datetime", "acquisition_time_ms"] if c in display_df.columns]

        # ── 操作按钮栏 ──
        rec_btn1, rec_btn2, rec_btn3, rec_btn4 = st.columns([1, 1, 2, 2])
        with rec_btn1:
            if st.button("☑️ 全选", key="btn_rec_select_all"):
                st.session_state["rec_selected_ids"] = display_df["id"].tolist()
                bump_editor_key("rec_selector")
                st.rerun()
        with rec_btn2:
            if st.button("☐️ 取消全选", key="btn_rec_deselect_all"):
                st.session_state["rec_selected_ids"] = []
                bump_editor_key("rec_selector")
                st.rerun()
        with rec_btn3:
            rec_range1, rec_range2 = st.columns(2)
            with rec_range1:
                rec_start = st.number_input("起始行", min_value=1, max_value=len(display_df), value=1, key="rec_range_start")
            with rec_range2:
                rec_end = st.number_input("结束行", min_value=1, max_value=len(display_df), value=len(display_df), key="rec_range_end")
        with rec_btn4:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("📌 选择范围", key="btn_rec_select_range"):
                s = min(rec_start, rec_end) - 1
                e = max(rec_start, rec_end)
                st.session_state["rec_selected_ids"] = display_df["id"].iloc[s:e].tolist()
                bump_editor_key("rec_selector")
                st.rerun()

        # ── 可编辑数据表（带选择列，id 隐藏但用于删除） ──
        # 使用 build_selection_column：用上一轮存的 selected_ids 重建勾选列初始值。
        # key 稳定（fresh_editor_key 只在按钮/删除时 bump），勾选交互不重建组件。
        display_with_sel = build_selection_column(
            display_df[["id"] + show_cols],
            id_col="id",
            session_sel_key="rec_selected_ids",
        )
        _rec_key = fresh_editor_key("rec_selector")
        edited_rec = st.data_editor(
            display_with_sel,
            width="stretch", hide_index=True, height=400,
            disabled=show_cols + ["id"], key=_rec_key,
            column_config={"选择": st.column_config.CheckboxColumn()},
        )

        # ✅ 直接从 data_editor 返回的最终 df 里提取选中行。
        # 关键修正：这里【不要】再 bump 世代号。稳定 key 让 data_editor 自己
        # 持久化勾选状态（edited_rows），快速连续勾选不会因组件重建而丢点击；
        # 只有“全选/取消全选/选择范围/删除”按钮整体改写选择时才 bump 重置组件。
        new_rec_ids = edited_rec.loc[edited_rec["选择"].astype(bool), "id"].tolist()
        new_rec_ids = list(dict.fromkeys(new_rec_ids))  # 去重且保序
        st.session_state["rec_selected_ids"] = new_rec_ids

        # ── 选中操作 ──
        selected_rec_ids = new_rec_ids
        if selected_rec_ids:
            rec_act1, rec_act2 = st.columns([1, 1])
            with rec_act1:
                st.warning(f"已选择 {len(selected_rec_ids)} 条记录")
            with rec_act2:
                if st.button("🗑️ 删除选中记录", type="primary", key="btn_delete_rec"):
                    conn = get_connection()
                    cursor = conn.cursor()
                    ph = ",".join("?" * len(selected_rec_ids))
                    cursor.execute(f"DELETE FROM oscilloscope_channels WHERE record_id IN ({ph})", selected_rec_ids)
                    cursor.execute(f"DELETE FROM power_data WHERE record_id IN ({ph})", selected_rec_ids)
                    cursor.execute(f"DELETE FROM power_measurements WHERE record_id IN ({ph})", selected_rec_ids)
                    cursor.execute(f"DELETE FROM acquisition_records WHERE id IN ({ph})", selected_rec_ids)
                    conn.commit()
                    conn.close()
                    # 删除后清 session_state + 强制刷新 widget key
                    remaining = [i for i in (st.session_state.get("rec_selected_ids", []) or [])
                                 if i not in set(selected_rec_ids)]
                    st.session_state["rec_selected_ids"] = remaining
                    bump_editor_key("rec_selector")
                    st.success(f"已删除 {len(selected_rec_ids)} 条记录")
                    st.rerun()

        # CSV 导出 — 使用 df_records 原始数值（保留纯数值列，避免显示格式化干扰分析）
        if selected_rec_ids:
            csv_df = df_records[df_records["id"].isin(selected_rec_ids)].copy()
        else:
            csv_df = df_records.copy()
        # 列顺序: id, instrument_id, filename, sync_datetime, instrument_type, sequence_number, acquisition_time_ms, created_at, metadata
        csv_order = [c for c in ["id", "instrument_id", "filename", "sync_datetime",
                                 "instrument_type", "sequence_number",
                                 "acquisition_time_ms", "created_at", "metadata"]
                     if c in csv_df.columns]
        csv_df = csv_df[csv_order]
        if "sync_datetime" in csv_df.columns:
            csv_df["sync_datetime"] = pd.to_datetime(csv_df["sync_datetime"], format="ISO8601", errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
        if "created_at" in csv_df.columns:
            csv_df["created_at"] = pd.to_datetime(csv_df["created_at"], format="ISO8601", errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
        csv_data = csv_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(label="📥 导出 CSV", data=csv_data,
                           file_name=f"records_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                           mime="text/csv")

# ── 标签2：示波器数据 ──
with tab_osc:
    st.subheader("📈 示波器通道数据")
    df_osc = load_oscilloscope_data(instrument_id=inst_filter)

    if df_osc.empty:
        st.info("暂无示波器数据。")
    else:
        if "channel_id" in df_osc.columns:
            channels = sorted(df_osc["channel_id"].unique())
            selected_ch = st.selectbox("通道", options=["全部"] + [f"CH{c}" for c in channels], index=0, key="osc_ch")
            if selected_ch != "全部":
                df_osc = df_osc[df_osc["channel_id"] == int(selected_ch.replace("CH", ""))]

        display_osc = df_osc.copy()
        if "sync_datetime" in display_osc.columns:
            display_osc["sync_datetime"] = pd.to_datetime(display_osc["sync_datetime"], format="ISO8601", errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")

        rename_osc = {
            "max_voltage": "MAX", "min_voltage": "MIN",
            "rms_voltage": "RMS", "peak_to_peak": "PP",
            "avg_voltage": "AVER",
        }
        display_osc.rename(columns=rename_osc, inplace=True)

        channel_types = {}
        for _, row in df_osc.iterrows():
            meta = row.get("metadata", "{}")
            if isinstance(meta, str):
                try: meta = json.loads(meta)
                except: meta = {}
            ct = meta.get("channel_types", {})
            for k, v in ct.items():
                channel_types[int(k)] = v

        def fmt_val(val, ch_id):
            if pd.isna(val) or val == 0:
                return "0"
            unit_type = channel_types.get(ch_id, "V")
            abs_v = abs(val)
            if unit_type == "A":
                if abs_v >= 1: return f"{val:.6g} A"
                elif abs_v >= 0.001: return f"{val*1000:.6g} mA"
                else: return f"{val*1000000:.6g} μA"
            else:
                if abs_v >= 1: return f"{val:.6g} V"
                elif abs_v >= 0.001: return f"{val*1000:.6g} mV"
                else: return f"{val*1000000:.6g} μV"

        for col in ["MAX", "MIN", "RMS", "PP", "AVER"]:
            if col in display_osc.columns:
                display_osc[col] = display_osc.apply(
                    lambda r: fmt_val(r[col], r["channel_id"]) if "channel_id" in r else "", axis=1)

        time_units = {
            "FREQ": ("Hz", "kHz"), "PERIOD": ("s", "ms"),
            "RISE": ("s", "ms"), "FALL": ("s", "ms"),
            "PWIDTH": ("s", "ms"), "NWIDTH": ("s", "ms"),
            "DUTY": ("%", None),
        }
        for col, (u1, u2) in time_units.items():
            if col in display_osc.columns:
                display_osc[col] = display_osc[col].apply(
                    lambda v, u1=u1, u2=u2: "" if pd.isna(v) or v == 0 else (
                        f"{v/1000:.6g} {u2}" if u2 and abs(v) >= 1000 else f"{v:.6g} {u1}"
                    )
                )

        display_osc.insert(0, "行号", range(1, len(display_osc) + 1))

        show_osc_cols = [c for c in ["行号", "filename", "instrument_id",
                                      "channel_name", "sync_datetime",
                                      "MAX", "MIN", "RMS", "PP", "AVER",
                                      "FREQ", "PERIOD", "RISE", "FALL",
                                      "PWIDTH", "NWIDTH", "DUTY"]
                         if c in display_osc.columns]

        # ── 操作按钮栏 ──
        osc_btn1, osc_btn2, osc_btn3, osc_btn4 = st.columns([1, 1, 2, 2])
        with osc_btn1:
            if st.button("☑️ 全选", key="btn_osc_select_all"):
                st.session_state["osc_selected_ids"] = df_osc["id"].tolist()
                bump_editor_key("osc_selector")
                st.rerun()
        with osc_btn2:
            if st.button("☐️ 取消全选", key="btn_osc_deselect_all"):
                st.session_state["osc_selected_ids"] = []
                bump_editor_key("osc_selector")
                st.rerun()
        with osc_btn3:
            osc_range1, osc_range2 = st.columns(2)
            with osc_range1:
                osc_start = st.number_input("起始行", min_value=1, max_value=len(df_osc), value=1, key="osc_range_start")
            with osc_range2:
                osc_end = st.number_input("结束行", min_value=1, max_value=len(df_osc), value=len(df_osc), key="osc_range_end")
        with osc_btn4:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("📌 选择范围", key="btn_osc_select_range"):
                s = min(osc_start, osc_end) - 1
                e = max(osc_start, osc_end)
                st.session_state["osc_selected_ids"] = df_osc["id"].iloc[s:e].tolist()
                bump_editor_key("osc_selector")
                st.rerun()

        # ── 可编辑数据表（带选择列，id 隐藏但用于定位通道） ──
        # 注意：oscilloscope_channels 每行是一个「通道」，record_id 可能重复
        # （一条采集含多通道），所以勾选必须以唯一的通道主键 id 作为标识，
        # 否则同一 record 的多个通道会互相联动、取消其中一条会连累另一条。
        display_with_sel = build_selection_column(
            display_osc[["id"] + show_osc_cols],
            id_col="id",
            session_sel_key="osc_selected_ids",
        )
        _osc_key = fresh_editor_key("osc_selector")
        edited_osc = st.data_editor(
            display_with_sel,
            width="stretch", hide_index=True, height=400,
            disabled=show_osc_cols + ["id"], key=_osc_key,
            column_config={"选择": st.column_config.CheckboxColumn()},
        )

        # 稳定 key：勾选交互不再 bump，避免快速连点丢勾（同采集记录 tab）。
        new_osc_ids = edited_osc.loc[edited_osc["选择"].astype(bool), "id"].tolist()
        new_osc_ids = list(dict.fromkeys(new_osc_ids))  # 去重且保序
        st.session_state["osc_selected_ids"] = new_osc_ids
        selected_osc_ids = new_osc_ids

        # ── 选中操作 ──
        if selected_osc_ids:
            osc_act1, osc_act2 = st.columns([1, 1])
            with osc_act1:
                st.warning(f"已选择 {len(selected_osc_ids)} 条记录")
            with osc_act2:
                if st.button("🗑️ 删除选中记录", type="primary", key="btn_delete_osc"):
                    conn = get_connection()
                    cursor = conn.cursor()
                    # 选中的是通道行（id），先映射回所属采集记录 record_id 再整体删除
                    rec_ids = df_osc[df_osc["id"].isin(selected_osc_ids)]["record_id"].unique().tolist()
                    ph = ",".join("?" * len(rec_ids))
                    cursor.execute(f"DELETE FROM oscilloscope_channels WHERE record_id IN ({ph})", rec_ids)
                    cursor.execute(f"DELETE FROM acquisition_records WHERE id IN ({ph})", rec_ids)
                    conn.commit()
                    conn.close()
                    remaining = [i for i in (st.session_state.get("osc_selected_ids", []) or [])
                                 if i not in set(selected_osc_ids)]
                    st.session_state["osc_selected_ids"] = remaining
                    bump_editor_key("osc_selector")
                    st.success(f"已删除 {len(selected_osc_ids)} 条记录")
                    st.rerun()

        # CSV 导出 — 使用 df_osc 原始数值列（纯数值，便于后续分析），表头字段用 filename（英文）
        if selected_osc_ids:
            osc_csv = df_osc[df_osc["id"].isin(selected_osc_ids)].copy()
        else:
            osc_csv = df_osc.copy()
        # 从 metadata 提取 measurements 原始数值，补充到 CSV 列中
        def _get_meas_from_meta(meta_raw, ch_id, metric):
            if not meta_raw:
                return None
            try:
                m = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
                return m.get("measurements", {}).get(f"CH{ch_id}_{metric}")
            except Exception:
                return None
        if len(osc_csv):
            for metric in ["FREQ", "PERIOD", "RISE", "FALL", "PWIDTH", "NWIDTH", "DUTY"]:
                osc_csv[metric] = osc_csv.apply(
                    lambda r: _get_meas_from_meta(r.get("metadata"), r.get("channel_id"), metric), axis=1)
        # 统一时间格式
        if "sync_datetime" in osc_csv.columns:
            osc_csv["sync_datetime"] = pd.to_datetime(osc_csv["sync_datetime"], format="ISO8601", errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
        # 列顺序 + 重命名为英文字段名（含 filename 字段）
        osc_col_order = [
            ("record_id", "record_id"), ("instrument_id", "instrument_id"),
            ("filename", "filename"), ("sync_datetime", "sync_datetime"),
            ("channel_id", "channel_id"), ("channel_name", "channel_name"),
            ("sample_rate", "sample_rate"), ("voltage_range", "voltage_range"),
            ("max_voltage", "max_voltage"), ("min_voltage", "min_voltage"),
            ("avg_voltage", "avg_voltage"), ("rms_voltage", "rms_voltage"),
            ("peak_to_peak", "peak_to_peak"), ("data_points", "data_points"),
        ]
        for metric in ["FREQ", "PERIOD", "RISE", "FALL", "PWIDTH", "NWIDTH", "DUTY"]:
            osc_col_order.append((metric, metric.lower()))
        osc_rename = {old: new for old, new in osc_col_order}
        # 只保留存在的列
        osc_use_cols = [old for old, _ in osc_col_order if old in osc_csv.columns]
        osc_csv = osc_csv[osc_use_cols].rename(columns=osc_rename)
        csv_osc = osc_csv.to_csv(index=False).encode("utf-8-sig")
        st.download_button(label="📥 导出示波器数据", data=csv_osc,
                           file_name=f"osc_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                           mime="text/csv", key="dl_osc")

# ── 标签3：功率分析仪数据 ──
with tab_pwr:
    st.subheader("⚡ 功率分析仪数据")
    df_power = load_power_data_v2(instrument_id=inst_filter)

    if df_power.empty:
        st.info("暂无功率分析仪数据。")
    else:
        # 格式化时间
        display_pwr = df_power.copy()
        if "sync_datetime" in display_pwr.columns:
            display_pwr["sync_datetime"] = pd.to_datetime(display_pwr["sync_datetime"], format="ISO8601", errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")

        # Ch1 参数列定义：(列名, 显示名, 单位)
        ch1_cols = [
            ("ch1_urms", "URMS", "V"),
            ("ch1_irms", "IRMS", "A"),
            ("ch1_p",    "P",    "W"),
            ("ch1_lamb", "LAMB", ""),
            ("ch1_ithd", "ITHD", "%"),
            ("ch1_ik3",  "IK3",  "%"),
            ("ch1_ik5",  "IK5",  "%"),
            ("ch1_ik7",  "IK7",  "%"),
            ("ch1_ik9",  "IK9",  "%"),
        ]
        # Ch2 参数列定义
        ch2_cols = [
            ("ch2_urms",   "URMS",   "V"),
            ("ch2_irms",   "IRMS",   "A"),
            ("ch2_p",      "P",      "W"),
            ("ch2_ippeak", "IPPeak", "A"),
            ("ch2_impeak", "IMPeak", "A"),
            ("ch2_f1",     "F1",     "A"),
            ("ch2_idc",    "IDC",    "A"),
            ("ch2_f2",     "F2",     "%"),
        ]
        # Ch3 = Ch1 参数
        ch3_cols = [
            ("ch3_urms", "URMS", "V"),
            ("ch3_irms", "IRMS", "A"),
            ("ch3_p",    "P",    "W"),
            ("ch3_lamb", "LAMB", ""),
            ("ch3_ithd", "ITHD", "%"),
            ("ch3_ik3",  "IK3",  "%"),
            ("ch3_ik5",  "IK5",  "%"),
            ("ch3_ik7",  "IK7",  "%"),
            ("ch3_ik9",  "IK9",  "%"),
        ]
        # Ch4 = Ch2 参数
        ch4_cols = [
            ("ch4_urms",   "URMS",   "V"),
            ("ch4_irms",   "IRMS",   "A"),
            ("ch4_p",      "P",      "W"),
            ("ch4_ippeak", "IPPeak", "A"),
            ("ch4_impeak", "IMPeak", "A"),
            ("ch4_f3",     "F3",     "A"),
            ("ch4_idc",    "IDC",    "A"),
            ("ch4_f4",     "F4",     "%"),
        ]
        all_ch_cols = ch1_cols + ch2_cols + ch3_cols + ch4_cols

        def fmt_power(val, unit):
            if pd.isna(val) or val is None:
                return ""
            if unit == "%":
                return f"{val:g}%"
            if unit == "":
                return f"{val:g}"
            return f"{val:g} {unit}"

        # 格式化所有列
        for db_col, disp_name, unit in all_ch_cols:
            if db_col in display_pwr.columns:
                ch_num = db_col.split("_")[0]  # ch1, ch2, ch3, ch4
                col_key = f"{disp_name}_{ch_num}"
                display_pwr[col_key] = \
                    display_pwr[db_col].apply(lambda v, u=unit: fmt_power(v, u))

        # 构建显示列（record_id 和 id 隐藏，仅用于内部操作）
        display_pwr.insert(0, "行号", range(1, len(display_pwr) + 1))
        show_pwr_cols = ["行号", "filename", "instrument_id", "sync_datetime"]
        for db_col, disp_name, unit in all_ch_cols:
            if db_col in display_pwr.columns:
                ch_num = db_col.split("_")[0]
                col_key = f"{disp_name}_{ch_num}"
                if col_key in display_pwr.columns:
                    show_pwr_cols.append(col_key)

        # 重命名为友好名称
        rename_map = {"id": "序号", "instrument_id": "仪器ID", "sync_datetime": "采集时间"}
        for db_col, disp_name, unit in all_ch_cols:
            if db_col in display_pwr.columns:
                ch_num = db_col.split("_")[0].upper()  # CH1, CH2, CH3, CH4
                col_key = f"{disp_name}_{db_col.split('_')[0]}"
                rename_map[col_key] = f"{ch_num}_{disp_name}({unit})" if unit else f"{ch_num}_{disp_name}"

        display_pwr.rename(columns=rename_map, inplace=True)
        show_pwr_cols = [rename_map.get(c, c) for c in show_pwr_cols]

        # ── 操作按钮栏 ──
        btn_pwr1, btn_pwr2, btn_pwr3, btn_pwr4 = st.columns([1, 1, 2, 2])
        with btn_pwr1:
            if st.button("☑️ 全选", key="btn_pwr_select_all"):
                st.session_state["pwr_selected_ids"] = df_power["record_id"].tolist()
                bump_editor_key("pwr_selector")
                st.rerun()
        with btn_pwr2:
            if st.button("☐️ 取消全选", key="btn_pwr_deselect_all"):
                st.session_state["pwr_selected_ids"] = []
                bump_editor_key("pwr_selector")
                st.rerun()
        with btn_pwr3:
            range_pwr1, range_pwr2 = st.columns(2)
            with range_pwr1:
                pwr_start = st.number_input("起始行", min_value=1, max_value=len(df_power), value=1, key="pwr_range_start")
            with range_pwr2:
                pwr_end = st.number_input("结束行", min_value=1, max_value=len(df_power), value=len(df_power), key="pwr_range_end")
        with btn_pwr4:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("📌 选择范围", key="btn_pwr_select_range"):
                s = min(pwr_start, pwr_end) - 1
                e = max(pwr_start, pwr_end)
                st.session_state["pwr_selected_ids"] = df_power["record_id"].iloc[s:e].tolist()
                bump_editor_key("pwr_selector")
                st.rerun()

        # ── 可编辑数据表（带选择列，record_id 隐藏但用于删除） ──
        # 使用统一工具函数 build_selection_column 重建勾选列初始值；
        # data_editor 返回的 edited_pwr 里的「选择」列即为本轮最终真值。
        display_with_sel = build_selection_column(
            display_pwr[["record_id"] + show_pwr_cols],
            id_col="record_id",
            session_sel_key="pwr_selected_ids",
        )
        _pwr_key = fresh_editor_key("pwr_selector")
        edited_pwr = st.data_editor(
            display_with_sel,
            width="stretch", hide_index=True, height=400,
            disabled=show_pwr_cols + ["record_id"], key=_pwr_key,
            column_config={"选择": st.column_config.CheckboxColumn()},
        )

        # 稳定 key：勾选交互不再 bump，避免快速连点丢勾（同采集记录 tab）。
        new_pwr_ids = edited_pwr.loc[edited_pwr["选择"].astype(bool), "record_id"].tolist()
        new_pwr_ids = list(dict.fromkeys(new_pwr_ids))  # 去重且保序
        st.session_state["pwr_selected_ids"] = new_pwr_ids
        selected_pwr_ids = new_pwr_ids

        # ── 选中操作 ──
        if selected_pwr_ids:
            pwr_act1, pwr_act2 = st.columns([1, 1])
            with pwr_act1:
                st.warning(f"已选择 {len(selected_pwr_ids)} 条记录")
            with pwr_act2:
                if st.button("🗑️ 删除选中记录", type="primary", key="btn_delete_pwr"):
                    conn = get_connection()
                    cursor = conn.cursor()
                    ph = ",".join("?" * len(selected_pwr_ids))
                    cursor.execute(f"DELETE FROM power_data WHERE record_id IN ({ph})", selected_pwr_ids)
                    cursor.execute(f"DELETE FROM acquisition_records WHERE id IN ({ph})", selected_pwr_ids)
                    conn.commit()
                    conn.close()
                    remaining = [i for i in (st.session_state.get("pwr_selected_ids", []) or [])
                                 if i not in set(selected_pwr_ids)]
                    st.session_state["pwr_selected_ids"] = remaining
                    bump_editor_key("pwr_selector")
                    st.success(f"已删除 {len(selected_pwr_ids)} 条记录")
                    st.rerun()

        # CSV 导出（原始数值，不带格式化）— 字段名全部用英文，filename 保留为英文 "filename"
        if selected_pwr_ids:
            csv_pwr = df_power[df_power["record_id"].isin(selected_pwr_ids)].copy()
        else:
            csv_pwr = df_power.copy()
        # 时间格式化
        if "sync_datetime" in csv_pwr.columns:
            csv_pwr["sync_datetime"] = pd.to_datetime(csv_pwr["sync_datetime"], format="ISO8601", errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
        # 构建列顺序（固定：record_id, instrument_id, filename, sync_datetime → 34 参数列 → metadata）
        base_cols = ["record_id", "instrument_id", "filename", "sync_datetime"]
        # 34 参数列按原顺序
        param_cols = [c for c, _, _ in all_ch_cols if c in csv_pwr.columns]
        # 列重命名规则：保留 base_cols 原名（filename 就是 "filename"），参数列按 CH{num}_{name}(unit)
        pwr_rename = {}
        for db_col, disp_name, unit in all_ch_cols:
            if db_col in csv_pwr.columns:
                ch_num = db_col.split("_")[0].upper()  # ch1 -> CH1
                pwr_rename[db_col] = f"{ch_num}_{disp_name}({unit})" if unit else f"{ch_num}_{disp_name}"
        # 先拼 base 参数存在的列列表
        cols_out = [c for c in base_cols if c in csv_pwr.columns] + param_cols
        # rename + 输出
        csv_pwr = csv_pwr[cols_out].rename(columns=pwr_rename)
        csv_data = csv_pwr.to_csv(index=False).encode("utf-8-sig")
        st.download_button(label="📥 导出功率数据 CSV", data=csv_data,
                           file_name=f"power_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                           mime="text/csv", key="dl_pwr")

# ── 标签4：报告生成 ──
with tab_report:
    st.subheader("📝 自动生成报告")
    st.caption("从下方数据表中选择要生成报告的记录，上传 BOM 表，一键生成。")

    # 检查报告生成模块
    if not REPORT_GENERATOR_PATH.exists():
        st.error(f"报告生成模块不存在: {REPORT_GENERATOR_PATH}")
        st.info("请将 auto_report_generator.py 放到项目根目录。")
    else:
        # ── 步骤1：选择数据 ──
        st.markdown("#### 步骤1：选择数据")

        # 加载示波器数据用于选择
        report_df = load_oscilloscope_data(instrument_id=inst_filter)

        if report_df.empty:
            st.info("暂无示波器数据。")
        else:
            # 准备显示数据
            report_display = report_df.copy()
            if "sync_datetime" in report_display.columns:
                report_display["sync_datetime"] = pd.to_datetime(report_display["sync_datetime"], format="ISO8601", errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")

            report_show_cols = [c for c in ["filename", "channel_name", "sync_datetime",
                                             "max_voltage", "min_voltage", "rms_voltage", "peak_to_peak", "avg_voltage"]
                                if c in report_display.columns]

            # 从 metadata 获取通道类型
            channel_types = {}
            for _, row in report_df.iterrows():
                meta = row.get("metadata", "{}")
                if isinstance(meta, str):
                    try: meta = json.loads(meta)
                    except: meta = {}
                ct = meta.get("channel_types", {})
                for k, v in ct.items():
                    channel_types[int(k)] = v

            # 数值加单位
            def report_fmt_val(val, ch_id):
                if pd.isna(val) or val == 0:
                    return "0"
                unit_type = channel_types.get(ch_id, "V")
                abs_v = abs(val)
                if unit_type == "A":
                    if abs_v >= 1: return f"{val:.6g} A"
                    elif abs_v >= 0.001: return f"{val*1000:.6g} mA"
                    else: return f"{val*1000000:.6g} μA"
                else:
                    if abs_v >= 1: return f"{val:.6g} V"
                    elif abs_v >= 0.001: return f"{val*1000:.6g} mV"
                    else: return f"{val*1000000:.6g} μV"

            for col in ["max_voltage", "min_voltage", "rms_voltage", "peak_to_peak", "avg_voltage"]:
                if col in report_display.columns:
                    report_display[col] = report_display.apply(
                        lambda r: report_fmt_val(r[col], r["channel_id"]) if "channel_id" in r else "", axis=1)

            # 重命名列
            report_display.rename(columns={
                "max_voltage": "MAX", "min_voltage": "MIN",
                "rms_voltage": "RMS", "peak_to_peak": "PP", "avg_voltage": "AVER",
            }, inplace=True)

            report_show_cols = [c for c in ["filename", "channel_name", "sync_datetime",
                                             "MAX", "MIN", "RMS", "PP", "AVER"]
                                if c in report_display.columns]

            # 添加行号列（替代数据库 ID）
            report_display.insert(0, "行号", range(1, len(report_display) + 1))

            # 快捷选择按钮
            btn_col1, btn_col2, btn_col3 = st.columns([2, 2, 3])
            with btn_col1:
                if st.button("☑️ 全选", key="btn_select_all"):
                    st.session_state["report_selected_ids"] = report_df["id"].tolist()
                    bump_editor_key("report_selector")
                    st.rerun()
            with btn_col2:
                if st.button("☐️ 取消全选", key="btn_deselect_all"):
                    st.session_state["report_selected_ids"] = []
                    bump_editor_key("report_selector")
                    st.rerun()
            with btn_col3:
                range_col1, range_col2 = st.columns(2)
                with range_col1:
                    range_start = st.number_input("起始行", min_value=1, max_value=len(report_df), value=1, key="range_start")
                with range_col2:
                    range_end = st.number_input("结束行", min_value=1, max_value=len(report_df), value=len(report_df), key="range_end")
                if st.button("📌 选择范围", key="btn_select_range"):
                    s = min(range_start, range_end) - 1
                    e = max(range_start, range_end)
                    selected = report_df["id"].iloc[s:e].tolist()
                    st.session_state["report_selected_ids"] = selected
                    bump_editor_key("report_selector")
                    st.rerun()

            # 使用统一工具函数 build_selection_column 重建勾选列初始值；
            # data_editor 返回的 edited_report 里的「选择」列即为本轮最终真值。
            report_with_sel = build_selection_column(
                report_display[["id", "行号"] + report_show_cols],
                id_col="id",
                session_sel_key="report_selected_ids",
            )
            _report_key = fresh_editor_key("report_selector")

            edited_report = st.data_editor(
                report_with_sel,
                width="stretch",
                hide_index=True,
                height=400,
                disabled=["行号"] + report_show_cols,
                key=_report_key,
                column_config={"选择": st.column_config.CheckboxColumn()},
            )

            # 稳定 key：勾选交互不再 bump，避免快速连点丢勾（同采集记录 tab）。
            new_report_ids = edited_report.loc[edited_report["选择"].astype(bool), "id"].tolist()
            new_report_ids = list(dict.fromkeys(new_report_ids))  # 去重且保序
            st.session_state["report_selected_ids"] = new_report_ids
            selected_report_ids = new_report_ids
            selected_count = len(selected_report_ids)

            if selected_count > 0:
                st.success(f"✅ 已选择 {selected_count} 条记录")

                # 删除不想要的记录
                if st.button("🗑️ 删除选中记录", type="secondary", key="btn_delete_report"):
                    conn = get_connection()
                    cursor = conn.cursor()
                    rec_ids = report_df[report_df["id"].isin(selected_report_ids)]["record_id"].unique().tolist()
                    if rec_ids:
                        ph = ",".join("?" * len(rec_ids))
                        cursor.execute(f"DELETE FROM oscilloscope_channels WHERE record_id IN ({ph})", rec_ids)
                        cursor.execute(f"DELETE FROM power_measurements WHERE record_id IN ({ph})", rec_ids)
                        cursor.execute(f"DELETE FROM power_data WHERE record_id IN ({ph})", rec_ids)
                        cursor.execute(f"DELETE FROM acquisition_records WHERE id IN ({ph})", rec_ids)
                        conn.commit()
                    conn.close()
                    st.success(f"已删除 {selected_count} 条记录")
                    st.rerun()
            else:
                st.info("请在左侧勾选要生成报告的数据行")

        # ── 步骤2：上传 BOM ──
        st.markdown("#### 步骤2：上传 BOM 表")
        bom_file = st.file_uploader("选择 BOM 表（.xlsx）", type=["xlsx", "xls"], key="bom_upload")
        if bom_file:
            st.success(f"已上传: {bom_file.name}")

        # ── 步骤3：生成报告 ──
        st.markdown("#### 步骤3：生成报告")
        if not bom_file:
            st.warning("请先上传 BOM 表")
        elif selected_count == 0:
            st.warning("请先选择数据")
        else:
            # 如果 session_state 中有已生成的报告，先显示下载按钮
            if "generated_reports" in st.session_state and st.session_state.generated_reports:
                st.success("✅ 报告已生成！")
                report_files = st.session_state.generated_reports
                cols = st.columns(min(len(report_files), 3))
                for i, (fname, fpath) in enumerate(report_files):
                    if os.path.exists(fpath):
                        with cols[i % len(cols)]:
                            with open(fpath, "rb") as f:
                                st.download_button(
                                    label=f"📥 下载 {fname}",
                                    data=f,
                                    file_name=fname,
                                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                    key=f"dl_report_{i}",
                                )
                st.divider()

            if st.button("🚀 生成报告", type="primary", key="btn_gen_report"):
                # 清除旧的报告状态
                st.session_state.generated_reports = []
                with st.spinner("正在生成报告，请稍候..."):
                    try:
                        # 从选中记录导出（带单位字符串，和原数据格式一致）
                        selected_rows = report_df[report_df["id"].isin(selected_report_ids)]

                        # 通道类型固定映射：CH1=电压, CH2=电流, CH3=电压, CH4=电流
                        CHANNEL_UNIT = {1: "V", 2: "A", 3: "V", 4: "A"}

                        def fmt_raw(val, ch_id):
                            """格式化为带单位的字符串（绝对值，无负号）"""
                            if val is None:
                                return None
                            val = abs(val)
                            if val == int(val):
                                val = int(val)
                            unit_type = CHANNEL_UNIT.get(ch_id, "V")
                            abs_v = abs(val)
                            if unit_type == "A":
                                if abs_v >= 1: return f"{val} A"
                                elif abs_v >= 0.001: return f"{val*1000} mA"
                                else: return f"{val*1000000} μA"
                            else:
                                if abs_v >= 1: return f"{val} V"
                                elif abs_v >= 0.001: return f"{val*1000} mV"
                                else: return f"{val*1000000} μV"

                        csv_rows = []
                        for _, row in selected_rows.iterrows():
                            meta = row.get("metadata", "{}")
                            if isinstance(meta, str):
                                try: meta = json.loads(meta)
                                except: meta = {}
                            measurements = meta.get("measurements", {})
                            ch_id = row["channel_id"]
                            csv_rows.append({
                                "filename": row.get("filename", ""),
                                "MAX": fmt_raw(measurements.get(f"CH{ch_id}_MAX"), ch_id),
                                "MIN": fmt_raw(measurements.get(f"CH{ch_id}_MIN"), ch_id),
                                "RMS": fmt_raw(measurements.get(f"CH{ch_id}_RMS"), ch_id),
                                "PP": fmt_raw(measurements.get(f"CH{ch_id}_PP"), ch_id),
                                "AVER": fmt_raw(measurements.get(f"CH{ch_id}_AVER"), ch_id),
                            })
                        csv_rows = [r for r in csv_rows if any(v is not None for v in r.values())]

                        if not csv_rows:
                            st.error("选中数据中没有有效测量值")
                        else:
                            tmp_dir = tempfile.mkdtemp()
                            # 导出为 .xlsx（报告生成器要求）
                            xlsx_path = os.path.join(tmp_dir, "raw_data.xlsx")
                            df_export = pd.DataFrame(csv_rows)
                            df_export.to_excel(xlsx_path, index=False, engine="openpyxl")

                            bom_path = os.path.join(tmp_dir, "bom.xlsx")
                            with open(bom_path, "wb") as f:
                                f.write(bom_file.getvalue())

                            # 输出目录
                            output_dir = os.path.join(str(Path(__file__).parent), "output", "reports")
                            os.makedirs(output_dir, exist_ok=True)

                            # 用子进程调用报告生成（win32com 需要独立进程）
                            import subprocess
                            script_path = str(REPORT_GENERATOR_PATH)
                            result = subprocess.run(
                                [sys.executable, script_path, "--cli", bom_path, xlsx_path, output_dir],
                                capture_output=True, text=True, timeout=120,
                            )

                            # 显示日志
                            with st.expander("📋 生成日志", expanded=True):
                                if result.stdout:
                                    st.text(result.stdout)
                                if result.stderr:
                                    st.error(result.stderr)

                            if result.returncode == 0:
                                st.success("✅ 报告生成成功！")
                                # 查找生成的报告文件并保存到 session_state
                                report_files = [f for f in os.listdir(output_dir) if f.endswith((".xlsx", ".xlsm"))]
                                if report_files:
                                    # 保存报告文件信息到 session_state，避免下载时状态丢失
                                    st.session_state.generated_reports = [
                                        (fname, os.path.join(output_dir, fname)) for fname in report_files
                                    ]
                                    st.rerun()  # 刷新页面以显示下载按钮
                            else:
                                st.error("❌ 报告生成失败，请查看日志")

                            try:
                                os.remove(xlsx_path)
                                os.remove(bom_path)
                                os.rmdir(tmp_dir)
                            except:
                                pass

                    except Exception as e:
                        st.error(f"报告生成出错: {e}")
                        import traceback
                        st.code(traceback.format_exc())

# ── 标签5：设备管理 ──
with tab_devices:
    st.subheader("⚙️ 设备管理")
    st.caption("管理监控设备列表，增删改查设备配置。")
    
    # 加载设备配置
    DEVICES_JSON = os.path.join(Path(__file__).parent, "devices.json")
    
    def load_devices_config():
        """加载设备配置"""
        if os.path.exists(DEVICES_JSON):
            try:
                with open(DEVICES_JSON, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                st.error(f"读取配置失败: {e}")
        return {"devices": []}
    
    def save_devices_config(config):
        """保存设备配置"""
        try:
            with open(DEVICES_JSON, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
            return True
        except Exception as e:
            st.error(f"保存配置失败: {e}")
            return False
    
    # 初始化 session state
    if 'devices_config' not in st.session_state:
        st.session_state.devices_config = load_devices_config()
    
    config = st.session_state.devices_config
    devices = config.get("devices", [])
    
    # 显示当前设备列表
    st.markdown("#### 当前设备列表")
    if devices:
        df_devices = pd.DataFrame(devices)
        # 确保所有列都存在
        for col in ["id", "type", "ip", "model", "enabled"]:
            if col not in df_devices.columns:
                df_devices[col] = ""
        df_devices = df_devices[["id", "type", "ip", "model", "enabled"]]
        df_devices.columns = ["设备ID", "类型", "IP地址", "型号", "启用"]
        st.dataframe(df_devices, use_container_width=True, hide_index=True)
    else:
        st.info("暂无设备配置")
    
    # 添加新设备
    st.markdown("#### 添加新设备")
    with st.form("add_device_form"):
        col1, col2 = st.columns(2)
        with col1:
            new_id = st.text_input("设备ID", placeholder="如: EP11-652")
            new_type = st.selectbox("设备类型", ["power_analyzer", "oscilloscope"], 
                                   format_func=lambda x: "功率分析仪" if x == "power_analyzer" else "示波器")
        with col2:
            new_ip = st.text_input("IP地址", placeholder="如: 172.17.76.201")
            new_model = st.text_input("型号", placeholder="如: WT1804E 或 DLM5000")
        new_enabled = st.checkbox("启用此设备", value=True)
        
        submitted = st.form_submit_button("➕ 添加设备", type="primary")
        if submitted:
            if not new_id or not new_ip:
                st.error("设备ID和IP地址不能为空")
            else:
                # 检查ID是否已存在
                if any(d.get("id") == new_id for d in devices):
                    st.error(f"设备ID '{new_id}' 已存在")
                else:
                    devices.append({
                        "id": new_id,
                        "type": new_type,
                        "ip": new_ip,
                        "model": new_model,
                        "enabled": new_enabled
                    })
                    config["devices"] = devices
                    if save_devices_config(config):
                        st.session_state.devices_config = config
                        st.success(f"✅ 设备 '{new_id}' 添加成功！")
                        st.rerun()
    
    # 编辑/删除设备
    st.markdown("#### 编辑/删除设备")
    if devices:
        device_options = {f"{d['id']} ({d.get('model', '')})": d for d in devices}
        selected = st.selectbox("选择设备", list(device_options.keys()), key="edit_select")
        selected_device = device_options[selected]
        
        with st.form("edit_device_form"):
            col1, col2 = st.columns(2)
            with col1:
                edit_id = st.text_input("设备ID", value=selected_device["id"], disabled=True)
                edit_type = st.selectbox("设备类型", ["power_analyzer", "oscilloscope"],
                                        index=0 if selected_device["type"] == "power_analyzer" else 1,
                                        format_func=lambda x: "功率分析仪" if x == "power_analyzer" else "示波器")
            with col2:
                edit_ip = st.text_input("IP地址", value=selected_device.get("ip", ""))
                edit_model = st.text_input("型号", value=selected_device.get("model", ""))
            edit_enabled = st.checkbox("启用此设备", value=selected_device.get("enabled", True))
            
            col_save, col_del = st.columns(2)
            with col_save:
                save_submitted = st.form_submit_button("💾 保存修改", type="primary")
            with col_del:
                delete_submitted = st.form_submit_button("🗑️ 删除设备", type="secondary")
            
            if save_submitted:
                # 更新设备
                for d in devices:
                    if d["id"] == edit_id:
                        d["type"] = edit_type
                        d["ip"] = edit_ip
                        d["model"] = edit_model
                        d["enabled"] = edit_enabled
                        break
                config["devices"] = devices
                if save_devices_config(config):
                    st.session_state.devices_config = config
                    st.success(f"✅ 设备 '{edit_id}' 修改成功！")
                    st.rerun()
            
            if delete_submitted:
                # 删除设备
                devices = [d for d in devices if d["id"] != edit_id]
                config["devices"] = devices
                if save_devices_config(config):
                    st.session_state.devices_config = config
                    st.success(f"✅ 设备 '{edit_id}' 已删除！")
                    st.rerun()
    else:
        st.info("没有可编辑的设备")
    
    st.divider()
    st.caption(f"配置文件路径: {DEVICES_JSON}")

# ── 标签6：监控控制 ──
with tab_monitor:
    st.subheader("🔴 监控控制")
    st.caption("选择要监控的设备，手动启动/停止每台设备。")

    # 自动刷新控制
    auto_col1, auto_col2 = st.columns([1, 1])
    with auto_col1:
        auto_refresh = st.checkbox("自动刷新日志", value=False, key="auto_refresh_logs")
    with auto_col2:
        refresh_interval = st.selectbox(
            "刷新间隔",
            options=[3, 5, 10, 30],
            index=1,
            format_func=lambda x: f"{x} 秒",
            key="refresh_interval",
            disabled=not auto_refresh,
        )

    if auto_refresh:
        # ⚠️ 不使用 meta http-equiv="refresh"（浏览器 HTTP 级硬刷新），
        # 因为硬刷新会清空 Streamlit widget 缓存和所有 session_state
        # 中未持久化到 cookie/server 的内容，导致其它 tab 的勾选框
        # 被莫名其妙重置为未选中。
        #
        # 改用 Streamlit 「time.sleep + st.rerun()」的软刷新：
        #  — 只重跑 Python 脚本，不重载浏览器页面；
        #  — 所有 session_state、勾选、筛选、tab 选择全部保留；
        #  — 日志面板能显示最新内容，完全无副作用。
        import time as _time
        status = st.empty()
        for remaining in range(refresh_interval, 0, -1):
            status.caption(f"🔄 {remaining} 秒后自动刷新下一页内容（不会丢勾选/筛选）…")
            _time.sleep(1)
        status.empty()
        st.rerun()
    
    import subprocess
    import signal
    
    _BASE = str(Path(__file__).parent)
    MONITOR_SCRIPT = os.path.join(_BASE, "multi_device_monitor.py")
    LOG_DIR = os.path.join(_BASE, "logs")
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # 加载设备配置
    DEVICES_JSON = os.path.join(_BASE, "devices.json")
    if os.path.exists(DEVICES_JSON):
        with open(DEVICES_JSON, 'r', encoding='utf-8') as f:
            devices = json.load(f).get("devices", [])
    else:
        devices = []
    
    def get_device_pid(device_id):
        """获取指定设备的PID文件路径"""
        return os.path.join(LOG_DIR, f".{device_id}.pid")
    
    def is_device_running(device_id):
        """检查指定设备是否在运行（通过PID文件）"""
        pid_file = get_device_pid(device_id)
        if not os.path.exists(pid_file):
            return False
        try:
            with open(pid_file, 'r') as f:
                pid = int(f.read().strip())
            if sys.platform == 'win32':
                import ctypes
                kernel32 = ctypes.windll.kernel32
                SYNCHRONIZE = 0x00100000
                handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
            else:
                os.kill(pid, 0)
                return True
        except (ValueError, OSError):
            pass
        # PID 文件过期，清理
        try:
            os.remove(pid_file)
        except:
            pass
        return False
    
    def start_device(device_id, device_type, device_ip):
        """启动单个设备监控"""
        pid_file = get_device_pid(device_id)
        log_file = os.path.join(LOG_DIR, f"{device_id}.log")
        try:
            # 清空日志文件
            with open(log_file, 'w') as f:
                pass
            
            # 检查脚本文件是否存在
            if not os.path.exists(MONITOR_SCRIPT):
                st.error(f"监控脚本不存在: {MONITOR_SCRIPT}")
                return False
            
            # 启动进程，传入设备ID作为参数
            import subprocess
            log_fh = open(log_file, 'a', encoding='utf-8', errors='replace')
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            proc = subprocess.Popen(
                [sys.executable, MONITOR_SCRIPT, "--device", device_id],
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=_BASE,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            
            # 等待一小段时间验证进程是否存活
            import time
            time.sleep(0.5)
            
            if proc.poll() is not None:
                # 进程已退出，启动失败
                log_fh.close()
                error_msg = f"进程启动后立即退出，返回码: {proc.returncode}"
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    log_content = f.read()
                if log_content:
                    error_msg += f"\n日志:\n{log_content[:500]}"
                st.error(f"启动 {device_id} 失败: {error_msg}")
                return False
            
            # 保存PID
            with open(pid_file, 'w') as f:
                f.write(str(proc.pid))
            
            st.success(f"{device_id} 进程已启动 (PID: {proc.pid})")
            return True
        except Exception as e:
            st.error(f"启动 {device_id} 失败: {e}")
            import traceback
            st.code(traceback.format_exc())
            return False
    
    def stop_device(device_id):
        """停止单个设备监控"""
        pid_file = get_device_pid(device_id)
        killed = False
        try:
            if os.path.exists(pid_file):
                with open(pid_file, 'r') as f:
                    pid = int(f.read().strip())
                if sys.platform == 'win32':
                    import ctypes
                    kernel32 = ctypes.windll.kernel32
                    PROCESS_TERMINATE = 0x0001
                    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                    if handle:
                        kernel32.TerminateProcess(handle, 0)
                        kernel32.CloseHandle(handle)
                        killed = True
                else:
                    import signal
                    os.kill(pid, signal.SIGTERM)
                    killed = True
        except (ValueError, OSError) as e:
            if not killed:
                st.error(f"停止 {device_id} 失败: {e}")
                return False
        # 清理 PID 文件（与进程终止分开处理）
        try:
            if os.path.exists(pid_file):
                os.remove(pid_file)
        except OSError:
            pass
        return True
    
    def get_device_log(device_id, lines=50):
        """获取指定设备的日志"""
        log_file = os.path.join(LOG_DIR, f"{device_id}.log")
        if os.path.exists(log_file):
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.readlines()
                    return ''.join(content[-lines:])
            except:
                return ""
        return ""

    # 显示设备状态表格
    st.markdown("#### 设备状态")

    # 创建设备状态数据
    status_data = []
    for dev in devices:
        dev_id = dev.get("id", "")
        dev_type = "示波器" if dev.get("type") == "oscilloscope" else "功率分析仪"
        running = is_device_running(dev_id)
        status = "🟢 运行中" if running else "⚪ 未连接"

        status_data.append({
            "设备ID": dev_id,
            "型号": dev.get("model", ""),
            "类型": dev_type,
            "IP地址": dev.get("ip", ""),
            "状态": status,
        })
    
    if status_data:
        # 显示设备表格
        df_display = pd.DataFrame(status_data)
        display_cols = ["设备ID", "型号", "类型", "IP地址", "状态"]
        st.dataframe(df_display[display_cols], use_container_width=True, hide_index=True)
        
        # 每台设备的独立控制
        st.markdown("#### 设备控制")
        
        for dev in devices:
            dev_id = dev.get("id", "")
            dev_type_name = "示波器" if dev.get("type") == "oscilloscope" else "功率分析仪"
            running = is_device_running(dev_id)

            # 设备控制行
            col1, col2 = st.columns([3, 3])
            with col1:
                icon = "🟢" if running else "⚪"
                st.write(f"{icon} **{dev_id}** ({dev_type_name})")
            with col2:
                if running:
                    if st.button(f"⏹️ 断开", key=f"stop_{dev_id}"):
                        if stop_device(dev_id):
                            st.success(f"{dev_id} 已断开")
                            st.rerun()
                else:
                    if st.button(f"▶️ 连接", key=f"start_{dev_id}", type="primary"):
                        if start_device(dev_id, dev.get("type"), dev.get("ip")):
                            st.rerun()
            
            # 实时日志（默认展开显示）
            log_content = get_device_log(dev_id, lines=50)
            if log_content or running:
                with st.expander(f"📋 {dev_id} 实时日志", expanded=running):
                    if log_content:
                        st.code(log_content, language='text')
                    else:
                        st.caption("暂无日志...")
                    # 日志文件信息
                    log_file = os.path.join(LOG_DIR, f"{dev_id}.log")
                    if os.path.exists(log_file):
                        size_kb = os.path.getsize(log_file) / 1024
                        st.caption(f"日志大小: {size_kb:.1f} KB")
    else:
        st.info("暂无设备配置，请在「设备管理」页面添加设备")
    
    st.divider()
    st.caption(f"日志目录: {LOG_DIR}")

# ============================================================
# 底部信息
# ============================================================

st.divider()
st.caption(f"数据库路径: {DB_PATH}  |  更新时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
