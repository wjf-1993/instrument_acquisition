#!/usr/bin/env python3
# fix_json_parsing.py - 修复 JSON 解析失败问题
import json
import re

def extract_json_from_response(response):
    code_block_pattern = r'```json\s*(.*?)\s*```'
    code_block_match = re.search(code_block_pattern, response, re.DOTALL)
    if code_block_match:
        json_str = code_block_match.group(1)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
    def find_balanced_braces(s):
        start = s.find('{')
        if start == -1:
            return None
        count = 1
        end = start + 1
        while end < len(s) and count > 0:
            if s[end] == '{':
                count += 1
            elif s[end] == '}':
                count -= 1
            end += 1
        if count == 0:
            return s[start:end]
        return None
    json_str = find_balanced_braces(response)
    if json_str:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            json_str = fix_json_format(json_str)
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass
    return None

def fix_json_format(json_str):
    json_str = re.sub(r',\s*}', '}', json_str)
    json_str = re.sub(r',\s*\]', ']', json_str)
    json_str = re.sub(r'(\w+):', r'"\1":', json_str)
    json_str = re.sub(r':\s*([^"\{\[\d\.\-\+eE\s]+)(?=,|\}|\])', r': "\1"', json_str)
    return json_str

def parse_measurement_data(json_data):
    if not json_data:
        return None
    result = {}
    for key, value in json_data.items():
        if isinstance(value, dict) and '数值' in value and '单位' in value:
            num = value['数值']
            unit = value['单位']
            if num:
                result[key] = f"{num} {unit}"
            else:
                result[key] = f"/"
        else:
            result[key] = f"/"
    return result