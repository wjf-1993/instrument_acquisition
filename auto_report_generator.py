# -*- coding: utf-8 -*-
"""
电子元件试验需求自动化报告生成器 v4.0
基于 v6 Prompt 业务规则实现

v4.0 改动：使用 win32com 操作模板，真正模拟人工"复制行→插入行"
  - openpyxl 仅用于读取 BOM 和原数据（只读）
  - win32com 用于操作模板：复制插入行、填充数据、保存
  - Excel 原生处理格式、公式、合并单元格、行高，零偏差

内置配置：
  - 对应关系表（类型→参数映射）已硬编码
  - 电压/电流模板从 ./templates/ 目录自动加载

用户只需提供：BOM表 + 原数据表
输出：填入数据后的 Excel 报告文件

支持三种使用方式：
  1. GUI 模式：python auto_report_generator.py
  2. CLI 模式：python auto_report_generator.py --cli <BOM> <原数据> [输出目录]
  3. API 调用：from auto_report_generator import generate_reports
"""

import openpyxl
import re
import sys
import os
import threading
from pathlib import Path
from datetime import datetime

# ============================================================
# 脚本所在目录（用于定位 templates/）
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SCRIPT_DIR / "templates"

# ============================================================
# 配置区域 - 所有业务规则集中管理（内置，无需用户输入）
# ============================================================

# ---------- 对应关系表（类型 → 所需参数 + 目标单位）----------
TYPE_PARAM_MAPPING = {
    # --- 电压模板参数（子表1） ---
    "陶瓷电容":          [("稳定Vmax", "V"), ("启动Vmax", "V"), ("稳定Vrms", "V")],
    "瓷片引线电容":      [("稳定Vmax", "V"), ("启动Vmax", "V"), ("稳定Vrms", "V")],
    "安规电容":          [("稳定Vrms", "V")],
    "薄膜电容(普通电路)": [("稳定Vmax", "V"), ("启动Vmax", "V")],
    "电解电容":          [("稳定Vmax", "V"), ("启动Vmax", "V"), ("稳定Vrms", "V")],
    "肖特基二极管":      [("稳定Vmax", "V"), ("启动Vmax", "V")],
    "普通硅二极管(光阻)": [("稳定Vmax", "V"), ("启动Vmax", "V")],
    "普通硅二极管(刀刮)": [("稳定Vmax", "V"), ("启动Vmax", "V")],
    "整流桥(光阻)":      [("稳定Vmax", "V"), ("启动Vmax", "V")],
    "整流桥(刀刮)":      [("稳定Vmax", "V"), ("启动Vmax", "V")],
    "Vcc":               [("稳定Vmax", "V"), ("启动Vmax", "V"), ("稳定Vrms", "V")],
    "Vds":               [("启动Vmax", "V")],
    "Vgs":               [("稳定Vmax", "V")],
    "Vce":               [("稳定Vmax", "V")],

    # --- 电压模板参数（子表2） ---
    "保险电阻":          [("稳定Vmax", "V"), ("稳定Vrms", "V")],
    "贴片电阻":          [("稳定Vmax", "V"), ("稳定Vrms", "V")],
    "插件电阻":          [("稳定Vmax", "V"), ("稳定Vrms", "V")],

    # --- 电流模板参数（子表3） ---
    "薄膜电容(谐振电路)_I": [("稳定Irms", "mA")],
    "肖特基二极管_I":    [("稳定Iavg", "mA")],
    "普通硅二极管(光阻)_I": [("稳定Iavg", "mA"), ("启动Ipk", "A")],
    "普通硅二极管(刀刮)_I": [("稳定Iavg", "mA"), ("启动Ipk", "A")],
    "整流桥(光阻)_I":    [("稳定Iavg", "mA"), ("启动Ipk", "A")],
    "整流桥(刀刮)_I":    [("稳定Iavg", "mA"), ("启动Ipk", "A")],
    "稳压二极管":        [("稳定Irms", "mA")],

    # --- 电流模板参数（子表4） ---
    "Id":                [("稳定Ipk", "A"), ("启动Ipk", "A")],
    "Ic":                [("稳定Ipk", "A"), ("启动Ipk", "A")],

    # --- 电流模板参数（子表5） ---
    "保险电阻_I":        [("启动Ipk", "A")],
    "插件电阻_I":        [("启动Ipk", "A")],
    "快熔断保险丝":      [("启动Ipk", "A"), ("稳定Irms", "mA")],
    "慢熔断保险丝":      [("启动Ipk", "A"), ("稳定Irms", "mA")],

    # --- 电流模板参数（子表6） ---
    "功率电感(PC40)":    [("稳定Ipk", "A"), ("启动Ipk", "A")],
    "功率电感(PC95)":    [("稳定Ipk", "A"), ("启动Ipk", "A")],
    "变压器(PC40)":      [("稳定Ipk", "A"), ("启动Ipk", "A")],
    "变压器(PC95)":      [("稳定Ipk", "A"), ("启动Ipk", "A")],
    "滤波电感":          [("稳定Ipk", "A"), ("启动Ipk", "A")],
}

# ---------- filename 与 BOM 类型映射 ----------
FILENAME_TYPE_MAPPING = {
    "陶瓷电容": "18μF_500V", "瓷片引线电容": "18μF_500V",
    "安规电容": "18μF_500V", "薄膜电容(普通电路)": "18μF_500V",
    "电解电容": "18μF_500V", "贴片电阻": "1Ω/1W",
    "快熔断保险丝": "600V_2A", "慢熔断保险丝": "600V_2A",
    "功率电感(PC40)": "1.5mH", "功率电感(PC95)": "1.5mH",
    "变压器(PC40)": "1.5mH", "变压器(PC95)": "1.5mH",
    "滤波电感": "1.5mH", "稳压二极管": "3.3V_0.2W",
    "Vcc": "JW1807CHK", "Vds": "6.2A_20V",
    "Vgs": "6.2A_20V", "Vce": "6.2A_20V",
    "Id": "6.2A_20V", "Ic": "6.2A_20V",
    "薄膜电容(谐振电路)": "18μF_500V", "肖特基二极管": "600V_2A",
    "普通硅二极管(光阻)": "600V_2A", "普通硅二极管(刀刮)": "600V_2A",
    "整流桥(光阻)": "600V_2A", "整流桥(刀刮)": "600V_2A",
    "保险电阻": "1Ω/1W", "插件电阻": "1Ω/1W",
}

# ---------- filename 缩写与 BOM 类型映射（用于同一位号多种类型的情况）----------
# filename格式：位号-缩写-状态-电压类型-编号
# 例如：Q1-DS-W-V-1 → 位号Q1, 缩写DS → Vds
FILENAME_ABBR_TYPE_MAPPING = {
    "VCC": "Vcc",
    "DS":  "Vds",
    "GS":  "Vgs",
    "CE":  "Vce",
    "ID":  "Id",
    "IC":  "Ic",
}

# ---------- 电流模板类型集合（用于 filename -I-/-V- 过滤）----------
# filename 中含 -I- 表示电流通道数据，只匹配此集合中的类型
# filename 中含 -V- 表示电压通道数据，排除此集合中的类型
_CURRENT_TYPES = {
    # 子表3
    "薄膜电容(谐振电路)", "肖特基二极管", "普通硅二极管(光阻)", "普通硅二极管(刀刮)",
    "整流桥(光阻)", "整流桥(刀刮)", "稳压二极管",
    # 子表4
    "Id", "Ic",
    # 子表5
    "保险电阻", "插件电阻", "快熔断保险丝", "慢熔断保险丝",
    # 子表6
    "功率电感(PC40)", "功率电感(PC95)", "变压器(PC40)", "变压器(PC95)", "滤波电感",
}

# ---------- 子表结构定义 ----------
SUBTABLE_CONFIG = {
    "电压模板": {
        "子表1": ["陶瓷电容", "瓷片引线电容", "安规电容", "薄膜电容(普通电路)",
                  "薄膜电容(谐振电路)", "电解电容", "肖特基二极管", "普通硅二极管(光阻)",
                  "普通硅二极管(刀刮)", "整流桥(光阻)", "整流桥(刀刮)", "Vcc", "Vds", "Vgs", "Vce"],
        "子表2": ["保险电阻", "贴片电阻", "插件电阻"],
    },
    "电流模板": {
        "子表3": ["薄膜电容(谐振电路)", "肖特基二极管", "普通硅二极管(光阻)",
                  "普通硅二极管(刀刮)", "整流桥(光阻)", "整流桥(刀刮)", "稳压二极管"],
        "子表4": ["Id", "Ic"],
        "子表5": ["保险电阻", "插件电阻", "快熔断保险丝", "慢熔断保险丝"],
        "子表6": ["功率电感(PC40)", "功率电感(PC95)", "变压器(PC40)",
                  "变压器(PC95)", "滤波电感"],
    },
}

# ---------- 参数映射：模板参数名 → (原数据列, 提取方式) ----------
PARAM_MAPPING = {
    "Vmax": ("MAX_MIN", "max_abs"),
    "Ipk":  ("MAX_MIN", "max_abs"),
    "Vrms": ("RMS", "direct"),
    "Irms": ("RMS", "direct"),
    "Iavg": ("AVER", "direct"),
}

# ---------- 状态映射 ----------
STATE_MAPPING = {"W": "稳定", "Q": "启动"}

# ---------- 单位转换规则 ----------
UNIT_CONVERSIONS = {
    ("μA", "mA"): 0.001, ("uA", "mA"): 0.001,
    ("A", "mA"): 1000,    ("mA", "A"): 0.001,
    ("V", "V"): 1,        ("μV", "V"): 0.001, ("uV", "V"): 0.001, ("mV", "V"): 0.001,
    ("μF", "μF"): 1,      ("uF", "μF"): 1,
    ("nF", "μF"): 0.001,  ("pF", "μF"): 0.000001,
    ("mH", "mH"): 1,      ("μH", "mH"): 0.001, ("uH", "mH"): 0.001, ("H", "mH"): 1000,
    ("Ω", "Ω"): 1,        ("kΩ", "Ω"): 1000,    ("MΩ", "Ω"): 1000000,
    ("W", "W"): 1,        ("mW", "W"): 0.001,
    ("A", "A"): 1,        ("mA", "mA"): 1,
}


# ============================================================
# 日志回调系统
# ============================================================

class Logger:
    """统一日志管理，支持回调到 GUI"""
    def __init__(self):
        self._callback = None
        self._warnings = []
        self._errors = []

    def set_callback(self, callback):
        self._callback = callback

    def log(self, msg):
        print(msg)
        if self._callback:
            self._callback(msg)

    def warn(self, msg):
        self._warnings.append(msg)
        self.log(f"  [警告] {msg}")

    def error(self, msg):
        self._errors.append(msg)
        self.log(f"  [错误] {msg}")

    def get_summary(self):
        return {
            "warnings": len(self._warnings),
            "errors": len(self._errors),
            "warning_list": self._warnings,
            "error_list": self._errors,
        }

    def reset(self):
        self._warnings = []
        self._errors = []


logger = Logger()


# ============================================================
# 核心处理函数（openpyxl 只用于读取 BOM 和原数据）
# ============================================================

def parse_value_with_unit(cell_value):
    """从带单位的字符串中提取纯数值和单位"""
    if cell_value is None:
        return None, None
    s = str(cell_value).strip()
    match = re.match(r'^([+-]?\d+\.?\d*(?:[eE][+-]?\d+)?)\s*(\S+)$', s)
    if match:
        return float(match.group(1)), match.group(2)
    try:
        return float(s), None
    except ValueError:
        return None, None


def convert_unit(value, from_unit, to_unit):
    """根据原始单位和目标单位进行转换"""
    if from_unit is None or to_unit is None or value is None:
        return value
    from_unit = from_unit.replace("u", "μ")
    to_unit = to_unit.replace("u", "μ")
    key = (from_unit, to_unit)
    if key in UNIT_CONVERSIONS:
        return value * UNIT_CONVERSIONS[key]
    if from_unit == to_unit:
        return value
    logger.warn(f"未知单位转换: {from_unit} -> {to_unit}，保持原值")
    return value


def extract_param_value(row_data, param_name):
    """根据参数映射规则从原数据行提取参数值"""
    if param_name not in PARAM_MAPPING:
        return None, None
    col_name, method = PARAM_MAPPING[param_name]
    if method == "max_abs":
        max_val, max_unit = parse_value_with_unit(row_data.get("MAX"))
        min_val, min_unit = parse_value_with_unit(row_data.get("MIN"))
        if max_val is not None and min_val is not None:
            # 统一单位后再比较（如 MAX=3A, MIN=-16mA → 统一为A再比较）
            if max_unit and min_unit and max_unit != min_unit:
                # 将min_val转换为max_unit的单位
                min_val = convert_unit(abs(min_val), min_unit, max_unit)
                min_unit = max_unit
            return max(abs(max_val), abs(min_val)), max_unit or min_unit
        elif max_val is not None:
            return abs(max_val), max_unit
        elif min_val is not None:
            return abs(min_val), min_unit
        return None, None
    elif method == "direct":
        return parse_value_with_unit(row_data.get(col_name))
    return None, None


def parse_filename(filename_str):
    """解析 filename，提取状态标识、电压值、电压编号"""
    if not filename_str:
        return None, None, None
    parts = str(filename_str).split("-")
    state_char = None
    voltage_idx = None
    for i, part in enumerate(parts):
        if part.upper() == "W":
            state_char = "W"
            voltage_idx = i + 1
            break
        elif part.upper() == "Q":
            state_char = "Q"
            voltage_idx = i + 1
            break
    voltage_value = None
    voltage_code = None
    if voltage_idx and voltage_idx < len(parts):
        voltage_value = parts[voltage_idx]
        voltage_code = parts[voltage_idx + 1] if voltage_idx + 1 < len(parts) else None
    return state_char, voltage_value, voltage_code


def match_type_to_subtables(component_type, template_type):
    """查找类型所属的子表列表

    对于电流模板，如果 component_type 带 _I 后缀（如 "普通硅二极管(刀刮)_I"），
    先精确匹配，再尝试去掉 _I 后缀匹配（因为 SUBTABLE_CONFIG 中用的是基础名）。
    """
    config = SUBTABLE_CONFIG.get(template_type, {})
    result = [name for name, types in config.items() if component_type in types]
    if not result and template_type == "电流模板" and component_type.endswith("_I"):
        base_type = component_type[:-2]
        result = [name for name, types in config.items() if base_type in types]
    return result


def get_type_params(comp_type, template_type):
    """获取某个类型在指定模板中需要的参数列表"""
    if template_type == "电流模板":
        current_key = f"{comp_type}_I"
        current_mapping = TYPE_PARAM_MAPPING.get(current_key, [])
        if current_mapping:
            return current_mapping
    return TYPE_PARAM_MAPPING.get(comp_type, [])


# ============================================================
# openpyxl 只读：解析 BOM 和原数据
# ============================================================

def load_bom(bom_path):
    """解析 BOM 表（openpyxl 只读）"""
    wb = openpyxl.load_workbook(bom_path, data_only=True)
    ws = wb.active
    entries = []
    for row in range(2, ws.max_row + 1):
        component_type = ws.cell(row=row, column=6).value
        designator = ws.cell(row=row, column=3).value
        model = ws.cell(row=row, column=2).value
        if not component_type or not designator:
            continue
        designators = [d.strip() for d in str(designator).replace("，", ",").split(",") if d.strip()]
        for des in designators:
            entries.append({
                "位号": str(des).strip(),
                "类型": str(component_type).strip(),
                "型号": str(model).strip() if model else "",
            })
    wb.close()
    logger.log(f"[步骤1] BOM解析完成，共 {len(entries)} 个位号")
    for e in entries[:5]:
        logger.log(f"  {e['位号']} -> {e['类型']} -> {e['型号']}")
    if len(entries) > 5:
        logger.log(f"  ... 省略 {len(entries) - 5} 个")
    return entries


def load_raw_data(raw_data_path):
    """加载原数据表（openpyxl 只读），遍历所有Sheet"""
    wb = openpyxl.load_workbook(raw_data_path, data_only=True)
    data_rows = []
    for ws in wb.worksheets:
        headers = {}
        for col in range(1, ws.max_column + 1):
            val = ws.cell(row=1, column=col).value
            if val:
                headers[str(val).strip()] = col
        if not headers:
            continue
        for row in range(2, ws.max_row + 1):
            row_data = {}
            for header, col in headers.items():
                row_data[header] = ws.cell(row=row, column=col).value
            if row_data.get("filename"):
                data_rows.append(row_data)
    wb.close()
    # 打印所有加载的filename，便于排查
    for i, row in enumerate(data_rows):
        fn = row.get("filename", "")
        logger.log(f"    原数据[{i+1}]: {fn}")
    logger.log(f"[步骤2] 原数据加载完成，共 {len(data_rows)} 条记录（来自 {len(wb.worksheets)} 个Sheet）")
    return data_rows


def find_template(template_type):
    """从 ./templates/ 目录自动查找模板文件"""
    if not TEMPLATES_DIR.exists():
        logger.error(f"模板目录不存在: {TEMPLATES_DIR}")
        return None
    keywords = "电压" if "电压" in template_type else "电流"
    for f in TEMPLATES_DIR.iterdir():
        # 跳过临时锁文件（~$开头）和隐藏文件
        if f.name.startswith("~$") or f.name.startswith("."):
            continue
        if f.suffix.lower() in (".xlsx", ".xls") and keywords in f.name:
            logger.log(f"  自动找到{template_type}: {f.name}")
            return str(f)
    logger.error(f"在 {TEMPLATES_DIR} 中未找到{template_type}文件")
    return None


# ============================================================
# win32com 操作模板：真正模拟人工"复制行→插入行"
# ============================================================

def get_excel_app():
    """获取或创建独立的 Excel/WPS COM 应用
    使用 DispatchEx 创建独立进程，不会影响用户正在使用的 WPS/Excel
    """
    import win32com.client

    # 优先尝试 Excel（独立进程）
    try:
        xl = win32com.client.DispatchEx("Excel.Application")
        logger.log("  [引擎] 使用 Microsoft Excel（独立进程）")
        return xl
    except Exception:
        pass

    # 尝试 WPS 表格（独立进程）
    try:
        xl = win32com.client.DispatchEx("ket.Application")
        logger.log("  [引擎] 使用 WPS 表格（独立进程）")
        return xl
    except Exception:
        pass

    logger.error("无法启动 Excel 或 WPS，请确保已安装其中之一")
    raise RuntimeError("无法启动 Excel 或 WPS")


def col_letter_to_num(col_letter):
    """Excel 列字母转数字，如 A->1, Z->26, AA->27"""
    result = 0
    for c in col_letter.upper():
        result = result * 26 + (ord(c) - ord('A') + 1)
    return result


def find_type_row_in_sheet_com(xl_ws, component_type, start_row=1, end_row=500):
    """在 win32com 工作表中 B 列查找类型匹配的行"""
    for row in range(start_row, end_row + 1):
        try:
            val = xl_ws.Cells(row, 2).Value
            if val and str(val).strip() == component_type:
                return row
        except Exception:
            continue
    return None


def get_state_col_ranges(xl_ws, type_row):
    """向上搜索表头行，找到"启动状态"和"稳定状态"的列范围

    返回 dict:
      {"启动": (start_col, end_col), "稳定": (start_col, end_col)}
    如果某个状态不存在则不包含该key
    """
    search_start = 1  # 始终从第1行开始搜索，确保能找到表头
    ranges = {}

    for row in range(type_row, search_start - 1, -1):
        for col in range(1, 30):
            try:
                val = xl_ws.Cells(row, col).Value
                if val and "状态" in str(val):
                    state_name = str(val).strip().replace("状态", "")
                    # 向右找到该状态区域的结束列（下一个"状态"或空列之前）
                    end_col = col
                    for c in range(col + 1, 30):
                        cv = xl_ws.Cells(row, c).Value
                        if cv and "状态" in str(cv):
                            break
                        end_col = c
                    ranges[state_name] = (col, end_col)
            except Exception:
                continue
        # 如果找到了状态标记就停止向上搜索
        if ranges:
            break

    return ranges


def find_param_col_in_sheet_com(xl_ws, type_row, param_name, state=None, max_col=30):
    """在 win32com 工作表中向上搜索参数列号，支持按状态限定列范围

    参数:
      xl_ws:      工作表对象
      type_row:   当前数据行号
      param_name: 参数名（如 "Vmax", "Ipk"）
      state:      状态（"启动"/"稳定"/None），如果指定则只在对应列范围内搜索
      max_col:    最大搜索列数
    """
    search_start = 1  # 始终从第1行开始搜索，确保能找到表头

    # 确定搜索列范围
    if state:
        col_ranges = get_state_col_ranges(xl_ws, type_row)
        if state in col_ranges:
            start_col, end_col = col_ranges[state]
            search_cols = range(start_col, min(end_col + 1, max_col + 1))
        else:
            # 该状态不存在（如电压子表2没有启动），回退到全列搜索
            search_cols = range(1, max_col + 1)
    else:
        search_cols = range(1, max_col + 1)

    for row in range(type_row, search_start - 1, -1):
        for col in search_cols:
            try:
                val = xl_ws.Cells(row, col).Value
                if val and param_name in str(val):
                    return col
            except Exception:
                continue
    return None


def _scan_sheet_headers(xl_ws, max_row=1000, max_col=40):
    """一次性读取工作表所有非空单元格，返回 [(row, col, value_str), ...]。

    表头内容在整个生成过程中不会变化，缓存后避免在热循环里重复走 COM 逐格读取。
    这是报告生成性能的关键：原实现每填一个参数都要重新扫描表头，单次可达
    上千次 COM 调用，导致 120 秒超时。

    注意：必须在插入行之后调用，此时各子表表头所在行号才是最终值。
    优先用 UsedRange.Value 做单次 COM 批量读取（全表），失败时回退到逐格读取。
    """
    cells = []
    try:
        used = xl_ws.UsedRange
        data = used.Value
        if data is not None:
            base_row = used.Row
            base_col = used.Column
            n_rows = used.Rows.Count
            n_cols = used.Columns.Count
            # 归一化为二维元组（单单元格/单行/单列时 .Value 可能返回标量或一维元组）
            if n_rows == 1 and n_cols == 1:
                data = ((data,),)
            elif n_rows == 1:
                data = (tuple(data),)
            elif n_cols == 1:
                data = tuple((v,) for v in data)
            for r_idx, row_vals in enumerate(data, start=base_row):
                if row_vals is None:
                    continue
                if not isinstance(row_vals, tuple):
                    row_vals = (row_vals,)
                for c_idx, val in enumerate(row_vals[:max_col], start=base_col):
                    if val is not None and str(val).strip():
                        cells.append((r_idx, c_idx, str(val).strip()))
            return cells
    except Exception:
        cells = []

    # 兜底：逐格读取
    for row in range(1, max_row + 1):
        for col in range(1, max_col + 1):
            try:
                val = xl_ws.Cells(row, col).Value
            except Exception:
                continue
            if val is not None and str(val).strip():
                cells.append((row, col, str(val).strip()))
    return cells


def _state_ranges_from_headers(cells, type_row, max_col=30):
    """从缓存表头提取某类型行上方最近"状态"表头行的各状态列范围，复刻 get_state_col_ranges 的语义。

    原逻辑：从 type_row 向上找第一个含"状态"的表头行，向右扫描到下一个"状态"或第 max_col 列，
    得到 (start_col, end_col)。这里改为在缓存的纯 Python 列表上计算。
    """
    for row in sorted({r for r, _, _ in cells if r <= type_row}, reverse=True):
        row_cells = {c: v for r, c, v in cells if r == row}
        found = {}
        for col in sorted(c for c in row_cells if c < max_col):
            val = row_cells[col]
            if "状态" in val:
                state_name = val.replace("状态", "")
                end_col = col
                for c in range(col + 1, max_col):
                    cv = row_cells.get(c, "")
                    if cv and "状态" in cv:
                        break
                    end_col = c
                found[state_name] = (col, end_col)
        if found:
            return found
    return {}


def _param_col_from_headers(cells, param_name, state, state_ranges, type_row, max_col=30):
    """从缓存表头查找参数列号，复刻 find_param_col_in_sheet_com 的语义。

    从 type_row 向上搜索；按状态限定列范围；state 为 None 或状态不存在时回退到全列搜索。
    """
    if state and state in state_ranges:
        start_col, end_col = state_ranges[state]
        search_cols = set(range(start_col, min(end_col + 1, max_col + 1)))
    else:
        search_cols = set(range(1, max_col + 1))

    for row in sorted({r for r, _, _ in cells if r <= type_row}, reverse=True):
        for col, val in sorted([(c, v) for r, c, v in cells if r == row]):
            if col in search_cols and param_name in val:
                return col
    return None


def extract_spec_from_model(model, comp_type):
    """从BOM完整型号中智能提取规格参数，用于填入模板C列

    规范：KV→kV, KΩ→kΩ, uF/uf/μf→μF
    示例：
      贴片电容_X7R_2.2nF_1000V_±10％_1206_RoHS_CZSH → 2.2nF_1000V
      贴片电阻_0805_1%_1Ω_1W_RoHS → 1Ω/1W
      MBR10100CT → 600V_2A (无法提取时返回原值)
    """
    if not model:
        return ""

    model = str(model).strip()

    def normalize_unit(s):
        """单位规范化：KV→kV, KΩ→kΩ, uF/uf/μf→μF"""
        s = s.replace(" ", "")
        s = re.sub(r'KV', 'kV', s, flags=re.IGNORECASE)
        s = re.sub(r'KΩ', 'kΩ', s, flags=re.IGNORECASE)
        s = re.sub(r'[Uu][Ff]', 'μF', s)
        s = re.sub(r'μ[fF]', 'μF', s)
        return s

    # 1. 电容类：提取 电容值+电压（如 2.2nF_1000V, 18μF_500V）
    #    F前面必须有p/n/μ/u之一，尝试两种顺序：值_电压 或 电压_值
    cap_pattern = re.search(r'(\d+\.?\d*\s*[pnuμUu][Ff])\s*[_\s]*(\d+\.?\d*\s*[kK]?[Vv])', model)
    if not cap_pattern:
        # 尝试反向：电压在前，电容值在后（如 450V_100μF）
        cap_pattern = re.search(r'(\d+\.?\d*\s*[kK]?[Vv])\s*[_\s]*(\d+\.?\d*\s*[pnuμUu][Ff])', model)
        if cap_pattern:
            volt = normalize_unit(cap_pattern.group(1))
            val = normalize_unit(cap_pattern.group(2))
            return f"{val}_{volt}"
    if cap_pattern:
        val = normalize_unit(cap_pattern.group(1))
        volt = normalize_unit(cap_pattern.group(2))
        return f"{val}_{volt}"

    # 2. 电阻类：提取 电阻值+功率（如 1Ω_1W → 1Ω/1W）
    res_pattern = re.search(r'(\d+\.?\d*\s*[mMkK]?[ΩΩ])\s*[_\s]*(\d+\.?\d*\s*[mM]?[Ww])', model)
    if res_pattern:
        val = normalize_unit(res_pattern.group(1))
        power = normalize_unit(res_pattern.group(2))
        return f"{val}/{power}"

    # 3. 电感类：提取电感值（如 1.5mH）
    #    要求：H前面必须有p/n/μ/u/m之一
    ind_pattern = re.search(r'(\d+\.?\d*\s*[pnuμUumM][Hh])', model)
    if ind_pattern:
        val = normalize_unit(ind_pattern.group(1))
        return val

    # 4. 二极管/保险丝/MOS管类：提取 电压+电流（如 600V_2A 或 6.2A_20V）
    #    要求：V前面必须是数字（排除型号中的字母V）
    vi_pattern = re.search(r'(\d+\.?\d*\s*[kK]?[Vv])\s*[_\s]*(\d+\.?\d*\s*[Aa])', model)
    if vi_pattern:
        val = normalize_unit(vi_pattern.group(1))
        cur = vi_pattern.group(2).replace(" ", "")
        return f"{val}_{cur}"
    # 反向匹配：电流在前电压在后（如 6.2A_20V）
    iv_pattern = re.search(r'(\d+\.?\d*\s*[Aa])\s*[_\s]*(\d+\.?\d*\s*[kK]?[Vv])', model)
    if iv_pattern:
        cur = iv_pattern.group(1).replace(" ", "")
        val = normalize_unit(iv_pattern.group(2))
        return f"{val}_{cur}"

    # 5. 稳压二极管：提取 稳压值+功率（如 3.3V_0.2W）
    vdw_pattern = re.search(r'(\d+\.?\d*\s*[Vv])\s*[_\s]*(\d+\.?\d*\s*[mM]?[Ww])', model)
    if vdw_pattern:
        val = normalize_unit(vdw_pattern.group(1))
        power = normalize_unit(vdw_pattern.group(2))
        return f"{val}_{power}"

    # IC类直接返回型号
    if comp_type in ("Vcc",):
        return model

    # 兜底：返回原始型号
    return model


def process_template_win32com(template_path, bom_entries, raw_data, output_path, template_type):
    """使用 win32com 操作模板，真正模拟人工"复制行→插入行"

    核心逻辑：
    1. 用 Excel COM 打开模板
    2. 统计每个类型需要多少行（BOM 中出现次数）
    3. 对每个类型：如果需要 N 行，就在模板行下方执行 N-1 次"复制整行→插入行"
       Excel 原生处理：格式、公式、合并单元格、行高全部自动正确
    4. 填入位号、型号、测试数据
    5. 保存并关闭
    """
    import win32com.client

    # Excel 常量值
    xlShiftDown = -4121
    xlShiftUp = -4162

    xl = get_excel_app()
    xl.Visible = False
    xl.DisplayAlerts = False

    logger.log(f"[步骤3] 处理{template_type}: {os.path.basename(template_path)}")

    # 打开模板（转为绝对路径，COM 需要）
    template_abs = os.path.abspath(template_path)
    wb = xl.Workbooks.Open(template_abs)

    # 收集所有电压编号
    voltage_codes = set()
    for rd in raw_data:
        _, _, vcode = parse_filename(rd.get("filename", ""))
        if vcode:
            voltage_codes.add(vcode)

    # 多电压时复制Sheet
    if len(voltage_codes) > 1:
        original_ws = wb.Sheets(1)
        sorted_codes = sorted(voltage_codes)
        for vcode in sorted_codes[1:]:
            original_ws.Copy(Before=wb.Sheets(1))
            new_ws = wb.Sheets(1)
            new_ws.Name = str(vcode)
        original_ws.Name = str(sorted_codes[0])
        logger.log(f"  检测到 {len(voltage_codes)} 个电压编号: {sorted_codes}，已复制Sheet")

    stats = {"filled": 0, "skipped": 0, "rows_added": 0}

    for sheet_idx in range(1, wb.Sheets.Count + 1):
        xl_ws = wb.Sheets(sheet_idx)
        sheet_name = xl_ws.Name
        logger.log(f"\n  处理Sheet: {sheet_name}")
        current_voltage_code = sheet_name

        # ===== 阶段1：统计每个类型需要插入的行数 =====
        type_count = {}
        for entry in bom_entries:
            comp_type = entry["类型"]
            matched_subtables = match_type_to_subtables(comp_type, template_type)
            if matched_subtables:
                type_count[comp_type] = type_count.get(comp_type, 0) + 1

        # ===== 阶段2：从下往上执行"复制行→插入行" =====
        # 找到每个类型在模板中的行号
        type_row_map = {}  # comp_type -> row_number
        for comp_type in type_count:
            row = find_type_row_in_sheet_com(xl_ws, comp_type, start_row=1, end_row=200)
            if row:
                type_row_map[comp_type] = row

        # 按行号从大到小排序，从下往上插入，避免行号偏移
        sorted_types = sorted(type_row_map.items(), key=lambda x: x[1], reverse=True)

        for comp_type, type_row in sorted_types:
            count = type_count[comp_type]
            if count <= 1:
                continue
            needed = count - 1  # 需要额外插入的行数

            logger.log(f"  [插入] '{comp_type}': 在第{type_row}行下方插入 {needed} 行")

            # 模拟人工操作：选中整行 → 复制 → 在下方插入复制的行
            # 注意：Insert 操作本身就会粘贴剪贴板内容，无需额外 PasteSpecial
            for i in range(needed):
                xl_ws.Rows(type_row).Copy()
                xl_ws.Rows(type_row + 1).Insert(xlShiftDown)

            stats["rows_added"] += needed

        # 缓存表头（插入行之后扫描，此时各子表表头行号才是最终值；热循环里复用避免重复 COM 调用导致超时）
        header_cells = _scan_sheet_headers(xl_ws)

        # ===== 阶段3：填充数据 =====
        # 重新查找类型行（因为插入后行号可能变化，但类型名不变）
        # 注意：由于从下往上插入，上面的类型行号不受影响
        # 但同一类型插入了多行，需要重新定位
        type_fill_counter = {}

        for entry in bom_entries:
            comp_type = entry["类型"]
            designator = entry["位号"]
            model = entry["型号"]

            matched_subtables = match_type_to_subtables(comp_type, template_type)
            if not matched_subtables:
                stats["skipped"] += 1
                logger.log(f"  [跳过] {designator}/{comp_type}: 不属于{template_type}的任何子表")
                continue

            needed_params = get_type_params(comp_type, template_type)
            if not needed_params:
                logger.warn(f"  [无参数] {designator}/{comp_type}: get_type_params返回空")
                continue

            # 找到该类型在当前工作表中的行号
            # 第一次查找用全表搜索，后续用 type_row + offset
            if comp_type not in type_fill_counter:
                type_row = find_type_row_in_sheet_com(xl_ws, comp_type, start_row=1, end_row=500)
                if type_row is None:
                    # 调试：打印B列所有值帮助排查（可能含隐藏字符/换行）
                    b_vals = []
                    for r in range(1, min(xl_ws.UsedRange.Rows.Count + 1, 100)):
                        try:
                            v = xl_ws.Cells(r, 2).Value
                            if v:
                                b_vals.append(f"行{r}='{repr(str(v))}'")
                        except Exception:
                            pass
                    logger.warn(f"类型 '{comp_type}' 在模板中未找到对应行, B列值: {', '.join(b_vals[:15])}")
                    continue
                type_fill_counter[comp_type] = {"base_row": type_row, "count": 0}

            info = type_fill_counter[comp_type]
            current_row = info["base_row"] + info["count"]
            info["count"] += 1

            # 该行所属子表的表头状态列范围：从当前行向上搜索最近的"状态"表头行，
            # 避免多个子表表头互相串扰（此前缓存整个Sheet导致取到最后一个子表的列布局）
            header_state_ranges = _state_ranges_from_headers(header_cells, current_row)

            # 填入位号（A列）和规格参数（C列，从BOM型号智能提取）
            spec = extract_spec_from_model(model, comp_type)
            xl_ws.Cells(current_row, 1).Value = designator
            xl_ws.Cells(current_row, 3).Value = spec

            # 从原数据中查找匹配记录并填入参数
            matched_files = []
            for rd in raw_data:
                filename = str(rd.get("filename", ""))
                state_char, voltage_value, voltage_code = parse_filename(filename)
                state_name = STATE_MAPPING.get(state_char, "") if state_char else ""

                # 电压编号匹配
                if len(voltage_codes) > 1 and str(voltage_code) != str(current_voltage_code):
                    continue

                # 位号匹配
                if designator and designator not in filename:
                    continue

                # 同一位号多种类型时（如Q1同时有Vds/Vgs/Id），用filename中的缩写区分
                # filename格式1：位号-缩写-状态-类型-编号（如 Q1-DS-W-V-1，缩写在第二段）
                # filename格式2：位号-状态-测量类型-编号（如 D4-Q-I-1，无缩写，I/V在第三段）
                #   I = 电流通道数据 → 只匹配电流类型（Id, Ic, 普通硅二极管_I 等）
                #   V = 电压通道数据 → 只匹配电压类型（Vds, Vgs, 普通硅二极管 等）
                if comp_type in FILENAME_ABBR_TYPE_MAPPING.values():
                    parts = filename.split("-")
                    if len(parts) >= 2:
                        abbr = parts[1].upper()
                        expected_type = FILENAME_ABBR_TYPE_MAPPING.get(abbr)
                        if expected_type and expected_type != comp_type:
                            continue
                # filename中的 -I-/-V- 测量类型过滤（适用于所有类型，不限于缩写映射的）
                upper_fn = filename.upper()
                has_i_marker = any(f"-{ch}-" in upper_fn for ch in ("I", "A"))
                has_v_marker = "-V-" in upper_fn
                if template_type == "电压模板":
                    # 电压模板：跳过 -I-/-A- 标记的电流数据
                    if has_i_marker:
                        logger.log(f"  [类型过滤] {filename}: -I-/-A- 标记为电流数据，电压模板跳过")
                        continue
                    # -V- 标记的电压数据直接匹配，不做过滤
                else:
                    # 电流模板：跳过 -V- 标记的电压数据
                    if has_v_marker:
                        logger.log(f"  [类型过滤] {filename}: -V- 标记为电压数据，电流模板跳过")
                        continue
                    # -I-/-A- 标记的电流数据直接匹配，不做过滤

                matched_files.append(filename)

                # 提取并填入每个参数
                for param_info, target_unit in needed_params:
                    param_state = None
                    param_name = param_info

                    # 从参数名中提取状态前缀（如 "稳定Vmax" → state="稳定", name="Vmax"）
                    param_state = None
                    param_name = param_info
                    for state_key, state_val in STATE_MAPPING.items():
                        if param_info.startswith(state_val):
                            param_state = state_val
                            param_name = param_info[len(state_val):]
                            break

                    if param_state and state_name != param_state:
                        continue

                    raw_value, raw_unit = extract_param_value(rd, param_name)
                    if raw_value is None:
                        continue

                    if raw_unit and target_unit:
                        converted_value = convert_unit(raw_value, raw_unit, target_unit)
                    else:
                        converted_value = raw_value

                    # 在模板中查找参数列（按状态限定列范围，从当前行向上搜索，用缓存避免重复 COM 调用）
                    param_col = _param_col_from_headers(header_cells, param_name, param_state, header_state_ranges, current_row)
                    if param_col:
                        cell = xl_ws.Cells(current_row, param_col)
                        fval = float(converted_value)
                        if fval == int(fval):
                            cell.NumberFormat = "0"
                        else:
                            cell.NumberFormat = "0.###############"
                        cell.Value = fval
                        stats["filled"] += 1
                        logger.log(f"  [填入] {designator}/{comp_type}: {param_info}={converted_value} → 行{current_row}列{param_col} (来自{filename})")
                    else:
                        logger.warn(f"  [未找到列] {designator}/{comp_type}: {param_name}(状态={param_state}) 在行{current_row}上方未匹配到列头")

            if not matched_files and needed_params:
                logger.warn(f"  [未匹配] {designator}/{comp_type}: 在原数据中未找到匹配的filename")

    # 清理：只保留 BOM 中实际出现的类型行，删除模板示例中多余的类型行
    # 收集 BOM 中实际存在的类型集合
    bom_types = set()
    for entry in bom_entries:
        matched = match_type_to_subtables(entry["类型"], template_type)
        if matched:
            bom_types.add(entry["类型"])

    # 表头关键字：包含这些字的行是子表表头，必须保留
    header_keywords = {"位号", "类型", "型号"}

    for sheet_idx in range(1, wb.Sheets.Count + 1):
        xl_ws = wb.Sheets(sheet_idx)
        # 收集所有B列有值的行，从下往上检查
        type_rows = []
        for row in range(1, 300):
            try:
                val = xl_ws.Cells(row, 2).Value
                if val and str(val).strip():
                    type_rows.append((row, str(val).strip()))
            except Exception:
                continue

        for type_row, type_name in reversed(type_rows):
            try:
                # 检查是否为表头行（A/B/C列包含"位号"/"类型"/"型号"）
                is_header = False
                for col in range(1, 4):
                    cell_val = xl_ws.Cells(type_row, col).Value
                    if cell_val and str(cell_val).strip() in header_keywords:
                        is_header = True
                        break
                if is_header:
                    continue  # 表头行，跳过不删除

                a_val = xl_ws.Cells(type_row, 1).Value
                # 删除条件：BOM中没有该类型，或者该行没有填入位号（A列为空）
                a_empty = not a_val or not str(a_val).strip()
                if type_name not in bom_types or a_empty:
                    try:
                        xl_ws.Rows(type_row).Delete(xlShiftUp)
                        logger.log(f"  [清理] 删除多余类型行: {type_name} (行{type_row})")
                    except Exception as e:
                        # 合并单元格等可能导致删除失败，跳过
                        logger.warn(f"  [清理] 跳过行{type_row} ({type_name}): {str(e)[:50]}")
            except Exception:
                pass

    # 保存
    output_abs = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(output_abs), exist_ok=True)

    # 如果输出文件已存在，先删除（COM SaveAs 不能覆盖）
    if os.path.exists(output_abs):
        try:
            os.remove(output_abs)
        except Exception:
            pass

    wb.SaveAs(output_abs)
    wb.Close(SaveChanges=False)

    logger.log(f"[完成] 报告已保存: {output_path}")
    logger.log(f"  填入参数: {stats['filled']} 个, 新增行: {stats['rows_added']} 行, 跳过: {stats['skipped']} 个")
    return stats, xl


# ============================================================
# 对外 API：只需 BOM + 原数据，自动完成所有步骤
# ============================================================

def generate_reports(bom_path, raw_data_path, output_dir=None):
    """核心 API：根据 BOM 和原数据自动生成报告

    参数:
        bom_path:      BOM表路径 (.xlsx)
        raw_data_path: 原数据表路径 (.xlsx)
        output_dir:    输出目录 (默认为脚本所在目录下的 output/)

    返回:
        dict: {"success": bool, "voltage_report": str, "current_report": str, "summary": dict}
    """
    # 默认输出目录
    if not output_dir:
        output_dir = str(SCRIPT_DIR / "output")

    logger.reset()
    logger.log("=" * 60)
    logger.log("  电子元件试验需求自动化报告生成器 v4.0 (win32com)")
    logger.log(f"  执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.log("=" * 60)

    # 验证输入文件
    if not os.path.exists(bom_path):
        logger.error(f"BOM表文件不存在: {bom_path}")
        return {"success": False, "error": f"BOM表文件不存在: {bom_path}"}
    if not os.path.exists(raw_data_path):
        logger.error(f"原数据表文件不存在: {raw_data_path}")
        return {"success": False, "error": f"原数据表文件不存在: {raw_data_path}"}

    os.makedirs(output_dir, exist_ok=True)

    # 步骤1: 解析BOM（openpyxl 只读）
    logger.log("")
    bom_entries = load_bom(bom_path)
    if not bom_entries:
        logger.error("BOM表为空或格式不正确")
        return {"success": False, "error": "BOM表为空或格式不正确"}

    # 步骤2: 加载原数据（openpyxl 只读）
    logger.log("")
    raw_data = load_raw_data(raw_data_path)
    if not raw_data:
        logger.error("原数据表为空或格式不正确")
        return {"success": False, "error": "原数据表为空或格式不正确"}

    # 步骤3: 自动查找模板
    logger.log("")
    logger.log("[内置配置] 对应关系表: 已加载（硬编码）")
    logger.log(f"[内置配置] 模板目录: {TEMPLATES_DIR}")
    logger.log("")

    voltage_template = find_template("电压模板")
    current_template = find_template("电流模板")

    voltage_output = None
    current_output = None

    xl = None
    try:
        if voltage_template:
            voltage_output = os.path.join(output_dir, "电压模板_报告.xlsx")
            _, xl = process_template_win32com(voltage_template, bom_entries, raw_data, voltage_output, "电压模板")

        if current_template:
            current_output = os.path.join(output_dir, "电流模板_报告.xlsx")
            _, xl = process_template_win32com(current_template, bom_entries, raw_data, current_output, "电流模板")
    finally:
        # 关闭脚本创建的独立 Excel/WPS 进程（不影响用户自己的）
        if xl:
            try:
                xl.Quit()
            except Exception:
                pass

    # 汇总
    summary = logger.get_summary()
    logger.log("")
    logger.log("=" * 60)
    logger.log("  所有报告生成完成！")
    if voltage_output:
        logger.log(f"  电压模板报告: {voltage_output}")
    if current_output:
        logger.log(f"  电流模板报告: {current_output}")
    if summary["warnings"] > 0:
        logger.log(f"  警告: {summary['warnings']} 个")
    if summary["errors"] > 0:
        logger.log(f"  错误: {summary['errors']} 个")
    logger.log("=" * 60)

    success = summary["errors"] == 0
    return {
        "success": success,
        "voltage_report": voltage_output,
        "current_report": current_output,
        "summary": summary,
    }


# ============================================================
# GUI 界面
# ============================================================

def launch_gui():
    """启动图形用户界面"""
    try:
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox, scrolledtext
    except ImportError:
        print("tkinter 不可用，请使用命令行模式")
        print("用法: python auto_report_generator.py --cli <BOM> <原数据> [输出目录]")
        return

    class ReportGeneratorApp:
        def __init__(self, root):
            self.root = root
            self.root.title("电子元件试验需求自动化报告生成器 v4.0 (win32com)")
            self.root.geometry("750x620")
            self.root.minsize(650, 550)

            self.file_vars = {
                "BOM表": tk.StringVar(),
                "原数据表": tk.StringVar(),
            }
            self.output_dir = tk.StringVar(value=str(SCRIPT_DIR / "output"))
            self.is_running = False

            self.templates_ok = TEMPLATES_DIR.exists() and list(TEMPLATES_DIR.glob("*.xls*"))

            self._build_ui()

        def _build_ui(self):
            style = ttk.Style()
            style.configure("Title.TLabel", font=("Microsoft YaHei", 14, "bold"))
            style.configure("Section.TLabel", font=("Microsoft YaHei", 10, "bold"))
            style.configure("Status.TLabel", font=("Microsoft YaHei", 9))

            main_frame = ttk.Frame(self.root, padding=15)
            main_frame.pack(fill=tk.BOTH, expand=True)

            ttk.Label(main_frame, text="电子元件试验需求自动化报告生成器",
                      style="Title.TLabel").pack(anchor=tk.W, pady=(0, 10))

            config_frame = ttk.LabelFrame(main_frame, text=" 内置配置（无需上传） ", padding=8)
            config_frame.pack(fill=tk.X, pady=(0, 10))
            config_items = [
                ("对应关系表", "已内置（硬编码）", "#009900"),
                ("电压/电流模板", f"从 {TEMPLATES_DIR} 自动加载", "#009900" if self.templates_ok else "#CC0000"),
                ("操作引擎", "win32com（模拟人工操作）", "#009900"),
            ]
            for name, status, color in config_items:
                row = ttk.Frame(config_frame)
                row.pack(fill=tk.X, pady=1)
                ttk.Label(row, text=f"  {name}:", width=14, style="Section.TLabel").pack(side=tk.LEFT)
                ttk.Label(row, text=status, foreground=color).pack(side=tk.LEFT)
            if not self.templates_ok:
                ttk.Label(config_frame,
                          text=f"  请创建 {TEMPLATES_DIR} 目录并放入模板文件",
                          foreground="#CC0000").pack(anchor=tk.W, pady=(3, 0))

            input_frame = ttk.LabelFrame(main_frame, text=" 输入文件（用户上传） ", padding=10)
            input_frame.pack(fill=tk.X, pady=(0, 10))

            file_descriptions = {
                "BOM表": "BOM表（含位号、型号、类型）",
                "原数据表": "原数据表（测试数据）",
            }

            for name, desc in file_descriptions.items():
                row_frame = ttk.Frame(input_frame)
                row_frame.pack(fill=tk.X, pady=3)
                ttk.Label(row_frame, text=f"{name}:", width=10,
                          style="Section.TLabel").pack(side=tk.LEFT)
                ttk.Entry(row_frame, textvariable=self.file_vars[name],
                          width=55, font=("Consolas", 9)).pack(
                    side=tk.LEFT, padx=(5, 5), fill=tk.X, expand=True)
                ttk.Button(row_frame, text="浏览...",
                           command=lambda n=name: self._browse_file(n)).pack(side=tk.RIGHT)
                desc_row = ttk.Frame(input_frame)
                desc_row.pack(fill=tk.X)
                ttk.Label(desc_row, text="", width=10).pack(side=tk.LEFT)
                ttk.Label(desc_row, text=desc, foreground="#888888").pack(side=tk.LEFT, padx=(5, 0))

            output_frame = ttk.LabelFrame(main_frame, text=" 输出目录 ", padding=10)
            output_frame.pack(fill=tk.X, pady=(0, 10))
            out_row = ttk.Frame(output_frame)
            out_row.pack(fill=tk.X)
            ttk.Label(out_row, text="输出路径:", width=10, style="Section.TLabel").pack(side=tk.LEFT)
            ttk.Entry(out_row, textvariable=self.output_dir,
                      width=55, font=("Consolas", 9)).pack(
                side=tk.LEFT, padx=(5, 5), fill=tk.X, expand=True)
            ttk.Button(out_row, text="浏览...",
                       command=self._browse_output_dir).pack(side=tk.RIGHT)

            btn_frame = ttk.Frame(main_frame)
            btn_frame.pack(fill=tk.X, pady=(0, 8))
            self.generate_btn = ttk.Button(btn_frame, text="一键生成报告",
                                           command=self._generate)
            self.generate_btn.pack(side=tk.LEFT, padx=(0, 10))
            self.open_output_btn = ttk.Button(btn_frame, text="打开输出目录",
                                              command=self._open_output, state=tk.DISABLED)
            self.open_output_btn.pack(side=tk.LEFT)

            self.progress = ttk.Progressbar(main_frame, mode="indeterminate")
            self.progress.pack(fill=tk.X, pady=(0, 5))
            self.status_label = ttk.Label(main_frame, text="就绪 - 请选择 BOM 和原数据后点击生成",
                                          style="Status.TLabel", foreground="#0066cc")
            self.status_label.pack(anchor=tk.W)

            log_frame = ttk.LabelFrame(main_frame, text=" 运行日志 ", padding=5)
            log_frame.pack(fill=tk.BOTH, expand=True, pady=(5, 0))
            self.log_text = scrolledtext.ScrolledText(
                log_frame, height=10, font=("Consolas", 9),
                bg="#1e1e1e", fg="#d4d4d4", insertbackground="white",
                selectbackground="#264f78", wrap=tk.WORD
            )
            self.log_text.pack(fill=tk.BOTH, expand=True)
            self.log_text.tag_configure("warning", foreground="#dcdcaa")
            self.log_text.tag_configure("error", foreground="#f44747")
            self.log_text.tag_configure("success", foreground="#4ec9b0")

        def _browse_file(self, name):
            filetypes = [("Excel 文件", "*.xlsx *.xls"), ("所有文件", "*.*")]
            path = filedialog.askopenfilename(title=f"选择{name}", filetypes=filetypes)
            if path:
                self.file_vars[name].set(path)

        def _browse_output_dir(self):
            path = filedialog.askdirectory(title="选择输出目录")
            if path:
                self.output_dir.set(path)

        def _append_log(self, msg):
            def _do_append():
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
                if "[警告]" in msg:
                    start, end = self.log_text.index("end-2l linestart"), self.log_text.index("end-1l lineend")
                    self.log_text.tag_add("warning", start, end)
                elif "[错误]" in msg:
                    start, end = self.log_text.index("end-2l linestart"), self.log_text.index("end-1l lineend")
                    self.log_text.tag_add("error", start, end)
                elif "[完成]" in msg or "生成完成" in msg:
                    start, end = self.log_text.index("end-2l linestart"), self.log_text.index("end-1l lineend")
                    self.log_text.tag_add("success", start, end)
            self.root.after(0, _do_append)

        def _generate(self):
            if self.is_running:
                return
            missing = [name for name, var in self.file_vars.items() if not var.get().strip()]
            if missing:
                messagebox.showwarning("文件未选择",
                                       f"请先选择以下文件：\n- " + "\n- ".join(missing))
                return
            self.log_text.delete("1.0", tk.END)
            self.is_running = True
            self.generate_btn.config(state=tk.DISABLED)
            self.open_output_btn.config(state=tk.DISABLED)
            self.progress.start(10)
            self.status_label.config(text="正在生成报告，请稍候...", foreground="#cc6600")
            logger.set_callback(self._append_log)
            threading.Thread(target=self._run_generation, daemon=True).start()

        def _run_generation(self):
            try:
                result = generate_reports(
                    bom_path=self.file_vars["BOM表"].get().strip(),
                    raw_data_path=self.file_vars["原数据表"].get().strip(),
                    output_dir=self.output_dir.get().strip(),
                )
                def _on_complete():
                    self.progress.stop()
                    self.is_running = False
                    self.generate_btn.config(state=tk.NORMAL)
                    self.open_output_btn.config(state=tk.NORMAL)
                    if result["success"]:
                        self.status_label.config(text="报告生成成功！", foreground="#009900")
                    else:
                        self.status_label.config(text="生成出错，请查看日志", foreground="#cc0000")
                self.root.after(0, _on_complete)
            except Exception as e:
                logger.error(f"生成失败: {str(e)}")
                def _on_error():
                    self.progress.stop()
                    self.is_running = False
                    self.generate_btn.config(state=tk.NORMAL)
                    self.status_label.config(text=f"生成失败: {str(e)}", foreground="#cc0000")
                self.root.after(0, _on_error)

        def _open_output(self):
            output = self.output_dir.get().strip()
            if os.path.exists(output):
                if sys.platform == "win32":
                    os.startfile(output)
                elif sys.platform == "darwin":
                    import subprocess
                    subprocess.Popen(["open", output])
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", output])

    root = tk.Tk()
    ReportGeneratorApp(root)
    root.mainloop()


# ============================================================
# 命令行入口
# ============================================================

def main():
    if len(sys.argv) >= 2 and sys.argv[1] == "--cli":
        if len(sys.argv) < 4:
            print("命令行用法:")
            print("  python auto_report_generator.py --cli <BOM> <原数据> [输出目录]")
            print()
            print("示例:")
            print("  python auto_report_generator.py --cli bom.xlsx data.xlsx ./output")
            print()
            print("说明:")
            print("  v4.0 使用 win32com 操作模板，真正模拟人工复制插入行")
            print("  对应关系表已内置，电压/电流模板从 ./templates/ 自动加载")
            sys.exit(1)

        result = generate_reports(
            bom_path=sys.argv[2],
            raw_data_path=sys.argv[3],
            output_dir=sys.argv[4] if len(sys.argv) >= 5 else None,
        )
        sys.exit(0 if result["success"] else 1)
    else:
        launch_gui()


if __name__ == "__main__":
    main()
