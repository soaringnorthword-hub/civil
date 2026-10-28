# -*- coding: utf-8 -*-
"""挡墙施工台账读写引擎。

台账结构（一工区施工台账（挡墙）.xlsx）：
- 每个挡墙浇筑块占若干行：块首行 A-H 有值且纵向合并，块内每行一个"部位"
- 部位行 I 列写「基础」「（第一层）墙身」…，未施工时 I 列有值、其余为空
- 部位行的 J..AA 各列都是独立单元格（不合并），可安全逐格写入
- U/V/W/X 是依赖 S 的公式，只需填 S，其余自动算

因此本引擎只做两件事：定位到正确的部位行，逐格写值。
"""
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime

import openpyxl
from openpyxl.utils import get_column_letter

DATE_FMT = 'yyyy"年"m"月"d"日";@'

# 列号
C_A, C_B, C_C, C_D, C_E, C_F, C_G = 1, 2, 3, 4, 5, 6, 7
C_H, C_I, C_J, C_K, C_L, C_M, C_N = 8, 9, 10, 11, 12, 13, 14
C_O, C_P, C_Q, C_R, C_S, C_T = 15, 16, 17, 18, 19, 20
C_U, C_V, C_W, C_X, C_Y, C_Z, C_AA = 21, 22, 23, 24, 25, 26, 27

KM_RE = re.compile(r'[Kk]?\s*(\d{1,3})\s*\+\s*(\d{1,3}(?:\.\d{1,3})?)')
CN = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8,
      '九': 9, '十': 10, '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15}


def _s(v):
    if v is None:
        return ''
    if isinstance(v, datetime):
        return v.strftime('%Y-%m-%d')
    return str(v).strip()


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_km_range(text):
    """'K27+562.428-K27+567.473' -> (27562.428, 27567.473)"""
    if not text:
        return None, None
    t = text.replace('～', '-').replace('~', '-').replace('—', '-').replace('–', '-')
    vals = []
    for m in KM_RE.finditer(t):
        vals.append(round(int(m.group(1)) * 1000 + float(m.group(2)), 3))
    if not vals:
        return None, None
    if len(vals) == 1:
        return vals[0], None
    a, b = vals[0], vals[1]
    return (a, b) if a <= b else (b, a)


def fmt_km(v):
    if v is None:
        return ''
    km = int(v // 1000)
    return 'K%d+%07.3f' % (km, v - km * 1000)


def part_layer(i_text):
    """从「（第二层）墙身」推出 (kind, layer)
    kind: 'expand'(扩大基础) | 'slab'(托梁/垫层) | 'base'(基础) | 'body'(墙身) | 'other'
    """
    t = i_text.replace('（', '(').replace('）', ')')
    if '扩大基础' in t:
        return 'expand', None
    if '托梁' in t or '垫层' in t:
        return 'slab', None
    m = re.search(r'第\s*([一二三四五六七八九十\d]{1,3})\s*层', t)
    g = m.group(1) if m else None
    n = None
    if g:
        n = int(g) if g.isdigit() else CN.get(g)
    if '基础' in t:
        return 'base', n
    if '墙身' in t:
        return 'body', n
    return 'other', None


@dataclass
class Part:
    row: int
    i_text: str
    kind: str
    layer: int
    filled: bool          # 是否已登记（K 有值 或 S 有值）
    k: float = None       # 开累高度
    j: float = None       # 本次浇筑
    m: float = None       # 墙高H
    s: float = None       # 设计方量

    @property
    def label(self):
        return '第%d行 %s%s' % (self.row, self.i_text, '' if not self.filled else '（已填）')


@dataclass
class Block:
    row: int                    # 块首行
    end: int                    # 块末行（含）
    a: float
    b: float
    c_text: str
    side: str
    struct: str
    g_text: str
    team: str
    parts: list = field(default_factory=list)

    @property
    def length(self):
        if self.a is None or self.b is None:
            return None
        return round(self.b - self.a, 3)

    @property
    def km_text(self):
        return '%s～%s' % (fmt_km(self.a), fmt_km(self.b))

    def base_h(self):
        """基础累计厚度：取所有已填基础行 M（墙高H）的最大值，即最上层基础顶面标高"""
        vals = [p.m for p in self.parts if p.kind == 'base' and p.m]
        return max(vals) if vals else None

    def prev_base_m(self, row):
        """指定基础行之前已完成的累计基础厚度"""
        vals = [p.m for p in self.parts
                if p.kind == 'base' and p.m and p.row < row]
        return max(vals) if vals else 0.0

    def find_part(self, kind, layer):
        """按部位类型+层号定位行。
        返回 (Part, 说明)；找不到返回 (None, 原因)
        """
        cand = [p for p in self.parts if p.kind == kind]
        if kind in ('base', 'body'):
            if layer:
                exact = [p for p in cand if p.layer == layer]
                if exact:
                    empty = [p for p in exact if not p.filled]
                    return (empty[0] if empty else exact[0]), '按第%d层精确匹配' % layer
                if kind == 'body':
                    if len(cand) >= layer:
                        return cand[layer - 1], '⚠ 未找到第%d层，按顺序推断' % layer
                    return None, '该块只预留 %d 层墙身，报检为第 %d 层，需手工加行' % (len(cand), layer)
            empty = [p for p in cand if not p.filled]
            if empty:
                return empty[0], '按首个未填行'
            return (cand[-1] if cand else None), ('⚠ 该块%s行已全部填满' % ('基础' if kind == 'base' else '墙身'))
        if cand:
            return cand[0], '同类型首行'
        return None, '未找到对应部位行'


class Ledger:
    def __init__(self, path):
        self.path = path
        self.wb_v = None      # data_only=True，用于读值
        self.wb_f = None      # data_only=False，用于写入
        self.ws_v = None
        self.ws_f = None
        self.blocks = []
        self.load()

    # ------------------------------------------------------------------ load
    def load(self):
        if not os.path.exists(self.path):
            raise FileNotFoundError(self.path)
        self.wb_v = openpyxl.load_workbook(self.path, data_only=True)
        self.wb_f = openpyxl.load_workbook(self.path, data_only=False)
        self.ws_v = self.wb_v.worksheets[0]
        self.ws_f = self.wb_f.worksheets[0]
        self._scan()

    def _scan(self):
        wsv = self.ws_v
        maxr = wsv.max_row
        heads = []
        for r in range(3, maxr + 1):
            if wsv.cell(r, C_A).value not in (None, ''):
                heads.append(r)
        self.blocks = []
        for idx, h in enumerate(heads):
            end = (heads[idx + 1] - 1) if idx + 1 < len(heads) else maxr
            ctext = _s(wsv.cell(h, C_C).value)
            a, b = parse_km_range(ctext)
            if a is None:
                # C 列缺失时退回用 A 列描述
                a, b = parse_km_range(_s(wsv.cell(h, C_A).value))
            blk = Block(row=h, end=end, a=a, b=b, c_text=ctext,
                        side=_s(wsv.cell(h, C_B).value),
                        struct=_s(wsv.cell(h, C_D).value),
                        g_text=_s(wsv.cell(h, C_G).value),
                        team=_s(wsv.cell(h, C_Y).value))
            for r in range(h, end + 1):
                it = _s(wsv.cell(r, C_I).value)
                if not it:
                    continue
                kind, layer = part_layer(it)
                if kind == 'other' and ('地基' in it or '承载力' in it or '/' == it):
                    continue
                k = _f(wsv.cell(r, C_K).value)
                s = _f(wsv.cell(r, C_S).value)
                blk.parts.append(Part(row=r, i_text=it, kind=kind, layer=layer,
                                      filled=(k is not None and k > 0) or s is not None,
                                      k=k, j=_f(wsv.cell(r, C_J).value),
                                      m=_f(wsv.cell(r, C_M).value), s=s))
            self.blocks.append(blk)
        self.blocks = [b for b in self.blocks if b.a is not None]

    # --------------------------------------------------------------- locate
    def locate(self, km_start, km_end=None, tol=0.05):
        """按里程定位挡墙块，返回 (Block|None, 说明, candidates)

        严格模式：只有「唯一完全包含」才自动定位。跨多块、部分重叠、里程不存在、
        并行重复段，一律返回 None + 候选清单，由人工指定，绝不自动猜。
        candidates: [(Block, 关系文字, 数值)]
        """
        if km_start is None:
            return None, '记录没有里程，无法定位', []
        lo = km_start
        hi = km_end if km_end and km_end > km_start else km_start
        contains, overlap, nearby = [], [], []
        for b in self.blocks:
            if b.b is None:
                continue
            if b.a - tol <= lo and hi <= b.b + tol:
                contains.append(b)
            else:
                ov = min(hi, b.b) - max(lo, b.a)
                if ov > 0.01:
                    overlap.append((ov, b))
                else:
                    gap = min(abs(lo - b.b), abs(b.a - hi))
                    if gap <= 40:
                        nearby.append((gap, b))
        if len(contains) == 1:
            return contains[0], '里程落在块内', [(contains[0], '完全包含', round(hi - lo, 3))]
        if contains:
            contains.sort(key=lambda b: (b.a, b.b))
            cands = [(b, '完全包含', round(hi - lo, 3)) for b in contains]
            # 并行重复段：两块区间互相重叠 → 台账里存在两套同名段，必须人工选
            par = 0
            for i in range(len(contains)):
                for j in range(i + 1, len(contains)):
                    ov = min(contains[i].b, contains[j].b) - max(contains[i].a, contains[j].a)
                    if ov > 0.5:
                        par += 1
            if par:
                return None, ('该里程在台账中存在并行重复段（共 %d 块重叠），'
                              '必须人工指定具体块' % len(contains)), cands
            return None, ('牌里程跨 %d 个挡墙块、总长 %.2fm，不自动选择，请人工指定'
                          % (len(contains), hi - lo)), cands
        if overlap:
            overlap.sort(key=lambda x: -x[0])
            cands = [(b, '部分重叠 %.3fm' % ov, ov) for ov, b in overlap[:8]]
            return None, ('里程与块仅部分重叠（%.3fm），不可信，请人工指定'
                          % overlap[0][0]), cands
        if nearby:
            nearby.sort(key=lambda x: x[0])
            cands = [(b, '距离 %.3fm' % g, g) for g, b in nearby[:5]]
            return None, ('里程不在任何块内，最近的块差 %.3fm，请人工指定'
                          % nearby[0][0]), cands
        return None, '台账里找不到相邻的挡墙块', []

    # ---------------------------------------------------------------- write
    def build_values(self, blk, part, rec):
        """算出要写入各列的值。返回 (dict 列->值, 说明list)"""
        notes = []
        base_h = blk.base_h()
        if base_h is None:
            base_h = 0.4
            notes.append('台账未登记基础厚度，M（墙高H）按基础 0.40m 估算')

        j = rec.get('h_this')
        k_in = rec.get('h_cum')

        l_text, m_val = '', None
        if part.kind == 'base':
            # 基础行：K（墙身开累）恒为 0；M = 基础累计厚度；L = 基础上一顶面-本次顶面
            k_prev = 0.0
            k_new = 0.0
            pm = blk.prev_base_m(part.row)
            if j is not None:
                m_val = round(pm + j, 3)
                l_text = '基础%g-%g' % (pm, m_val)
            if k_in is not None and j is not None and abs(k_in - m_val) > 0.01:
                notes.append('单据累计高度 %.2f 与基础累计厚 %.2f 不一致，按本次高度累加' % (k_in, m_val))
        else:
            prev = [p for p in blk.parts
                    if p.kind == 'body' and p.row < part.row and p.k is not None]
            prev.sort(key=lambda p: p.row)
            k_prev = prev[-1].k if prev else 0.0
            if k_in is not None and j is not None and abs(k_in - (k_prev + j)) > 0.01:
                notes.append('单据累计高度 %.2f 与推算 %.2f 不一致，以单据为准' % (k_in, k_prev + j))
                k_new = k_in
            elif k_in is not None and j is None:
                k_new = k_in
            elif j is not None:
                k_new = round(k_prev + j, 3)
            else:
                k_new = None
            l_text = '墙身%g-%g' % (k_prev, k_new if k_new is not None else k_prev)
            if k_new is not None:
                m_val = round((base_h or 0.0) + k_new, 3)

        vals = {
            C_J: j,
            C_K: k_new,
            C_L: l_text,
            C_M: m_val,
            C_N: rec.get('date') or None,
            C_O: rec.get('w_top'),
            C_P: rec.get('w_bot'),
            C_Q: rec.get('q_height') if rec.get('q_height') else j,
            C_R: rec.get('strength') or None,
            C_S: rec.get('volume'),
            C_T: rec.get('date') or None,
            C_Z: rec.get('period') or None,
            C_AA: rec.get('reporter') or None,
        }
        if not rec.get('w_top') or not rec.get('w_bot'):
            notes.append('顶宽/底宽为图纸设计值，需按工点图核对')

        # ---- 模板报验牌：牌上没有方量，按梯形断面反算 ----
        if rec.get('doc_type') == 'formwork':
            w_top = rec.get('w_top')
            w_bot = rec.get('w_bot')
            hgt = rec.get('q_height') or j
            L = blk.length
            if w_top and w_bot and hgt and L:
                s_calc = round((w_top + w_bot) / 2.0 * hgt * L, 2)
                vals[C_S] = s_calc
                notes.append('方量按梯形断面反算：(顶宽%.2f+底宽%.2f)/2×高%.2f×块长%.2f = %.2f m³'
                             % (w_top, w_bot, hgt, L, s_calc))
                notes.append('反算值属预估值，与台账设计断面方量可能差 5~15%，浇筑后请按报检单校正')
            else:
                vals.pop(C_S, None)
                notes.append('缺顶宽/底宽/高/块长，无法反算方量，S 列留空')
            # 模板报验日期填 N（施工日期），不填 T（浇筑日期）
            vals[C_T] = None
            if rec.get('form_h0') is not None:
                notes.append('报验标高 %.2f~%.2f m，按第%d层墙身登记'
                             % (rec['form_h0'], rec['form_h1'], part.layer or 0))
            if rec.get('check_result'):
                notes.append('检查结论：%s' % rec['check_result'])
            who = [x for x in (rec.get('inspector'), rec.get('foreman')) if x]
            if who:
                notes.append('质检员/施工员：%s（台账无对应列，未写入）' % '、'.join(who))
            if rec.get('w_top_meas') or rec.get('w_bot_meas'):
                notes.append('实测顶宽 %s / 底宽 %s（台账无实测列，仅记录）'
                             % (rec.get('w_top_meas') or '-', rec.get('w_bot_meas') or '-'))
        return {k: v for k, v in vals.items() if v not in (None, '')}, notes

    def plan(self, items):
        """items: [(blk, part, rec)] -> [(blk, part, rec, vals, notes)]
        同一块内按行号升序依次计算，前一条的计算结果作为后一条的推算起点。
        """
        items = sorted(items, key=lambda x: (x[0].row, x[1].row))
        out = []
        for blk, part, rec in items:
            vals, notes = self.build_values(blk, part, rec)
            if C_K in vals:
                part.k = vals[C_K]
            if C_M in vals:
                part.m = vals[C_M]
            if C_J in vals:
                part.j = vals[C_J]
            part.filled = True
            out.append((blk, part, rec, vals, notes))
        return out

    def backup(self):
        bak = self.path + '.bak'
        i = 1
        while os.path.exists(bak):
            bak = '%s.bak%d' % (self.path, i)
            i += 1
        shutil.copy(self.path, bak)
        return bak

    def apply(self, jobs, allow_overwrite=False):
        """jobs: [(Block, Part, rec_dict)]；返回 (成功数, 问题list)"""
        from datetime import datetime as _dt
        issues = []
        done = 0
        for blk, part, rec, vals, notes in jobs:
            if not allow_overwrite:
                occ = [c for c in (C_J, C_K, C_S)
                       if self.ws_f.cell(part.row, c).value not in (None, '')]
                if occ:
                    issues.append('第%d行 %s：已有数据，已跳过（需覆盖请勾选“允许覆盖”）'
                                  % (part.row, part.i_text))
                    continue
            kept = []
            for col, val in vals.items():
                cell = self.ws_f.cell(part.row, col)
                # 台账里 K/M/Q/U/V/W/X 大量是公式（=K212+J213 / =M210+J211 / =J166 …），
                # 写值会把公式删掉、联动失效。凡是公式一律保留。
                if isinstance(cell.value, str) and cell.value.startswith('='):
                    kept.append(openpyxl.utils.get_column_letter(col))
                    continue
                if col in (C_N, C_T):
                    if isinstance(val, str) and re.match(r'^\d{4}-\d{2}-\d{2}$', val):
                        y, m, d = [int(x) for x in val.split('-')]
                        cell.value = date(y, m, d)
                        cell.number_format = DATE_FMT
                    else:
                        cell.value = val
                else:
                    cell.value = val
            # 班组：块级，只在块内为空时补
            if rec.get('team'):
                ycell = self.ws_f.cell(blk.row, C_Y)
                if ycell.value in (None, ''):
                    ycell.value = rec['team']
            done += 1
            if kept:
                issues.append('第%d行 %s：%s 列是公式，已保留并由表格自动联动'
                              % (part.row, part.i_text, '/'.join(kept)))
            for n in notes:
                issues.append('第%d行 %s：%s' % (part.row, part.i_text, n))
        return done, issues

    def save(self, path=None):
        target = path or self.path
        self.wb_f.save(target)
        return target

    def reload(self):
        self.load()
