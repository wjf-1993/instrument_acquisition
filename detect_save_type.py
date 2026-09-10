#!/usr/bin/env python3
"""测试 DLM5000 文件列表相关 SCPI 命令"""
import pyvisa
import sys

IP = sys.argv[1] if len(sys.argv) > 1 else "172.17.76.202"

rm = pyvisa.ResourceManager('@py')
inst = rm.open_resource(f"TCPIP0::{IP}::INSTR")
inst.timeout = 5000
inst.read_termination = "\n"
inst.write_termination = "\n"

commands = [
    ":FILE:DIR?",
    ":FILE:CAT?",
    ":FILE:CATA?",
    ":FILE:CATA:ALL?",
    ":FILE:LIST?",
    ":FILE:OPER:CAT?",
    ":MMEM:CAT?",
    ":MMEM:DIR?",
    ":MASS:STOR:CAT?",
    ":FILE:SAVE:NAME?",
    ":FILE:PATH?",
    ":FILE:FREE?",
    ":FILE:COUN?",
    ":FILE:NUM?",
    ":FILE:HIST?",
    ":FILE:HIST:NAME?",
    ":FILE:HIST:COUN?",
    ":FILE:REC?",
    ":FILE:REC:COUN?",
    ":FILE:REC:NAME?",
]

print("=" * 60)
print(f"  DLM5000 文件列表命令测试 ({IP})")
print("=" * 60)

for cmd in commands:
    try:
        inst.write(cmd)
        import time
        time.sleep(0.1)
        resp = inst.read().strip()
        print(f"  {cmd:30s} → {resp[:100]}")
    except Exception as e:
        print(f"  {cmd:30s} → (错误: {e})")

inst.close()
rm.close()
print("\n完成！")