# -*- coding: utf-8 -*-
import os
import sys
import traceback


def _base():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def T(msg):
    if not os.environ.get('TZDEBUG'):
        return
    try:
        with open(os.path.join(_base(), 'trace.log'), 'a', encoding='utf-8') as f:
            f.write('%s\n' % msg)
            f.flush()
    except Exception:
        pass


T('=== boot ===')
try:
    sys.path.insert(0, _base())
    T('sys.path ok')
    from tz.gui import main
    T('import tz.gui ok')
    main()
    T('main returned')
except Exception:
    T('FATAL:\n' + traceback.format_exc())
    raise
