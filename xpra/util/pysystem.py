# This file is part of Xpra.
# Copyright (C) 2019-2024 Antoine Martin <antoine@xpra.org>
# Xpra is released under the terms of the GNU GPL v2, or, at your option, any
# later version. See the file COPYING for details.

import os
import sys
from collections.abc import Callable


def dump_all_frames(logger=None) -> None:
    try:
        frames = sys._current_frames()  # pylint: disable=protected-access
    except AttributeError:
        return
    else:
        dump_frames(frames.items(), logger)


def dump_gc_frames(logger=None) -> None:
    import gc
    import inspect
    gc.collect()
    frames = tuple((None, x) for x in gc.get_objects() if inspect.isframe(x))
    dump_frames(frames, logger)


def dump_frames(frames, logger=None) -> None:
    if not logger:
        from xpra.util.io import get_util_logger
        logger = get_util_logger()
    logger("found %s frames:", len(frames))
    for i, (fid, frame) in enumerate(frames):
        fidstr = ""
        if fid is not None:
            try:
                fidstr = hex(fid)
            except TypeError:
                fidstr = str(fid)
        logger("%i: %s %s:", i, fidstr, frame, backtrace=True)


def detect_leaks() -> Callable[[], bool]:
    import tracemalloc
    tracemalloc.start()
    last_snapshot = [tracemalloc.take_snapshot()]

    def print_leaks() -> bool:
        s1 = last_snapshot[0]
        s2 = tracemalloc.take_snapshot()
        last_snapshot[0] = s2
        top_stats = s2.compare_to(s1, 'lineno')
        print("[ Top 20 differences ]")
        for stat in top_stats[:20]:
            print(stat)
        for i, stat in enumerate(top_stats[:20]):
            print()
            print("top %i:" % i)
            print("{} memory blocks: {:.1f} KiB".format(stat.count, stat.size / 1024))
            for line in stat.traceback.format():
                print(line)
        return True

    return print_leaks


def start_mem_watcher(ms) -> None:
    from xpra.util.thread import start_thread
    start_thread(mem_watcher, name="mem-watcher", daemon=True, args=(ms,))


def mem_watcher(ms, pid: int = os.getpid()) -> None:
    import time
    import psutil
    process = psutil.Process(pid)
    from xpra.util.io import get_util_logger
    logger = get_util_logger()
    while True:
        mem = process.memory_full_info()
        logger.info("memory usage for %s: %s", pid, mem)
        time.sleep(ms / 1000.0)


def log_mem_info(prefix="memory usage: ", pid=os.getpid()) -> None:
    import psutil
    process = psutil.Process(pid)
    mem = process.memory_full_info()
    print("%i %s%s" % (pid, prefix, mem))


def enforce_features(features, feature_map: dict[str, str]) -> None:
    """
    Prevent the modules from being imported later
    """
    from xpra.util.env import envbool
    debug_features = envbool("XPRA_FEATURES_DEBUG", False)
    for feature, modules in feature_map.items():
        enabled: bool = getattr(features, feature)
        for module in modules.split(","):
            value = sys.modules.get(module)
            if debug_features:
                from importlib.util import find_spec
                try:
                    exists = find_spec(module)
                except ModuleNotFoundError:
                    exists = False
                from xpra.util.io import get_util_logger
                log = get_util_logger()
                log.info(f"feature {feature!r:20}: {module!r:40} {enabled=}, found={exists}, value={value}")
            if not enabled:
                if value is not None:
                    from xpra.util.io import get_util_logger
                    log = get_util_logger()
                    log.warn(f"Warning: cannot disable {feature!r} feature")
                    log.warn(f" the module {module!r} is already loaded")
                else:
                    sys.modules[module] = None
