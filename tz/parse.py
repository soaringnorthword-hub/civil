# -*- coding: utf-8 -*-
"""报检内容字段解析：把 OCR 出来的文字行解析成结构化记录。

设计原则：宁可留空也不猜错。每个字段都记录原文证据，供人工核对。
"""
import re
from dataclasses import dataclass, field, asdict
from datetime import date

CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6,
          '七': 7, '八': 8, '九': 9, '十': 10, '十一': 11, '十二': 12,
          '十三': 13, '十四': 14, '十五': 15, '十六': 16}

# 里程：K27+562.428 / k27+562 / 27+562.428
KM_RE = re.compile(r'[Kk]?\s*(\d{1,3})\s*\+\s*(\d{1,3}(?:\.\d{1,3})?)')
NUM = r'(\d+(?:\.\d+)?)'


def cn2int(s):
    s = s.strip()
    if s.isdigit():
        return int(s)
    if s in CN_NUM:
        return CN_NUM[s]
    m = re.fullmatch(r'十([一二三四五六七八九])', s)
    if m:
        return 10 + CN_NUM[m.group(1)]
    return None


def km2m(k, m):
    return round(int(k) * 1000 + float(m), 3)


def fmt_km(v):
    if v is None:
        return ''
    km = int(v // 1000)
    mm = v - km * 1000
    return 'K%d+%07.3f' % (km, mm)


def _num(s):
    try:
        return float(s)
    except Exception:
        return None


@dataclass
class Record:
    # 'pour' = 混凝土浇筑报检；'formwork' = 模板报验（影像采集标识牌）
    doc_type: str = 'pour'
    km_start: float = None
    km_end: float = None
    side: str = ''
    struct: str = ''
    part_raw: str = ''
    form_no: int = None        # 第一模 / 第二模 ...
    form_h0: float = None      # 报验标高下限（0-1.5 中的 0）
    form_h1: float = None      # 报验标高上限（0-1.5 中的 1.5）
    w_top_meas: float = None   # 实测顶宽
    w_bot_meas: float = None   # 实测底宽
    check_result: str = ''     # 检查结论：合格
    inspector: str = ''        # 质检员
    foreman: str = ''          # 施工员
    critical: bool = False     # 致命错误：禁止写入台账
    layer: int = None          # 0 = 基础；1..n = 墙身层号；None = 未识别
    is_base: bool = False
    h_this: float = None       # 本次浇筑高度（J 列）
    h_cum: float = None        # 累计高度（K 列）
    volume: float = None       # 设计方量（S 列）
    strength: str = ''         # C25
    w_top: float = None        # 顶宽（O 列）
    w_bot: float = None        # 底宽（P 列）
    q_height: float = None     # 高（Q 列）
    date: str = ''             # 浇筑日期 T 列 yyyy-MM-dd
    reporter: str = ''
    team: str = ''
    period: str = ''
    raw_text: str = ''
    evidence: dict = field(default_factory=dict)   # 字段 -> 原文证据
    warnings: list = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @property
    def km_text(self):
        if self.km_start is None:
            return ''
        if self.km_end is None:
            return fmt_km(self.km_start)
        return '%s～%s' % (fmt_km(self.km_start), fmt_km(self.km_end))

    @property
    def part_name(self):
        if self.is_base:
            return '基础'
        if self.layer:
            return '（第%d层）墙身' % self.layer
        return self.part_raw


def parse_date(t):
    """支持 2026-10-26 / 2026年10月26日 / 2026.10.26 / 10月26日"""
    t = t.replace('O', '0').replace('o', '0')
    m = re.search(r'(20\d{2})\s*[-年./]\s*(\d{1,2})\s*[-月./]\s*(\d{1,2})', t)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]', t)
        if not m:
            return ''
        y, mo, d = date.today().year, int(m.group(1)), int(m.group(2))
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return ''


def _grab(patterns, text, flags=0):
    """按顺序尝试正则，返回 (值, 匹配原文)"""
    for p in patterns:
        m = re.search(p, text, flags)
        if m:
            return m.group(1), m.group(0)
    return None, ''


def parse(text):
    """把 OCR 全文解析为 Record。text 可以是多行拼接。"""
    rec = Record()
    rec.raw_text = text
    t = text.replace('，', ',').replace('：', ':').replace('（', '(').replace('）', ')')
    # 归一化常见 OCR 误识别
    t = t.replace('m³', 'm3').replace('M³', 'm3')
    # 去掉单位括号，避免「方量(m3) 45.6」被数字 3 干扰
    t = re.sub(r'[（(]\s*(?:m\s*3|m3|方|立方米|立方)\s*[)）]', ' ', t, flags=re.I)
    flat = re.sub(r'[ \t]+', ' ', t)
    oneline = flat.replace('\n', ' ')

    # ---- 文档类型：模板报验牌 vs 浇筑报检单 ----
    if re.search(r'模板\s*报验|模板报检|影像资料采集|检查内容|检查结论', flat):
        rec.doc_type = 'formwork'
    elif re.search(r'浇筑|浇注|砼|混凝土', flat):
        rec.doc_type = 'pour'

    # ---- 里程 ----
    kms = []
    for m in KM_RE.finditer(flat):
        v = km2m(m.group(1), m.group(2))
        if 1000 <= v <= 200000:
            kms.append(v)
    if kms:
        rec.km_start = kms[0]
        rec.km_end = kms[1] if len(kms) > 1 else None
        rec.evidence['里程'] = ' '.join(fmt_km(v) for v in kms[:2])
        if rec.km_end and rec.km_end < rec.km_start:
            rec.km_start, rec.km_end = rec.km_end, rec.km_start
    else:
        rec.warnings.append('未识别到里程（K??+???）')

    # ---- 左右侧 ----
    if '右侧' in flat or '右幅' in flat:
        rec.side = '右侧'
    elif '左侧' in flat or '左幅' in flat:
        rec.side = '左侧'

    # ---- 结构形式 ----
    for kw in ('桩基托梁衡重式路肩挡土墙', '衡重式路肩挡土墙', '衡重式路肩墙',
               '重力式路堑墙', '重力式路肩墙', '路堤墙', '护脚', '路肩墙', '路堑墙'):
        if kw in flat:
            rec.struct = kw
            break

    # ---- 部位 / 层号 ----
    m = re.search(r'第\s*([一二三四五六七八九十\d]{1,3})\s*层', flat)
    if m:
        rec.layer = cn2int(m.group(1))
        rec.part_raw = m.group(0)
    if '基础' in flat and '墙身' not in flat:
        rec.is_base = True
        rec.layer = 0
        rec.part_raw = rec.part_raw or '基础'
    elif '基础' in flat and re.search(r'基础.{0,6}(浇筑|报验|报检)', flat) and not m:
        rec.is_base = True
        rec.layer = 0
    if '墙身' in flat and not rec.is_base:
        rec.part_raw = rec.part_raw or '墙身'
    if rec.layer is None and not rec.is_base:
        rec.warnings.append('未识别到部位（基础/第N层墙身）')

    # ---- 本次浇筑高度 ----
    v, raw = _grab([r'本次(?:浇筑)?(?:高度|厚度|方量)?\D{0,8}?' + NUM + r'\s*(?:m|米)',
                    r'(?:浇筑|浇注)(?:高度|厚)\D{0,6}?' + NUM,
                    r'(?:高度|厚)\D{0,4}?' + NUM + r'\s*(?:m|米)'], oneline)
    if v:
        rec.h_this = _num(v)
        rec.evidence['本次浇筑高度'] = raw

    # ---- 累计高度 ----
    v, raw = _grab([r'(?:累计|开累|累计高度|开累高度)\D{0,8}?' + NUM,
                    r'累计\D{0,4}?' + NUM + r'\s*(?:m|米)'], oneline)
    if v:
        rec.h_cum = _num(v)
        rec.evidence['累计高度'] = raw

    # ---- 设计方量 ----
    v, raw = _grab([r'(?:设计方量|方量|砼量|混凝土量|数量|设计数量)\D{0,10}?' + NUM,
                    NUM + r'\s*(?:m3|方|立方)'], oneline)
    if v:
        rec.volume = _num(v)
        rec.evidence['设计方量'] = raw
    elif rec.doc_type != 'formwork':
        rec.warnings.append('未识别到方量')

    # ---- 强度等级 ----
    cand = None
    for m in re.finditer(r'(?<![A-Za-z])([CM])\s?(\d{2})', flat):
        n = int(m.group(2))
        if 15 <= n <= 60:
            cand = m
            break
    if cand:
        rec.strength = 'C%s' % cand.group(2)
        rec.evidence['强度'] = cand.group(0)
    elif rec.doc_type != 'formwork':
        rec.warnings.append('未识别到强度等级')

    # ---- 模板报验牌：实测顶/底宽（先抓，避免被设计值覆盖）----
    if rec.doc_type == 'formwork':
        v, raw = _grab([r'实测\s*顶\s*宽\D{0,4}?' + NUM], oneline)
        if v:
            rec.w_top_meas = _num(v)
            rec.evidence['实测顶宽'] = raw
        v, raw = _grab([r'实测\s*底\s*宽\D{0,4}?' + NUM], oneline)
        if v:
            rec.w_bot_meas = _num(v)
            rec.evidence['实测底宽'] = raw

    # ---- 设计尺寸 ----
    # (?<!实测) 防止把实测值当设计值
    v, raw = _grab([r'(?<!实测)(?<!试验)\s*顶\s*宽\D{0,4}?' + NUM], oneline)
    if v:
        rec.w_top = _num(v)
        rec.evidence['顶宽'] = raw
    v, raw = _grab([r'(?<!实测)(?<!试验)\s*底\s*宽\D{0,4}?' + NUM], oneline)
    if v:
        rec.w_bot = _num(v)
        rec.evidence['底宽'] = raw
    # 高：优先取「顶宽x 底宽y 高z」三元组里的第三个，避免命中"浇筑高度/累计高度"
    v, raw = _grab([r'底宽\D{0,3}?(?:\d+(?:\.\d+)?)\D{0,8}?(?:高|墙高)\D{0,3}?' + NUM], oneline)
    if v:
        rec.q_height = _num(v)
        rec.evidence['高'] = raw
    else:
        v, raw = _grab([r'(?<!浇筑)(?<!累计)(?<!本次)(?<!墙身)(?:墙高|净高|断面高|高)\s*[:：]?\s*' + NUM + r'\s*(?:m|米)'], oneline)
        if v:
            rec.q_height = _num(v)
            rec.evidence['高'] = raw

    # ---- 日期 ----
    m = re.search(r'(?:浇筑|施工|报验|报检)?\s*日期\D{0,4}([0-9]{4}\s*[-年./]\s*[0-9]{1,2}\s*[-月./]\s*[0-9]{1,2}|[0-9]{1,2}\s*月\s*[0-9]{1,2}\s*[日号])', oneline)
    if m:
        rec.date = parse_date(m.group(1))
        rec.evidence['日期'] = m.group(0)
    if not rec.date:
        rec.date = parse_date(flat)
    if not rec.date:
        rec.warnings.append('未识别到日期')

    # ---- 报检人 ----
    NOT_NAME = ('班组|施工|日期|部位|桩号|里程|方量|强度|时间|单位|工程|签名|签字|'
                '旁站|监理|现场|负责|记录|报验|报检|浇筑|检查')
    v, raw = _grab([r'报检人\s*[:：]\s*([\u4e00-\u9fa5]{2,4})',
                    r'报验人\s*[:：]\s*([\u4e00-\u9fa5]{2,4})',
                    r'报检人\s*([\u4e00-\u9fa5]{2,3})(?![\u4e00-\u9fa5])'], oneline)
    if v and not re.search(NOT_NAME, v):
        rec.reporter = v
        rec.evidence['报检人'] = raw

    # ---- 班组 ----
    v, raw = _grab([r'(?:班组|施工队|施工班组|队伍)\s*[:：]\s*([\u4e00-\u9fa5A-Za-z0-9]{2,14})',
                    r'(?:班组|施工队|施工班组)\s*([\u4e00-\u9fa5A-Za-z0-9]{2,14})'], oneline)
    if v:
        # 截掉后面粘上来的其它字段名
        for stop in ('计价', '报检', '监理', '日期', '桩号', '部位', '方量', '强度', '期次'):
            i = v.find(stop)
            if i > 0:
                v = v[:i]
        if len(v) >= 2:
            rec.team = v
            rec.evidence['班组'] = raw

    # ---- 计价期次 ----
    v, raw = _grab([r'第\s*([\d一二三四五六七八九十]{1,3})\s*期'], oneline)
    if v:
        n = cn2int(v)
        rec.period = '第%s期' % (n if n is not None else v)
        rec.evidence['计价期次'] = raw

    # ---- 模板报验牌：模数、报验标高、检查结论、质检员/施工员 ----
    if rec.doc_type == 'formwork':
        m = re.search(r'第\s*([一二三四五六七八九十\d]{1,2})\s*模', flat)
        if m:
            rec.form_no = cn2int(m.group(1))
            rec.evidence['模数'] = m.group(0)
        # 报验标高范围，如（0-1.5）m
        m = re.search(r'[(（]\s*(\d+(?:\.\d+)?)\s*[-~－—至]\s*(\d+(?:\.\d+)?)\s*[)）]\s*(?:m|米)?', flat)
        if m:
            a, b = float(m.group(1)), float(m.group(2))
            if 0 <= a < b <= 8:
                rec.form_h0, rec.form_h1 = a, b
                rec.evidence['报验标高'] = m.group(0)
                if rec.q_height is None:
                    rec.q_height = round(b - a, 3)
                if rec.h_this is None:
                    rec.h_this = rec.q_height
                if rec.layer is None:
                    rec.layer = rec.form_no
        v, raw = _grab([r'(合格|不合格|符合要求|通过)'], flat)
        if v:
            rec.check_result = v
            rec.evidence['检查结论'] = raw
        NOT_NAME2 = ('人员|质检|施工|监理|时间|日期|单位|工程|签名|签字|检查|结论')
        v, raw = _grab([r'质检员\s*[:：]?\s*([\u4e00-\u9fa5]{2,4})'], oneline)
        if v and not re.search(NOT_NAME2, v):
            rec.inspector = v
            rec.evidence['质检员'] = raw
        v, raw = _grab([r'施工员\s*[:：]?\s*([\u4e00-\u9fa5]{2,4})'], oneline)
        if v and not re.search(NOT_NAME2, v):
            rec.foreman = v
            rec.evidence['施工员'] = raw

    validate(rec)
    return rec


def validate(rec):
    """量纲校验 + 交叉校验。OCR 与视觉大模型两条路线共用，防丢小数点的错值入库。"""

    def _dim(name, v, lo, hi, unit):
        if v is None:
            return None
        if lo <= v <= hi:
            return v
        rec.warnings.append('%s=%.4g 超出合理范围 %g~%g%s（疑似丢小数点），已作废'
                            % (name, v, lo, hi, unit))
        rec.critical = True
        return None

    rec.w_top = _dim('顶宽', rec.w_top, 0.2, 8, 'm')
    rec.w_bot = _dim('底宽', rec.w_bot, 0.2, 8, 'm')
    rec.w_top_meas = _dim('实测顶宽', rec.w_top_meas, 0.2, 8, 'm')
    rec.w_bot_meas = _dim('实测底宽', rec.w_bot_meas, 0.2, 8, 'm')
    rec.q_height = _dim('高', rec.q_height, 0.05, 8, 'm')
    rec.h_this = _dim('本次高度', rec.h_this, 0.05, 8, 'm')
    rec.h_cum = _dim('累计高度', rec.h_cum, 0.05, 20, 'm')
    rec.volume = _dim('方量', rec.volume, 0.1, 500, 'm3')
    # 顶宽不应大于底宽太多（衡重式/重力式断面：顶宽 ≤ 底宽 + 0.5）
    if rec.w_top and rec.w_bot and rec.w_top > rec.w_bot + 0.6:
        rec.warnings.append('顶宽(%.2f) 明显大于底宽(%.2f)，疑似顶底宽识别颠倒'
                            % (rec.w_top, rec.w_bot))
    # 里程跨度
    if rec.km_start is not None and rec.km_end is not None:
        span = abs(rec.km_end - rec.km_start)
        if span > 500:
            rec.warnings.append('里程跨度 %.0fm 明显错误（单块仅 4~12m），已作废里程，禁止写入'
                                % span)
            rec.critical = True
            rec.km_start = rec.km_end = None
        elif span > 200:
            rec.warnings.append('里程跨度 %.0fm 异常，请核对' % span)
        elif span > 60:
            rec.warnings.append('里程跨 %.0fm，超过单个挡墙块长度，将跨多块' % span)
    if rec.h_this and rec.h_cum:
        if rec.h_cum < rec.h_this - 0.01:
            rec.warnings.append('累计高度(%.2f)小于本次高度(%.2f)' % (rec.h_cum, rec.h_this))
    if rec.h_this and rec.h_cum and rec.is_base:
        if abs(rec.h_cum - rec.h_this) > 0.01:
            rec.warnings.append('基础行的累计高度应等于本次高度')
    return rec


def _km_one(t):
    """'K28+035.819' -> 28035.819"""
    if t is None:
        return None
    m = re.match(r'\s*[Kk]?\s*(\d{1,3})\s*[+十]\s*(\d{1,3}(?:\.\d{1,3})?)\s*$', str(t))
    if not m:
        return None
    return km2m(m.group(1), m.group(2))


def from_vlm(d):
    """视觉大模型返回的 dict -> Record，走与 OCR 完全相同的校验闸门。"""
    rec = Record()
    if not isinstance(d, dict):
        rec.warnings.append('模型返回结构异常，已拒绝')
        rec.critical = True
        return rec
    dt = str(d.get('doc_type') or '').lower()
    rec.doc_type = ('formwork' if dt.startswith('form') else
                    'pour' if dt.startswith('pour') else 'unknown')
    rec.km_start = _km_one(d.get('km_start'))
    rec.km_end = _km_one(d.get('km_end'))
    sd = str(d.get('side') or '')
    rec.side = '右侧' if '右' in sd else ('左侧' if '左' in sd else '')
    rec.struct = str(d.get('struct') or '').strip()

    def n(key):
        v = d.get(key)
        if v in (None, '', 'null', 'None'):
            return None
        try:
            return float(re.sub(r'[^\d.\-]', '', str(v)))
        except Exception:
            return None

    rec.form_no = int(n('form_no')) if n('form_no') else None
    rec.form_h0 = n('form_h0')
    rec.form_h1 = n('form_h1')
    rec.w_top = n('w_top_design')
    rec.w_bot = n('w_bot_design')
    rec.w_top_meas = n('w_top_meas')
    rec.w_bot_meas = n('w_bot_meas')
    rec.volume = n('volume')
    rec.q_height = n('q_height')
    st = str(d.get('strength') or '').strip().upper()
    m = re.match(r'^([CM])\s*(\d{2})$', st)
    rec.strength = 'C%s' % m.group(2) if m else ''
    rec.check_result = str(d.get('check_result') or '').strip()
    rec.inspector = str(d.get('inspector') or '').strip()
    rec.foreman = str(d.get('foreman') or '').strip()
    ds = d.get('date')
    if ds:
        rec.date = parse_date(str(ds))
    rec.raw_text = str(d.get('raw') or '')

    # 部位/层号：模板报验牌只有「第N模」，直接当层号用（第一模 = 第一层墙身）
    if rec.form_no:
        rec.layer = rec.form_no
    if rec.q_height is None and rec.form_h0 is not None and rec.form_h1 is not None:
        rec.q_height = round(rec.form_h1 - rec.form_h0, 3)
    if rec.h_this is None:
        rec.h_this = rec.q_height
    if '基础' in rec.struct and '墙身' not in rec.struct:
        rec.is_base = True
        rec.layer = 0
    if rec.layer is None and not rec.is_base:
        rec.warnings.append('未识别到部位（基础/第N层墙身）')
    if rec.km_start is None:
        rec.warnings.append('未识别到里程（K??+???）')
    validate(rec)
    return rec


if __name__ == '__main__':
    demo = '''【报检】挡墙C25混凝土浇筑报验
桩号：K27+562.428～K27+567.473  右侧
部位：衡重式路肩墙 第二层墙身
本次浇筑高度：0.8m  累计高度：2.0m
设计方量：S=16.5m3  强度等级：C25
顶宽1.65m 底宽1.49m 高0.8m
浇筑日期：2026-10-26  报检人：张伟
班组：四川蓝拓  计价期次：第7期'''
    r = parse(demo)
    for k, v in r.to_dict().items():
        if k not in ('raw_text', 'evidence'):
            print('%-10s %r' % (k, v))
    print('evidence', r.evidence)
    print('warn', r.warnings)
    print('km', r.km_text, 'part', r.part_name)
