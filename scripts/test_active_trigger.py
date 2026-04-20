"""测试主动调用 TTVideoEngine.setVideoID + play 是否会触发视频加载 Hook。

先找一个 playbackState=0 且 currentPath 空的 engine 实例，然后在主线程上
调用 setVideoID(target_vid) + play()，观察是否触发 setVideoModel / av_aes_init。
"""
from __future__ import annotations
import sys
import time
from pathlib import Path

import frida

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.download_drama import select_running_app_pid

APP_PACKAGE = "com.phoenix.read"
# 用已知 ep19 的 vid 测试
TARGET_VID = "v02ebeg10000d75383nog65uk4l1osc0"

HOOK = r"""
var TARGET_VID = '__VID__';

Java.perform(function() {
    var TTE;
    try {
        TTE = Java.use('com.ss.ttvideoengine.TTVideoEngine');
    } catch (e) {
        send({t:'err', m:'cannot Java.use TTVideoEngine: '+e.toString()});
        return;
    }

    // 先挂 setVideoModel 观察是否被自动调用
    try {
        var setVM = TTE.setVideoModel.overload('com.ss.ttvideoengine.model.IVideoModel');
        setVM.implementation = function(m) {
            send({t:'setVideoModel_call', hash: this.hashCode()});
            return setVM.call(this, m);
        };
    } catch (e) { send({t:'err', m:'hook setVideoModel: '+e.toString()}); }

    // 选择候选实例
    var candidates = [];
    Java.choose('com.ss.ttvideoengine.TTVideoEngine', {
        onMatch: function(inst) {
            try {
                var state = inst.getPlaybackState();
                var path = String(inst.getCurrentPlayPath() || '');
                candidates.push({inst: inst, state: state, pathEmpty: (path === '')});
            } catch (e) {}
        },
        onComplete: function() {
            send({t:'found', count: candidates.length});
            // 挑第一个 state==0 且 path 空
            var chosen = null;
            for (var i = 0; i < candidates.length; i++) {
                if (candidates[i].state === 0 && candidates[i].pathEmpty) {
                    chosen = candidates[i];
                    break;
                }
            }
            if (!chosen) {
                // 退而求其次，选任意 state==0
                for (var i = 0; i < candidates.length; i++) {
                    if (candidates[i].state === 0) { chosen = candidates[i]; break; }
                }
            }
            if (!chosen) {
                send({t:'err', m:'no idle instance found'});
                return;
            }
            send({t:'chosen', hash: chosen.inst.hashCode()});

            // 在主线程上调用 setVideoID + play
            Java.scheduleOnMainThread(function() {
                try {
                    send({t:'try_setVideoID'});
                    chosen.inst.setVideoID(TARGET_VID);
                    send({t:'setVideoID_ok'});
                    // 稍等再 play
                    setTimeout(function() {
                        Java.scheduleOnMainThread(function() {
                            try {
                                chosen.inst.play();
                                send({t:'play_ok'});
                            } catch (e) {
                                send({t:'err', m:'play: '+e.toString()});
                            }
                        });
                    }, 500);
                } catch (e) {
                    send({t:'err', m:'setVideoID: '+e.toString()});
                }
            });
        }
    });
});
""".replace('__VID__', TARGET_VID)

def main():
    device = frida.get_usb_device(timeout=10)
    pid = select_running_app_pid(device.enumerate_processes(), APP_PACKAGE)
    if pid is None:
        print('App 未运行'); return
    session = device.attach(pid)
    script = session.create_script(HOOK)

    def on_msg(msg, data):
        if msg.get('type') != 'send':
            print('[RAW]', msg); return
        print('[HOOK]', msg['payload'])

    script.on('message', on_msg)
    script.load()

    # 保持 20s 观察
    time.sleep(20)

if __name__ == '__main__':
    main()
