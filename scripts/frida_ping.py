#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""最小 frida attach 测试: 验证 Java bridge 是否响应."""
import subprocess, time

r = subprocess.run(['adb','-s','4d53df1f','shell','pidof','com.phoenix.read'],
                   capture_output=True, text=True, timeout=5)
pid = int(r.stdout.strip().split()[0])
print(f'pid={pid}')

import frida
dev = frida.get_device('4d53df1f')
print('device ok')
sess = dev.attach(pid)
print('attach ok')
script = sess.create_script("send({t:'hi', ts: Date.now()});")
print('create ok')

got = []
def on_msg(m, d):
    got.append(m)
    print('recv:', m)

script.on('message', on_msg)
script.load()
print('load ok')
time.sleep(0.5)
print(f'got {len(got)} msg')
try: script.unload()
except Exception as e: print('unload err:', e)
try: sess.detach()
except Exception as e: print('detach err:', e)
print('done')
