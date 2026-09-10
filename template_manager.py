"""
模板管理器
- 扫描 templates/ 目录下的所有 xlsx 模板
- 自动生成/管理 templates.json 元数据
- 支持 Agent 理解模板结构
"""
import os
import json
import glob
from pathlib import Path
from openpyxl import load_workbook
from datetime import datetime


# 模板目录（功率分析仪报告模板，避免与示波器 VI 模板冲突）
TEMPLATE_DIR = Path(__file__).parent / "power_templates"
TEMPLATES_JSON = TEMPLATE_DIR / "templates.json"

# 数据库字段定义（供 Agent 参考）
DB_FIELDS = {
    "ch1_urms": {"name": "CH1电压有效值", "unit": "V", "default": "input"},
    "ch1_irms": {"name": "CH1电流有效值", "unit": "A", "default": "input"},
    "ch1_p": {"name": "CH1有功功率", "unit": "W", "default": "input"},
    "ch1_lamb": {"name": "CH1功率因数", "unit": "", "default": "input"},
    "ch1_ithd": {"name": "CH1总谐波失真", "unit": "%", "default": "input"},
    "ch1_ik3": {"name": "CH1 3次谐波", "unit": "%", "default": "input"},
    "ch1_ik5": {"name": "CH1 5次谐波", "unit": "%", "default": "input"},
    "ch1_ik7": {"name": "CH1 7次谐波", "unit": "%", "default": "input"},
    "ch1_ik9": {"name": "CH1 9次谐波", "unit": "%", "default": "input"},
    "ch2_urms": {"name": "CH2电压有效值", "unit": "V", "default": "output"},
    "ch2_irms": {"name": "CH2电流有效值", "unit": "A", "default": "output"},
    "ch2_p": {"name": "CH2有功功率", "unit": "W", "default": "output"},
    # ... 其他通道类似
}


def ensure_template_dir():
    """确保模板目录存在"""
    TEMPLATE_DIR.mkdir(exist_ok=True)
    return TEMPLATE_DIR


def scan_templates():
    """扫描模板目录，返回所有 xlsx 文件"""
    if not TEMPLATE_DIR.exists():
        return []
    
    templates = []
    for xlsx_file in glob.glob(str(TEMPLATE_DIR / "*.xlsx")):
        rel_path = Path(xlsx_file).relative_to(TEMPLATE_DIR)
        templates.append({
            "file": str(rel_path),
            "name": rel_path.stem,  # 文件名（不含扩展名）
            "size": os.path.getsize(xlsx_file),
            "modified": datetime.fromtimestamp(os.path.getmtime(xlsx_file)).isoformat()
        })
    return sorted(templates, key=lambda x: x["name"])


def read_template_cells(file_path, max_rows=20, max_cols=20):
    """读取模板单元格内容，用于理解模板结构
    处理合并单元格：标签在合并区域，数据填在下一行
    """
    wb = load_workbook(file_path, data_only=True)
    cells = {}
    
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        sheet_cells = []
        
        # 记录合并单元格信息
        merged_ranges = []
        for merged_range in ws.merged_cells.ranges:
            merged_ranges.append({
                "min_row": merged_range.min_row,
                "max_row": merged_range.max_row,
                "min_col": merged_range.min_col,
                "max_col": merged_range.max_col,
                "start_cell": f"{chr(64+merged_range.min_col)}{merged_range.min_row}"
            })
        
        # 读取单元格内容
        for row in range(1, min(max_rows + 1, ws.max_row + 1)):
            for col in range(1, min(max_cols + 1, ws.max_column + 1)):
                cell = ws.cell(row=row, column=col)
                if cell.value is not None:
                    cell_ref = f"{chr(64+col)}{row}"
                    data_row = row  # 数据所在行（默认同行）
                    
                    # 检查是否在合并单元格中
                    is_merged = False
                    merged_info = None
                    for mr in merged_ranges:
                        if (mr["min_row"] <= row <= mr["max_row"] and 
                            mr["min_col"] <= col <= mr["max_col"]):
                            # 只在合并区域的起始单元格记录标签
                            if row == mr["min_row"] and col == mr["min_col"]:
                                cell_ref = mr["start_cell"]
                                merged_info = mr
                                is_merged = True
                            else:
                                # 合并区域内的其他单元格，跳过
                                is_merged = "skip"
                            break
                    
                    if is_merged == "skip":
                        continue
                    
                    # 数据单元格位置：合并区域正下方的单元格（保持同列）
                    if merged_info:
                        data_row = merged_info["max_row"] + 1
                        data_col = merged_info["min_col"]  # 使用合并区域的起始列
                    else:
                        data_row = row
                        data_col = col
                    data_cell = f"{chr(64+data_col)}{data_row}"
                    
                    # 去重：如果已经记录过这个单元格，跳过
                    if any(c["cell"] == cell_ref for c in sheet_cells):
                        continue
                    
                    sheet_cells.append({
                        "cell": cell_ref,
                        "value": str(cell.value)[:100],
                        "is_merged": is_merged,
                        "data_cell": data_cell  # 数据应该填入的位置
                    })
        
        if sheet_cells:
            cells[sheet_name] = sheet_cells
    
    wb.close()
    return cells


def generate_template_metadata(templates):
    """为每个模板生成元数据"""
    metadata = {
        "version": "1.0",
        "updated": datetime.now().isoformat(),
        "db_fields": DB_FIELDS,
        "templates": []
    }
    
    for t in templates:
        file_path = TEMPLATE_DIR / t["file"]
        cells = read_template_cells(file_path)
        
        template_info = {
            "id": Path(t["file"]).stem,  # 用文件名作为 ID
            "name": t["name"],
            "file": t["file"],
            "size": t["size"],
            "modified": t["modified"],
            "sheets": {},
            "field_mapping": {}  # 待 Agent 填充
        }
        
        # 读取每个 sheet 的基本信息
        wb = load_workbook(file_path)
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            template_info["sheets"][sheet_name] = {
                "rows": ws.max_row,
                "cols": ws.max_column,
                "cells": cells.get(sheet_name, [])[:50]  # 只保留前50个非空单元格
            }
        wb.close()
        
        metadata["templates"].append(template_info)
    
    return metadata


def save_metadata(metadata):
    """保存元数据到 JSON 文件"""
    with open(TEMPLATES_JSON, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def load_metadata():
    """加载元数据"""
    if not TEMPLATES_JSON.exists():
        return None
    with open(TEMPLATES_JSON, 'r', encoding='utf-8') as f:
        return json.load(f)


def refresh_templates():
    """扫描并更新所有模板元数据"""
    ensure_template_dir()
    templates = scan_templates()
    metadata = generate_template_metadata(templates)
    save_metadata(metadata)
    return metadata


def get_template_for_agent(template_id=None):
    """获取模板信息，供 Agent 使用"""
    metadata = load_metadata()
    if not metadata:
        return None
    
    if template_id:
        for t in metadata["templates"]:
            if t["id"] == template_id:
                return {
                    "db_fields": metadata["db_fields"],
                    "template": t
                }
    else:
        return {
            "db_fields": metadata["db_fields"],
            "templates": metadata["templates"]
        }


# ============================================================
# 模板填充器
# ============================================================

def fill_template(template_id, field_mapping, db_data, output_path):
    """根据 field_mapping 填充模板"""
    metadata = load_metadata()
    if not metadata:
        raise ValueError("模板元数据不存在，请先运行 refresh_templates()")
    
    # 找到模板文件
    template_file = None
    for t in metadata["templates"]:
        if t["id"] == template_id:
            template_file = TEMPLATE_DIR / t["file"]
            break
    
    if not template_file or not template_file.exists():
        raise ValueError(f"模板文件不存在: {template_id}")
    
    # 加载模板
    wb = load_workbook(template_file)
    
    for cell_ref, rule in field_mapping.items():
        if not cell_ref:
            continue
        
        # 解析单元格引用（如 "Sheet1!B5" 或 "B5"）
        if "!" in cell_ref:
            sheet_name, cell = cell_ref.split("!")
            ws = wb[sheet_name]
        else:
            ws = wb.active
            cell = cell_ref
        
        # 填充数据
        if "column" in rule:
            col_name = rule["column"]
            value = db_data.get(col_name)
            if value is not None:
                ws[cell] = value
        elif "formula" in rule:
            ws[cell] = f"={rule['formula']}"
    
    # 保存
    wb.save(output_path)
    wb.close()
    return output_path


# ============================================================
# CLI 命令
# ============================================================

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "refresh":
        print("正在扫描模板...")
        metadata = refresh_templates()
        print(f"✅ 已扫描 {len(metadata['templates'])} 个模板")
        print(f"✅ 元数据已保存到: {TEMPLATES_JSON}")
    elif len(sys.argv) > 1 and sys.argv[1] == "list":
        metadata = load_metadata()
        if metadata:
            print(f"\n📋 模板列表 ({len(metadata['templates'])} 个)\n")
            for t in metadata["templates"]:
                print(f"  • {t['id']}: {t['name']}")
                print(f"    文件: {t['file']}")
                print(f"    包含: {list(t['sheets'].keys())}")
                print()
        else:
            print("❌ 元数据文件不存在，请先运行 python template_manager.py refresh")
    else:
        print("用法:")
        print("  python template_manager.py refresh  # 扫描并更新所有模板元数据")
        print("  python template_manager.py list     # 列出所有模板")
