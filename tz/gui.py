# -*- coding: utf-8 -*-
"""墙背回填/挡墙台账 报检照片登记助手 —— 图形界面"""
import json
import os
import queue
import threading
import time
import traceback
from datetime import date

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

from .ledger import Ledger, fmt_km, get_column_letter
from .parse import parse, from_vlm, Record
from .vlm import VLM, PROVIDERS, load_cfg, save_cfg

def _T(msg):
    import sys, os
    if not os.environ.get('TZDEBUG'):
        return
    try:
        base = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, 'trace.log'), 'a', encoding='utf-8') as f:
            f.write('  [gui] %s\n' % msg)
            f.flush()
    except Exception:
        pass


APP_NAME = '挡墙台账登记助手'
CFG_DIR = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'TzLedger')
CFG_PATH = os.path.join(CFG_DIR, 'config.json')

IMG_EXT = ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff')

EDIT_FIELDS = [
    ('km_start', '里程起(m)', 'num'),
    ('km_end', '里程止(m)', 'num'),
    ('side', '左右侧', 'str'),
    ('part_kind', '部位类型', ('基础', '墙身', '托梁/垫层', '扩大基础')),
    ('layer', '层号', 'int'),
    ('h_this', '本次浇筑(m)', 'num'),
    ('h_cum', '累计高度(m)', 'num'),
    ('volume', '设计方量(m³)', 'num'),
    ('strength', '强度等级', 'str'),
    ('w_top', '顶宽(m)', 'num'),
    ('w_bot', '底宽(m)', 'num'),
    ('q_height', '高(m)', 'num'),
    ('date', '浇筑日期', 'date'),
    ('reporter', '报检人', 'str'),
    ('team', '班组', 'str'),
    ('period', '计价期次', 'str'),
]


def _screen():
    """取物理分辨率。Tk 的 winfo_screenwidth 在高 DPI 下会返回缩放后的逻辑值，
    导致窗口按 948px 建出来，表格列全被挤掉。这里直接问系统要物理像素。"""
    try:
        import ctypes
        sw = ctypes.windll.user32.GetSystemMetrics(0)
        sh = ctypes.windll.user32.GetSystemMetrics(1)
        if sw > 800 and sh > 600:
            return sw, sh
    except Exception:
        pass
    return 1360, 860


def compact_km(a, b=None):
    if a is None:
        return ''
    ka = int(a // 1000)
    sa = 'K%d+%07.3f' % (ka, a - ka * 1000)
    if b is None or abs(b - a) < 1e-6:
        return sa
    kb = int(b // 1000)
    sb = ('%d+%07.3f' % (kb, b - kb * 1000)) if kb != ka else ('%07.3f' % (b - kb * 1000))
    return '%s~%s' % (sa, sb)


def load_cfg():
    try:
        with open(CFG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(CFG_PATH, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


class Job:
    """一条待登记任务"""
    __slots__ = ('img', 'rec', 'block', 'part', 'locate_msg', 'part_msg',
                 'vals', 'notes', 'status', 'text', 'cands', 'via')

    def __init__(self, img):
        self.img = img
        self.rec = None
        self.block = None
        self.part = None
        self.locate_msg = ''
        self.part_msg = ''
        self.vals = {}
        self.notes = []
        self.status = '待识别'
        self.text = ''
        self.cands = []
        self.via = ''

    @property
    def ok(self):
        return self.block is not None and self.part is not None

    def rec_dict(self):
        r = self.rec
        return {k: getattr(r, k) for k in
                ('doc_type', 'h_this', 'h_cum', 'volume', 'strength', 'w_top', 'w_bot',
                 'q_height', 'date', 'reporter', 'team', 'period',
                 'form_h0', 'form_h1', 'w_top_meas', 'w_bot_meas',
                 'check_result', 'inspector', 'foreman', 'critical')}


class App(tk.Tk):
    def __init__(self, initial_files=None):
        super().__init__()
        _T('Tk root created')
        self.title(APP_NAME)
        # 高 DPI（用户机器 2560x1440 / 200% 缩放）下 Tk 的像素换算不可靠，
        # 算出来的窗口会小一半、表格列被挤掉。直接最大化最稳。
        sw, sh = _screen()
        w = min(1320, max(1000, sw - 40))
        h = min(840, max(640, sh - 70))
        self.geometry('%dx%d+%d+%d' % (w, h, max(0, (sw - w) // 2), 20))
        self.minsize(980, 580)
        self._maximized = False
        self.jobs = []
        self.ledger = None
        self.cfg = load_cfg()
        self.q = queue.Queue()
        self.busy = False
        self._ocr = None
        _T('build_ui start')
        self._build_ui()
        _T('build_ui done')
        self.after(100, self._poll)
        _T('after() scheduling')
        _T('after() scheduling')
        path = self.cfg.get('ledger')
        if path and os.path.exists(path):
            self.var_ledger.set(path)
            self.after(200, self.open_ledger)
        p = self.cfg.get('photos')
        if p and os.path.isdir(p):
            self.var_photo_dir.set(p)
        if initial_files:
            self.after(900, lambda: self._auto_run(list(initial_files)))

    def _auto_run(self, files):
        """拖拽/命令行传入照片时，自动识别"""
        try:
            if self.ledger is None:
                self.open_ledger()
            if self.ledger is None:
                self.lbl_status.config(text='请先选择台账文件')
                return
            self._add(files)
            self.run_ocr()
        except Exception:
            pass

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use('vista')
        except Exception:
            pass
        style.configure('Treeview', rowheight=24)
        style.configure('Warn.TLabel', foreground='#b45309')
        style.configure('Err.TLabel', foreground='#b91c1c')
        style.configure('Ok.TLabel', foreground='#15803d')

        top = ttk.Frame(self, padding=(10, 8, 10, 4))
        top.pack(fill='x')
        ttk.Label(top, text='台账文件：').pack(side='left')
        self.var_ledger = tk.StringVar()
        ttk.Entry(top, textvariable=self.var_ledger).pack(side='left', fill='x', expand=True, padx=4)
        ttk.Button(top, text='选择台账', command=self.choose_ledger).pack(side='left')
        ttk.Button(top, text='重新载入', command=self.open_ledger).pack(side='left', padx=4)
        self.lbl_ledger = ttk.Label(top, text='未载入', style='Warn.TLabel', width=30)
        self.lbl_ledger.pack(side='left', padx=(8, 0))

        body = ttk.Panedwindow(self, orient='horizontal')
        body.pack(fill='both', expand=True, padx=10, pady=4)

        # 左：照片列表
        left = ttk.Frame(body, width=300)
        left.pack_propagate(False)
        body.add(left, weight=0)
        bar = ttk.Frame(left)
        bar.pack(fill='x')
        ttk.Button(bar, text='添加照片', command=self.add_photos).pack(side='left')
        ttk.Button(bar, text='添加文件夹', command=self.add_folder).pack(side='left', padx=4)
        ttk.Button(bar, text='移除', command=self.remove_sel).pack(side='left')
        ttk.Button(bar, text='清空', command=self.clear_photos).pack(side='left', padx=4)
        self.var_photo_dir = tk.StringVar()
        self.lst = tk.Listbox(left, selectmode='extended', activestyle='none')
        self.lst.pack(fill='both', expand=True, pady=4)

        # 右：结果表
        right = ttk.Frame(body)
        body.add(right, weight=3)
        cols = ('no', 'file', 'kind', 'km', 'part', 'h', 'vol', 'str', 'date',
                'block', 'row', 'status')
        heads = {'no': '#', 'file': '照片', 'kind': '单据', 'km': '里程', 'part': '部位',
                 'h': '本次高', 'vol': '方量', 'str': '强度', 'date': '日期',
                 'block': '台账块', 'row': '目标行', 'status': '状态'}
        widths = {'no': 28, 'file': 74, 'kind': 52, 'km': 128, 'part': 64, 'h': 44,
                  'vol': 50, 'str': 38, 'date': 64, 'block': 92, 'row': 50, 'status': 240}
        self.tree = ttk.Treeview(right, columns=cols, show='headings', height=16)
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor='center' if c != 'status' else 'w')
        vs = ttk.Scrollbar(right, orient='vertical', command=self.tree.yview)
        hs = ttk.Scrollbar(right, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        vs.pack(side='right', fill='y')
        hs.pack(side='bottom', fill='x')
        self.tree.pack(side='left', fill='both', expand=True)
        self.tree.tag_configure('warn', background='#fef3c7')
        self.tree.tag_configure('err', background='#fee2e2')
        self.tree.tag_configure('ok', background='#dcfce7')
        self.tree.bind('<Double-1>', self.edit_selected)
        self.tree.bind('<<TreeviewSelect>>', self._on_select)

        # 底部
        bot = ttk.Frame(self, padding=(10, 4, 10, 8))
        bot.pack(fill='x')
        self.btn_ocr = ttk.Button(bot, text='① 开始识别', command=self.run_ocr)
        self.btn_ocr.pack(side='left')
        self.btn_fill = ttk.Button(bot, text='② 写入台账', command=self.do_fill, style='Accent.TButton')
        self.btn_fill.pack(side='left', padx=6)
        ttk.Button(bot, text='③ 导出结果', command=self.export).pack(side='left')
        ttk.Button(bot, text='查看OCR原文', command=self.show_raw).pack(side='left', padx=6)
        ttk.Button(bot, text='指定台账块', command=self.pick_block).pack(side='left')
        ttk.Button(bot, text='设置', command=self.open_settings).pack(side='left', padx=6)
        self.var_overwrite = tk.BooleanVar(value=False)
        ttk.Checkbutton(bot, text='允许覆盖已填行',
                        variable=self.var_overwrite).pack(side='left', padx=(4, 0))
        self.pb = ttk.Progressbar(bot, length=220, mode='determinate')
        self.pb.pack(side='left', padx=12)
        self.lbl_status = ttk.Label(bot, text='就绪：先选台账 → 添加照片 → 开始识别（右上「设置」可切换识别引擎）')
        self.lbl_status.pack(side='left', padx=6)

    # ------------------------------------------------------------- 台账
    def choose_ledger(self):
        p = filedialog.askopenfilename(title='选择挡墙施工台账',
                                       filetypes=[('Excel 工作簿', '*.xlsx *.xlsm'), ('全部', '*.*')])
        if p:
            self.var_ledger.set(p)
            self.open_ledger()

    def open_ledger(self):
        p = self.var_ledger.get().strip()
        if not p or not os.path.exists(p):
            self.lbl_ledger.config(text='文件不存在', style='Err.TLabel')
            return
        _T('open_ledger: ' + p)
        _T('open_ledger: ' + p)
        self.lbl_ledger.config(text='载入中…', style='Warn.TLabel')
        self.update_idletasks()
        try:
            t0 = time.time()
            self.ledger = Ledger(p)
            _T('open_ledger done: %d blocks' % len(self.ledger.blocks))
            _T('open_ledger done: %d blocks' % len(self.ledger.blocks))
            self.lbl_ledger.config(text='已载入 %d 个挡墙块（%.1fs）' % (len(self.ledger.blocks), time.time() - t0),
                                   style='Ok.TLabel')
            self.cfg['ledger'] = p
            save_cfg(self.cfg)
            self.refresh_all()
        except Exception as e:
            self.lbl_ledger.config(text='载入失败：%s' % e, style='Err.TLabel')

    # ------------------------------------------------------------- 照片
    def add_photos(self):
        d = self.var_photo_dir.get() or ''
        ps = filedialog.askopenfilenames(title='选择报检照片', initialdir=d or None,
                                         filetypes=[('图片', '*.png *.jpg *.jpeg *.bmp *.webp'), ('全部', '*.*')])
        if ps:
            self.var_photo_dir = tk.StringVar(value=os.path.dirname(ps[0]))
            self.cfg['photos'] = os.path.dirname(ps[0])
            save_cfg(self.cfg)
            self._add(list(ps))

    def add_folder(self):
        d = filedialog.askdirectory(title='选择照片文件夹')
        if not d:
            return
        files = sorted(os.path.join(d, f) for f in os.listdir(d)
                       if f.lower().endswith(IMG_EXT))
        self.cfg['photos'] = d
        save_cfg(self.cfg)
        self._add(files)

    def _add(self, files):
        exist = {j.img for j in self.jobs}
        n = 0
        for f in files:
            if f in exist:
                continue
            self.jobs.append(Job(f))
            n += 1
        self.refresh_all()
        self.lbl_status.config(text='已添加 %d 张照片' % n)

    def remove_sel(self):
        for i in sorted(self.lst.curselection(), reverse=True):
            del self.jobs[i]
        self.refresh_all()

    def clear_photos(self):
        self.jobs = []
        self.refresh_all()

    # ------------------------------------------------------------- 识别
    def _get_ocr(self):
        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR
            self._ocr = RapidOCR()
        return self._ocr

    def run_ocr(self):
        if self.busy:
            return
        todo = [j for j in self.jobs if j.status in ('待识别', '失败')]
        if not todo:
            messagebox.showinfo(APP_NAME, '没有待识别的照片')
            return
        if self.ledger is None:
            messagebox.showwarning(APP_NAME, '请先载入台账文件')
            return
        self.busy = True
        self.btn_ocr.config(state='disabled')
        self.pb.config(maximum=len(todo), value=0)
        th = threading.Thread(target=self._ocr_worker, args=(todo,), daemon=True)
        th.start()

    def _ocr_worker(self, todo):
        cfg = load_cfg()
        vlm = None
        if (cfg.get('engine') or 'ocr') == 'vlm':
            vlm = VLM.from_cfg(cfg)
            if not vlm.ready:
                self.q.put(('err', '视觉大模型未配置 API Key，本次回退本地 OCR'))
                vlm = None
        eng = None
        if vlm is None:
            try:
                eng = self._get_ocr()
            except Exception as e:
                self.q.put(('err', 'OCR 引擎初始化失败：%s' % e))
                return
        for j in todo:
            try:
                if vlm is not None:
                    d, raw = vlm.read(j.img)
                    j.text = raw
                    j.rec = from_vlm(d)
                    j.via = '视觉大模型'
                else:
                    res, _ = eng(j.img)
                    lines = [t for _b, t, _c in res] if res else []
                    text = '\n'.join(lines)
                    j.text = text
                    j.rec = parse(text)
                    j.via = '本地OCR'
                self._locate(j)
                if j.rec.critical:
                    j.status = '已拒绝'
                elif j.ok:
                    j.status = '需确认' if j.rec.warnings else '就绪'
                else:
                    j.status = '未定位'
            except Exception as e:
                j.status = '失败'
                j.notes = ['识别异常：%s' % e]
            self.q.put(('one', j))
        self.q.put(('done', None))

    def _locate(self, j):
        L = self.ledger
        r = j.rec
        blk, msg, cands = L.locate(r.km_start, r.km_end)
        j.cands = cands
        j.block, j.locate_msg = blk, msg
        j.part, j.part_msg = None, ''
        if not blk:
            j.vals, j.notes = {}, []
            return
        self._bind_part(j)

    def _bind_part(self, j):
        L, r, blk = self.ledger, j.rec, j.block
        kind = 'base' if r.is_base else 'body'
        j.part, j.part_msg = blk.find_part(kind, None if r.is_base else r.layer)
        if j.part:
            j.vals, j.notes = L.build_values(blk, j.part, j.rec_dict())
        else:
            j.vals, j.notes = {}, []

    # ------------------------------------------------------------- 填表
    def refresh_all(self):
        self.lst.delete(0, 'end')
        for j in self.jobs:
            self.lst.insert('end', '%s  [%s]' % (os.path.basename(j.img), j.status))
        for i in self.tree.get_children():
            self.tree.delete(i)
        for idx, j in enumerate(self.jobs, 1):
            r = j.rec
            km = compact_km(r.km_start, r.km_end) if r else ''
            part = r.part_name if r else ''
            tag = ''
            if j.status in ('需确认',):
                tag = 'warn'
            elif j.status in ('未定位', '失败', '已拒绝'):
                tag = 'err'
            elif j.status == '已写入':
                tag = 'ok'
            kind = ''
            if r is not None:
                kind = {'formwork': '模板报验', 'pour': '浇筑'}.get(r.doc_type, '?')
            self.tree.insert('', 'end', iid=str(idx - 1), tags=(tag,), values=(
                idx, os.path.basename(j.img)[:18], kind, km, part,
                '' if not r or r.h_this is None else '%.2f' % r.h_this,
                '' if not r or r.volume is None else '%.2f' % r.volume,
                r.strength if r else '',
                r.date if r else '',
                ('第%d-%d行' % (j.block.row, j.block.end)) if j.block else '—',
                ('第%d行 %s' % (j.part.row, j.part.i_text)) if j.part else '',
                self._status_text(j),
            ))
        for i in range(len(self.jobs)):
            pass

    def _on_select(self, _e=None):
        """选中一行时在状态栏显示完整原因（表格里放不下）"""
        sel = self.tree.selection()
        if not sel or not hasattr(self, 'lbl_status'):
            return
        try:
            j = self.jobs[int(sel[0])]
        except Exception:
            return
        buf = []
        if j.via:
            buf.append('识别引擎：' + j.via)
        if j.rec:
            buf.append('里程 ' + (j.rec.km_text or '未识别'))
            if j.rec.warnings:
                buf.append('问题：' + '；'.join(j.rec.warnings))
        if j.notes:
            buf.append('提示：' + '；'.join(j.notes[:3]))
        if j.locate_msg and j.block is None:
            buf.append('定位：' + j.locate_msg)
        self.lbl_status.config(text=(' ｜ '.join(buf))[:300] or j.status)

    def _status_text(self, j):
        if j.status == '已写入':
            return '✔ 已写入'
        if j.status == '已拒绝':
            ws = (j.rec.warnings if j.rec else []) or ['安全校验未通过']
            t = ws[0]
            if len(ws) > 1:
                t += '（另%d项）' % (len(ws) - 1)
            return '✗ 已拒绝：' + t
        if j.status == '失败':
            return '✗ ' + '；'.join(j.notes[:1])
        if not j.block:
            return '✗ ' + (j.locate_msg or '未定位')
        if not j.part:
            return '✗ ' + (j.part_msg or '未匹配部位')
        txt = '✔ ' + j.part_msg
        if j.block and j.locate_msg.startswith('⚠'):
            txt = '⚠ ' + j.locate_msg
        elif j.rec and j.rec.warnings:
            txt = '⚠ ' + '；'.join(j.rec.warnings)
        elif j.part_msg.startswith('⚠'):
            txt = '⚠ ' + j.part_msg[2:]
        return txt

    def edit_selected(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            return
        j = self.jobs[int(sel[0])]
        dlg = EditDialog(self, j)
        self.wait_window(dlg)
        if dlg.saved:
            self._locate(j)
            if j.block is None and j.cands:
                # 改完里程仍跨多块：直接让用户指定
                self.pick_block()
                return
            if j.rec.critical:
                j.status = '已拒绝'
            else:
                j.status = '就绪' if j.ok and not j.rec.warnings else ('需确认' if j.ok else '未定位')
            self.refresh_all()

    def do_fill(self):
        if self.busy:
            return
        if self.ledger is None:
            messagebox.showwarning(APP_NAME, '请先载入台账文件')
            return
        ready = [j for j in self.jobs if j.ok and j.status not in ('已写入', '已拒绝')]
        blocked = [j for j in self.jobs if j.status == '已拒绝']
        if not ready:
            extra = ''
            if blocked:
                extra = '\n\n有 %d 条被安全校验拒绝（里程/尺寸明显错误），已跳过。' % len(blocked)
            messagebox.showinfo(APP_NAME, '没有可写入的记录（需先识别并成功定位）' + extra)
            return
        warns = [j for j in ready if j.status == '需确认']
        msg = '将写入 %d 条记录到台账：\n%s\n\n' % (
            len(ready), os.path.basename(self.ledger.path))
        if blocked:
            msg += '另有 %d 条因识别结果明显错误被拒绝，不会写入。\n\n' % len(blocked)
        if warns:
            msg += '其中 %d 条带 ⚠ 提示，请确认已在表格中核对过。\n\n' % len(warns)
        msg += '写入前会自动备份原文件，是否继续？'
        if not messagebox.askyesno(APP_NAME, msg):
            return
        self.busy = True
        self.btn_fill.config(state='disabled')
        self.lbl_status.config(text='正在写入…')
        self.update_idletasks()
        try:
            bak = self.ledger.backup()
            # 按块内行号升序统一排算，保证同块多条记录层层递进
            items = [(j.block, j.part, j.rec_dict()) for j in ready]
            planned = self.ledger.plan(items)
            jm = {id(p): j for _b, p, _r in items for j in ready if j.part is p}
            for _b, p, _r, vals, notes in planned:
                jj = jm.get(id(p))
                if jj:
                    jj.vals, jj.notes = vals, notes
            n, issues = self.ledger.apply(planned, allow_overwrite=self.var_overwrite.get())
            self.ledger.save()
            for j in ready:
                j.status = '已写入'
            self.refresh_all()
            self.lbl_status.config(text='已写入 %d 条，备份：%s' % (n, os.path.basename(bak)))
            tail = ''
            if issues:
                tail = '\n\n提示：\n' + '\n'.join(issues[:12])
                if len(issues) > 12:
                    tail += '\n…共 %d 条' % len(issues)
            messagebox.showinfo(APP_NAME, '写入完成：%d 条\n备份文件：%s%s' %
                                (n, os.path.basename(bak), tail))
            self.ledger.reload()
        except PermissionError:
            messagebox.showerror(APP_NAME, '台账文件被占用，无法保存。\n请先在 Excel/WPS 中关闭该文件后重试。')
        except Exception as e:
            messagebox.showerror(APP_NAME, '写入失败：%s\n\n%s' % (e, traceback.format_exc(limit=3)))
        finally:
            self.busy = False
            self.btn_fill.config(state='normal')

    # ------------------------------------------------------------- 设置
    def open_settings(self):
        dlg = SettingsDialog(self)
        self.wait_window(dlg)
        if dlg.saved:
            self.lbl_status.config(text='设置已保存，识别引擎：%s' % dlg.engine_label())

    # --------------------------------------------------------- 指定台账块
    def pick_block(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, '请先在结果表里选中一条记录')
            return
        j = self.jobs[int(sel[0])]
        if not j.cands:
            messagebox.showinfo(APP_NAME, '该记录没有候选挡墙块。\n请双击该行打开编辑框，先修正里程。')
            return
        dlg = BlockPicker(self, j)
        self.wait_window(dlg)
        if dlg.chosen is None:
            return
        j.block = dlg.chosen
        j.locate_msg = '人工指定'
        self._bind_part(j)
        if j.rec.critical:
            j.status = '已拒绝'
        elif j.part:
            j.status = '需确认' if j.rec.warnings else '就绪'
        else:
            j.status = '未定位'
        self.refresh_all()

    def export(self):
        if not self.jobs:
            messagebox.showinfo(APP_NAME, '没有可导出的记录')
            return
        p = filedialog.asksaveasfilename(title='导出登记结果', defaultextension='.xlsx',
                                         initialfile='报检登记结果_%s.xlsx' % date.today().isoformat(),
                                         filetypes=[('Excel', '*.xlsx')])
        if not p:
            return
        try:
            import openpyxl
            from openpyxl.styles import Font, Alignment, Border, Side
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = '本批登记'
            hdr = ['序号', '照片', '里程', '左右侧', '部位', '本次浇筑(m)', '累计高度(m)',
                   '设计方量(m³)', '强度', '顶宽(m)', '底宽(m)', '高(m)', '浇筑日期',
                   '报检人', '班组', '期次', '台账块', '目标行', '状态']
            ws.append(hdr)
            for i, j in enumerate(self.jobs, 1):
                r = j.rec
                ws.append([i, os.path.basename(j.img), r.km_text if r else '',
                           r.side if r else '', r.part_name if r else '',
                           r.h_this if r else None, r.h_cum if r else None,
                           r.volume if r else None, r.strength if r else '',
                           r.w_top if r else None, r.w_bot if r else None,
                           r.q_height if r else None, r.date if r else '',
                           r.reporter if r else '', r.team if r else '',
                           r.period if r else '',
                           j.block.km_text if j.block else '',
                           j.part.row if j.part else '',
                           self._status_text(j)])
            thin = Side(style='thin')
            bd = Border(left=thin, right=thin, top=thin, bottom=thin)
            for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=len(hdr)):
                for c in row:
                    c.border = bd
                    c.alignment = Alignment(vertical='center', wrap_text=True)
            for c in ws[1]:
                c.font = Font(bold=True)
            widths = [5, 22, 24, 7, 12, 11, 11, 11, 7, 8, 8, 8, 12, 8, 12, 9, 24, 10, 26]
            for i, w in enumerate(widths, 1):
                ws.column_dimensions[get_column_letter(i)].width = w
            ws.freeze_panes = 'A2'
            wb.save(p)
            self.lbl_status.config(text='已导出：%s' % os.path.basename(p))
            if messagebox.askyesno(APP_NAME, '已导出到：\n%s\n\n是否打开所在文件夹？' % p):
                os.startfile(os.path.dirname(p))
        except Exception as e:
            messagebox.showerror(APP_NAME, '导出失败：%s' % e)

    def show_raw(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, '请先在表格里选中一行')
            return
        j = self.jobs[int(sel[0])]
        w = tk.Toplevel(self)
        w.title('OCR 原文 - %s' % os.path.basename(j.img))
        w.geometry('760x560')
        t = scrolledtext.ScrolledText(w, wrap='word', font=('Consolas', 10))
        t.pack(fill='both', expand=True)
        info = ['文件：%s' % j.img, '定位：%s' % j.locate_msg, '部位：%s' % j.part_msg, '']
        if j.rec and j.rec.evidence:
            info.append('字段证据：')
            for k, v in j.rec.evidence.items():
                info.append('  %s ← %s' % (k, v))
            info.append('')
        t.insert('end', '\n'.join(info) + '\n===== OCR 原文 =====\n' + (j.text or '（未识别）'))
        t.config(state='disabled')

    # ------------------------------------------------------------- 轮询
    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == 'one':
                    j = payload
                    idx = self.jobs.index(j)
                    self.lst.delete(idx)
                    self.lst.insert(idx, '%s  [%s]' % (os.path.basename(j.img), j.status))
                    self.pb.step(1)
                    self.lbl_status.config(text='识别中… %s' % os.path.basename(j.img))
                elif kind == 'done':
                    self.busy = False
                    self.btn_ocr.config(state='normal')
                    self.refresh_all()
                    ok = sum(1 for j in self.jobs if j.ok)
                    self.lbl_status.config(text='识别完成：%d 张，成功定位 %d 条' % (len(self.jobs), ok))
                elif kind == 'err':
                    self.busy = False
                    self.btn_ocr.config(state='normal')
                    messagebox.showerror(APP_NAME, payload)
        except queue.Empty:
            pass
        self.after(120, self._poll)


class BlockPicker(tk.Toplevel):
    """里程跨多块 / 并行重复段时，由人工指定具体挡墙块"""

    def __init__(self, master, job):
        super().__init__(master)
        self.title('指定台账块')
        self.geometry('880x420')
        self.transient(master)
        self.chosen = None
        self.cands = job.cands

        top = ttk.Frame(self, padding=(12, 10, 12, 4))
        top.pack(fill='x')
        r = job.rec
        ttk.Label(top, text='照片：%s' % os.path.basename(job.img)).pack(anchor='w')
        ttk.Label(top, text='识别里程：%s    %s' % (
            r.km_text if r else '-', job.locate_msg or ''),
            style='Warn.TLabel').pack(anchor='w', pady=(2, 0))
        ttk.Label(top, text='该里程在台账中对应多个块，请选定一条：'
                            '（"已填/预留"可帮你判断施工进度）').pack(anchor='w', pady=(6, 0))

        cols = ('km', 'len', 'side', 'struct', 'state', 'rel', 'row')
        heads = {'km': '块里程', 'len': '长度(m)', 'side': '左右侧', 'struct': '结构形式',
                 'state': '已填/预留', 'rel': '关系', 'row': '行号'}
        width = {'km': 190, 'len': 70, 'side': 60, 'struct': 110, 'state': 96,
                 'rel': 150, 'row': 70}
        frm = ttk.Frame(self, padding=(12, 4))
        frm.pack(fill='both', expand=True)
        self.tv = ttk.Treeview(frm, columns=cols, show='headings', height=12)
        for c in cols:
            self.tv.heading(c, text=heads[c])
            self.tv.column(c, width=width[c], anchor='center')
        vs = ttk.Scrollbar(frm, orient='vertical', command=self.tv.yview)
        self.tv.configure(yscrollcommand=vs.set)
        self.tv.pack(side='left', fill='both', expand=True)
        vs.pack(side='left', fill='y')
        for i, (b, rel, v) in enumerate(self.cands):
            body = [p for p in b.parts if p.kind in ('body', 'base')]
            done = sum(1 for p in body if p.filled)
            self.tv.insert('', 'end', iid=str(i), values=(
                b.km_text, '%.3f' % (b.length or 0), b.side, b.struct,
                '%d/%d' % (done, len(body)), rel, '第%d行' % b.row))
        if self.cands:
            self.tv.selection_set('0')
            self.tv.focus('0')
        self.tv.bind('<Double-1>', lambda e: self._ok())

        bot = ttk.Frame(self, padding=(12, 6, 12, 12))
        bot.pack(fill='x')
        ttk.Button(bot, text='确定', command=self._ok).pack(side='right')
        ttk.Button(bot, text='取消', command=self.destroy).pack(side='right', padx=6)

    def _ok(self):
        sel = self.tv.selection()
        if sel:
            self.chosen = self.cands[int(sel[0])][0]
            self.destroy()


class SettingsDialog(tk.Toplevel):
    """识别引擎设置"""

    def __init__(self, master):
        super().__init__(master)
        self.title('识别引擎设置')
        self.geometry('660x600')
        self.resizable(False, False)
        self.transient(master)
        self.saved = False
        cfg = load_cfg()
        self.cfg = cfg
        v = cfg.get('vlm') or {}

        frm = ttk.Frame(self, padding=16)
        frm.pack(fill='both', expand=True)
        frm.columnconfigure(1, weight=1)

        ttk.Label(frm, text='识别引擎', font=('微软雅黑', 10, 'bold')).grid(
            row=0, column=0, columnspan=2, sticky='w', pady=(0, 6))
        self.var_engine = tk.StringVar(value=cfg.get('engine', 'ocr'))
        ttk.Radiobutton(frm, text='本地离线 OCR　印刷体准、手写不可用；免密钥、断网可用',
                        variable=self.var_engine, value='ocr').grid(
            row=1, column=0, columnspan=2, sticky='w')
        ttk.Radiobutton(frm, text='视觉大模型　手写可读；需联网 + API Key，每张约几分钱',
                        variable=self.var_engine, value='vlm').grid(
            row=2, column=0, columnspan=2, sticky='w', pady=(0, 8))

        ttk.Separator(frm, orient='horizontal').grid(
            row=3, column=0, columnspan=2, sticky='ew', pady=6)
        ttk.Label(frm, text='视觉大模型参数', font=('微软雅黑', 10, 'bold')).grid(
            row=4, column=0, columnspan=2, sticky='w', pady=(0, 6))

        labels = [p['label'] for p in PROVIDERS.values()]
        inv = {p['label']: k for k, p in PROVIDERS.items()}
        cur = PROVIDERS.get(v.get('provider', 'dashscope'), PROVIDERS['dashscope'])
        ttk.Label(frm, text='服务商').grid(row=5, column=0, sticky='w', pady=3)
        self.var_prov = tk.StringVar(value=cur['label'])
        cb = ttk.Combobox(frm, textvariable=self.var_prov, values=labels,
                          state='readonly', width=36)
        cb.grid(row=5, column=1, sticky='w', pady=3)
        cb.bind('<<ComboboxSelected>>', self._on_prov)

        self.var_key = tk.StringVar(value=v.get('api_key', ''))
        self.var_model = tk.StringVar(value=v.get('model', '') or cur['model'])
        self.var_base = tk.StringVar(value=v.get('base_url', '') or cur['base'])
        for i, (lb, var, show) in enumerate([
                ('API Key', self.var_key, '*'),
                ('模型名', self.var_model, ''),
                ('接口地址', self.var_base, '')], start=6):
            ttk.Label(frm, text=lb).grid(row=i, column=0, sticky='w', pady=2)
            ttk.Entry(frm, textvariable=var, width=40, show=show).grid(
                row=i, column=1, sticky='w', pady=2)

        ttk.Label(frm, text='提示：通义千问可在 dashscope.aliyuncs.com 申请；Key 只存本机\n'
                            '（%APPDATA%\\TzLedger\\config.json），不会上传到任何第三方。',
                  foreground='#6b7280').grid(row=9, column=0, columnspan=2,
                                             sticky='w', pady=(8, 0))
        self.lbl_test = ttk.Label(frm, text='', wraplength=608, justify='left')
        self.lbl_test.grid(row=10, column=0, columnspan=2, sticky='w', pady=(6, 0))

        bot = ttk.Frame(frm)
        bot.grid(row=11, column=0, columnspan=2, sticky='ew', pady=(10, 0))
        ttk.Button(bot, text='测试连接', command=self._test).pack(side='left')
        ttk.Button(bot, text='保存', command=self._save).pack(side='right')
        ttk.Button(bot, text='取消', command=self.destroy).pack(side='right', padx=6)
        self._inv = inv

    def _on_prov(self, _e=None):
        k = self._inv.get(self.var_prov.get(), 'custom')
        p = PROVIDERS[k]
        if k == 'custom':
            cur_base = self.var_base.get().strip()
            if not cur_base.startswith('http'):
                self.var_base.set('https://dashscope.aliyuncs.com/compatible-mode/v1')
            cur_model = self.var_model.get().strip()
            if not cur_model or not cur_model.isascii():
                self.var_model.set('qwen-vl-max')
            return
        self.var_base.set(p['base'])
        self.var_model.set(p['model'])

    def _collect(self):
        k = self._inv.get(self.var_prov.get(), 'custom')
        return k, {'provider': k, 'api_key': self.var_key.get().strip(),
                   'model': self.var_model.get().strip(),
                   'base_url': self.var_base.get().strip()}

    def engine_label(self):
        return '视觉大模型' if self.var_engine.get() == 'vlm' else '本地 OCR'

    def _test(self):
        k, v = self._collect()
        bad = []
        if not v['api_key']:
            bad.append('API Key 为空')
        elif len(v['api_key']) < 20:
            bad.append('API Key 太短，多半是复制不全（应为 sk- 开头的一长串）')
        if not v['base'].startswith('http'):
            bad.append('接口地址为空或不以 http 开头')
        if not v['model']:
            bad.append('模型名为空')
        elif not v['model'].isascii():
            bad.append('模型名「%s」是中文，要填英文模型 ID，如 qwen-vl-max' % v['model'])
        if bad:
            self.lbl_test.config(text='配置有问题：' + '；'.join(bad), style='Err.TLabel')
            return
        self.lbl_test.config(text='正在测试…', style='Warn.TLabel')
        self.update_idletasks()
        try:
            from PIL import Image, ImageDraw
            import io as _io
            im = Image.new('RGB', (420, 90), 'white')
            d = ImageDraw.Draw(im)
            d.text((14, 30), 'TEST K28+035.819 1.96m', fill='black')
            tmp = os.path.join(CFG_DIR, '_t.png')
            os.makedirs(CFG_DIR, exist_ok=True)
            im.save(tmp)
            cli = VLM(k, v['api_key'], v['base_url'], v['model'], timeout=60)
            d0, raw = cli.read(tmp)
            try:
                os.remove(tmp)
            except Exception:
                pass
            self.lbl_test.config(text='连接正常，模型已返回结果 ✔', style='Ok.TLabel')
        except Exception as e:
            self.lbl_test.config(text='失败：%s' % str(e)[:180], style='Err.TLabel')

    def _save(self):
        k, v = self._collect()
        self.cfg['engine'] = self.var_engine.get()
        self.cfg['vlm'] = v
        save_cfg(self.cfg)
        self.saved = True
        self.destroy()


class EditDialog(tk.Toplevel):
    def __init__(self, master, job: Job):
        super().__init__(master)
        self.title('核对 / 修改 - %s' % os.path.basename(job.img))
        self.resizable(False, False)
        self.job = job
        self.saved = False
        r = job.rec
        self.vars = {}
        frm = ttk.Frame(self, padding=12)
        frm.pack(fill='both', expand=True)
        ttk.Label(frm, text='字段', font=('', 9, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(frm, text='值', font=('', 9, 'bold')).grid(row=0, column=1, sticky='w')
        ttk.Label(frm, text='OCR 依据', font=('', 9, 'bold')).grid(row=0, column=2, sticky='w', padx=8)

        cur = {'km_start': r.km_start, 'km_end': r.km_end, 'side': r.side,
               'part_kind': '基础' if r.is_base else '墙身',
               'layer': r.layer if not r.is_base else '',
               'h_this': r.h_this, 'h_cum': r.h_cum, 'volume': r.volume,
               'strength': r.strength, 'w_top': r.w_top, 'w_bot': r.w_bot,
               'q_height': r.q_height, 'date': r.date, 'reporter': r.reporter,
               'team': r.team, 'period': r.period}
        for i, (key, label, typ) in enumerate(EDIT_FIELDS, start=1):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky='w', pady=2)
            val = cur.get(key)
            if isinstance(typ, tuple):
                v = tk.StringVar(value=typ[0] if val else (typ[0] if key == 'part_kind' else ''))
                w = ttk.Combobox(frm, textvariable=v, values=list(typ), width=18, state='readonly')
            else:
                v = tk.StringVar(value='' if val is None else str(val))
                w = ttk.Entry(frm, textvariable=v, width=20)
            w.grid(row=i, column=1, sticky='w', pady=2)
            self.vars[key] = v
            ev = r.evidence.get(label.replace('(m)', '').replace('(m³)', '').strip(), '')
            ttk.Label(frm, text=ev[:46], foreground='#6b7280').grid(row=i, column=2, sticky='w', padx=8)

        row = len(EDIT_FIELDS) + 2
        ttk.Separator(frm).grid(row=row, column=0, columnspan=3, sticky='ew', pady=8)
        self.lbl = ttk.Label(frm, text='', wraplength=520, justify='left')
        self.lbl.grid(row=row + 1, column=0, columnspan=3, sticky='w')
        btns = ttk.Frame(frm)
        btns.grid(row=row + 2, column=0, columnspan=3, sticky='e', pady=(10, 0))
        ttk.Button(btns, text='保存并重新定位', command=self.on_ok).pack(side='right')
        ttk.Button(btns, text='取消', command=self.destroy).pack(side='right', padx=6)
        self._refresh_hint()

    def _refresh_hint(self):
        j = self.job
        txt = []
        if j.block:
            txt.append('定位：%s（第%d-%d行）%s' % (j.block.km_text, j.block.row, j.block.end, j.locate_msg))
        if j.part:
            txt.append('目标部位：%s  %s' % (j.part.label, j.part_msg))
        if j.notes:
            txt.append('提示：' + '；'.join(j.notes))
        self.lbl.config(text='\n'.join(txt) or '（保存后重新定位）')

    def on_ok(self):
        r = self.job.rec
        g = self.vars

        def fnum(k):
            s = g[k].get().strip()
            if not s:
                return None
            try:
                return float(s)
            except ValueError:
                return None
        r.km_start = fnum('km_start')
        r.km_end = fnum('km_end')
        r.side = g['side'].get().strip()
        pk = g['part_kind'].get()
        r.is_base = (pk == '基础')
        try:
            lay = g['layer'].get().strip()
            r.layer = int(float(lay)) if lay else (0 if r.is_base else None)
        except ValueError:
            r.layer = 0 if r.is_base else None
        r.h_this = fnum('h_this')
        r.h_cum = fnum('h_cum')
        r.volume = fnum('volume')
        r.strength = g['strength'].get().strip()
        r.w_top = fnum('w_top')
        r.w_bot = fnum('w_bot')
        r.q_height = fnum('q_height')
        r.date = g['date'].get().strip()
        r.reporter = g['reporter'].get().strip()
        r.team = g['team'].get().strip()
        r.period = g['period'].get().strip()
        r.warnings = []
        if r.km_start is None:
            r.warnings.append('缺少里程')
        if not r.is_base and not r.layer:
            r.warnings.append('缺少层号')
        if r.h_this is None:
            r.warnings.append('缺少本次浇筑高度')
        if r.volume is None:
            r.warnings.append('缺少方量')
        if not r.date:
            r.warnings.append('缺少日期')
        self.saved = True
        self.destroy()


def _log_path(name):
    import sys
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    try:
        return os.path.join(base, name)
    except Exception:
        return os.path.join(os.environ.get('TEMP', '.'), name)


def main():
    import sys
    if '--selftest' in sys.argv:
        _selftest()
        return
    files = [a for a in sys.argv[1:]
             if os.path.isfile(a) and a.lower().endswith(IMG_EXT)]

    def crash(t, v, tb):
        try:
            import traceback
            with open(_log_path('crash.log'), 'a', encoding='utf-8') as f:
                f.write(''.join(traceback.format_exception(t, v, tb)))
        except Exception:
            pass

    sys.excepthook = crash
    _T('main entered')
    try:
        app = App(initial_files=files)
        app.report_callback_exception = crash
        _T('before mainloop')
        app.mainloop()
    except Exception:
        crash(*sys.exc_info())
        raise


def _selftest():
    """打包后自检：跑一遍 OCR + 解析，结果写入 selftest.log（无控制台时用）"""
    import sys
    log = os.path.join(os.path.dirname(sys.executable), 'selftest.log')
    lines = []
    try:
        lines.append('frozen=%s' % getattr(sys, 'frozen', False))
        from rapidocr_onnxruntime import RapidOCR
        eng = RapidOCR()
        lines.append('OCR init OK')
        img = None
        for i, a in enumerate(sys.argv):
            if a == '--selftest' and i + 1 < len(sys.argv):
                img = sys.argv[i + 1]
        if img and os.path.exists(img):
            res, _ = eng(img)
            txt = '\n'.join(t for _b, t, _c in res) if res else ''
            lines.append('OCR text: ' + txt.replace('\n', ' | '))
            r = parse(txt)
            lines.append('parsed: km=%s part=%s h=%s vol=%s date=%s' %
                         (r.km_text, r.part_name, r.h_this, r.volume, r.date))
        else:
            lines.append('no image given')
    except Exception:
        lines.append('ERROR:\n' + traceback.format_exc())
    with open(log, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


if __name__ == '__main__':
    main()
