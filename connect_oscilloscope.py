#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# connect_oscilloscope.py - 连接DLM5000示波器

import time
import pyvisa as visa


def connect_dlm5000(ip_address, user='anonymous', password=''):
    """连接DLM5000示波器"""
    print(f"正在连接 DLM5000 示波器...")
    print(f"IP地址: {ip_address}")
    try:
        rm = visa.ResourceManager('@py')
        resource_str = f"TCPIP0::{ip_address}::INSTR"
        instrument = rm.open_resource(resource_str)
        instrument.timeout = 10000
        instrument.clear()
        idn = instrument.query('*IDN?')
        print(f"连接成功！")
        print(f"仪器信息: {idn.strip()}")
        return instrument
    except Exception as e:
        print(f"连接失败: {e}")
        return None


def test_communication(instrument):
    """测试通信"""
    if instrument is None:
        return False
    try:
        idn = instrument.query('*IDN?')
        print(f"仪器ID: {idn.strip()}")
        status = instrument.query('*STB?')
        print(f"系统状态: {status.strip()}")
        error = instrument.query('SYST:ERR?')
        print(f"错误信息: {error.strip()}")
        return True
    except Exception as e:
        print(f"通信测试失败: {e}")
        return False


def main():
    ip_address = '172.17.76.9'
    instrument = connect_dlm5000(ip_address)
    if instrument:
        test_communication(instrument)
        instrument.close()
        print("连接已关闭")


if __name__ == "__main__":
    main()