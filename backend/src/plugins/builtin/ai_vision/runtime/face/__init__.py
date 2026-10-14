# -*- coding: utf-8 -*-
"""人脸识别运行时：引擎 / 人脸库缓存 / 判定器（P1+P2 分期实现）。

- ``engine.py``：YuNet + SFace 推理（P1，人脸库登记 + P2 worker 共用）
- ``gallery.py``：人脸库进程内缓存（P1）
- ``detector.py``：FaceDetector 三模式判定（P2）
"""