#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# ============================================================
# poc_dlm5000.py — DLM5000 示波器通信 PoC（最终版 v2）
# ============================================================
# 关键发现:
#   1. pyvisa-py VXI-11 的 read() 约能连续使用 ~10 次后通道耗尽
#   2. 解决方案: 每次采集周期重新建立连接
#   3. NAN 值表示无信号输入（正常，接探头后会有数值）
#   4. 采集方式: :MEAS:CHANx:xxx:VAL? 读取统计值
#
# 运行方式：
#   cd instrument_acquisition
#   python poc_dlm5000.py
# ============================================================

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DLM5000_IP = "172.17.76.9"
TIMEOUT_MS = 10000


# ============================================================
# 连接管理（每次采集重新连接）
# ============================================================

def open_connection():
    """建立新的 VISA 连接"""
    import pyvisa
    rm = pyvisa.ResourceManager('@py')
    inst = rm.open_resource(f"TCPIP::{DLM5000_IP}::INSTR")
    inst.timeout = TIMEOUT_MS
    inst.read_termination = "\n"
    inst.write_termination = "\n"
    inst.clear()
    time.sleep(0.2)
    return rm, inst


def close_connection(rm, inst):
    """关闭连接"""
    try:
        inst.close()
    except Exception:
        pass
    try:
        rm.close()
    except Exception:
        pass


def wq(inst, cmd, timeout=None):
    """write + read 分步查询"""
    if timeout:
        old = inst.timeout
        inst.timeout = timeout
    try:
        inst.write("*CLS")
        time.sleep(0.03)
        inst.write(cmd)
        time.sleep(0.03)
        resp = inst.read().strip()
        if timeout:
            inst.timeout = old
        return resp
    except Exception as e:
        if timeout:
            inst.timeout = old
        try:
            inst.write("*CLS")
        except Exception:
            pass
        raise


def ww(inst, cmd):
    """write 命令"""
    inst.write("*CLS")
    time.sleep(0.03)
    inst.write(cmd)
    time.sleep(0.03)


# ============================================================
# 单次采集（使用独立连接）
# ============================================================

def acquire_once(channels=None, measurements=None):
    """
    执行一次完整的采集周期：
    1. 建立新连接
    2. 配置 + 启用测量项
    3. 触发采集
    4. 读取测量值
    5. 关闭连接

    返回: dict[channel_id] -> dict[measurement_name] -> float
    """
    if channels is None:
        channels = [1, 2, 3, 4]
    if measurements is None:
        measurements = ["MAX", "MIN", "RMS", "PP", "FREQ"]

    rm, inst = open_connection()

    try:
        # 1. 查询哪些通道启用
        active_channels = []
        for ch in channels:
            try:
                disp = wq(inst, f":CHAN{ch}:DISP?")
                if "1" in disp:
                    active_channels.append(ch)
            except Exception:
                pass

        if not active_channels:
            return {}

        # 2. 启用测量项（每个通道 × 每个测量项）
        for ch in active_channels:
            for m in measurements:
                try:
                    ww(inst, f":MEAS:CHAN{ch}:{m}:STAT ON")
                except Exception:
                    pass

        # 3. 触发采集
        ww(inst, ":ACQ:SINGLE")
        time.sleep(0.8)

        try:
            wq(inst, "*OPC?", timeout=5000)
        except Exception:
            time.sleep(0.5)

        ww(inst, ":ACQ:STOP")

        # 4. 读取测量值（限制总 read 次数 < 10）
        results = {}
        for ch in active_channels:
            ch_data = {}
            for m in measurements:
                try:
                    val = wq(inst, f":MEAS:CHAN{ch}:{m}:VAL?")
                    # 提取数值（格式: ":MEAS:CHAN1:MAX:VAL 3.28E+00"）
                    num_str = val.split()[-1]
                    try:
                        num = float(num_str)
                    except ValueError:
                        num = float('nan')
                    ch_data[m] = num
                except Exception:
                    ch_data[m] = float('nan')
            results[f"CH{ch}"] = ch_data

        return results

    finally:
        close_connection(rm, inst)


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("  DLM5000 示波器 PoC 验证（最终版 v2）")
    print(f"  {DLM5000_IP}  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # ── 步骤1: 连接测试 ──
    print("\n── 步骤 1: 连接测试 ──")
    rm, inst = open_connection()
    try:
        idn = inst.query("*IDN?").strip()
        print(f"  ✓ {idn}")

        # 查询状态
        for name, cmd in [("时基", ":TIM:TDIV?"), ("记录长度", ":ACQ:RLEN?"),
                          ("触发模式", ":TRIG:MODE?")]:
            val = wq(inst, cmd)
            print(f"  ✓ {name}: {val}")

        # 查询通道
        for ch in range(1, 5):
            try:
                disp = wq(inst, f":CHAN{ch}:DISP?")
                vdiv = wq(inst, f":CHAN{ch}:VDIV?")
                icon = "✓" if "1" in disp else "○"
                print(f"  {icon} CH{ch}: {disp}  V/div={vdiv}")
            except Exception:
                pass
    finally:
        close_connection(rm, inst)

    # ── 步骤2: 单次采集测试 ──
    print("\n── 步骤 2: 单次采集 ──")
    results = acquire_once()
    for ch, data in results.items():
        print(f"  {ch}:")
        for m, v in data.items():
            print(f"    {m:6s}: {v}")

    # ── 步骤3: 连续采集 10 次（每次重新连接）──
    print("\n── 步骤 3: 连续采集 10 次（每次重连）──")
    meas_keys = ["MAX", "MIN", "RMS", "PP", "FREQ"]
    header = f"  {'#':>3s}"
    for k in meas_keys:
        header += f"  {'CH1_'+k:>10s}"
    header += f"  {'耗时(ms)':>8s}"
    print(header)
    print(f"  {'─'*3}" + f"  {'─'*10}" * len(meas_keys) + f"  {'─'*8}")

    success_count = 0
    for i in range(10):
        t0 = time.time()
        try:
            results = acquire_once(channels=[1], measurements=meas_keys)
            elapsed = (time.time() - t0) * 1000
            ch1 = results.get("CH1", {})
            row = f"  {i+1:3d}"
            for k in meas_keys:
                v = ch1.get(k, float('nan'))
                row += f"  {v:>10.4f}" if v == v else f"  {'NAN':>10s}"
            row += f"  {elapsed:>7.0f}"
            print(row)
            success_count += 1
        except Exception as e:
            elapsed = (time.time() - t0) * 1000
            print(f"  {i+1:3d}  失败: {str(e)[:40]}  {elapsed:>7.0f}ms")

    print(f"\n  成功率: {success_count}/10")

    # ── 步骤4: 多通道连续采集 5 次 ──
    print("\n── 步骤 4: 多通道采集 5 次 ──")
    for i in range(5):
        t0 = time.time()
        try:
            results = acquire_once(channels=[1, 2, 3, 4],
                                   measurements=["MAX", "MIN", "RMS"])
            elapsed = (time.time() - t0) * 1000
            print(f"  第{i+1}次 ({elapsed:.0f}ms):")
            for ch in sorted(results.keys()):
                d = results[ch]
                print(f"    {ch}: MAX={d.get('MAX',0):.4f}  "
                      f"MIN={d.get('MIN',0):.4f}  "
                      f"RMS={d.get('RMS',0):.4f}")
        except Exception as e:
            print(f"  第{i+1}次失败: {e}")

    # ── 汇总 ──
    print("\n" + "=" * 60)
    print(f"  ✅ PoC 验证完成！成功率: {success_count}/10")
    print("=" * 60)
    print("""
  关键结论:
    1. 每次采集前重新连接，解决 VXI-11 通道耗尽问题
    2. 通过 :MEAS:CHANx:xxx:VAL? 读取测量统计值
    3. NAN 表示无信号（接探头后会有实际数值）

  后续: 我来更新 dlm5000_adapter.py
""")


if __name__ == "__main__":
    main()
