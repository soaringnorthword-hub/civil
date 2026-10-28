# -*- coding: utf-8 -*-
"""视觉大模型（VLM）识别：把手持标识牌照片读成结构化字段。

为什么需要它：RapidOCR 用的是印刷体轻量模型，对手写基本无能力——实测两张
真实现场标识牌，印刷表头置信度 0.99+，手写部分 0.51~0.79 且数字小数点必丢
（1.96 读成 96）。放大 3 倍 / CLAHE / 锐化实测均无改善。

这里用 OpenAI 兼容的 chat/completions 接口，兼容通义千问、智谱、硅基流动、
OpenAI 及任何自建网关。
"""
import base64
import json
import os
import re
import time
import urllib.error
import urllib.request

PROVIDERS = {
    'dashscope': dict(label='通义千问 VL（阿里）',
                      base='https://dashscope.aliyuncs.com/compatible-mode/v1',
                      model='qwen-vl-max-latest'),
    'zhipu': dict(label='智谱 GLM-4V',
                  base='https://open.bigmodel.cn/api/paas/v4',
                  model='glm-4v-plus'),
    'siliconflow': dict(label='硅基流动 SiliconFlow',
                        base='https://api.siliconflow.cn/v1',
                        model='Qwen/Qwen2.5-VL-72B-Instruct'),
    'openai': dict(label='OpenAI',
                   base='https://api.openai.com/v1',
                   model='gpt-4o'),
    'custom': dict(label='自定义（OpenAI 兼容）', base='', model=''),
}

PROMPT = """这是一张施工现场手持的「MM项目影像资料采集标识牌」或「混凝土浇筑报检单」照片。
牌面表格内容为**手写**填写。请仔细辨认，只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。

JSON 字段（看不清的填 null，绝对不要猜）：
{
  "doc_type": "formwork" 表示模板报验牌，"pour" 表示混凝土浇筑报检单，判断不了填 "unknown",
  "km_start": "K28+035.819" 或 null,
  "km_end": "K28+106.33" 或 null,
  "side": "左侧" 或 "右侧" 或 null,
  "struct": 结构物名称原文，如 "衡重式路肩挡土墙"，或 null,
  "form_no": 第几模（整数，如牌上写"第一模"就填 1），或 null,
  "form_h0": 报验标高下限（数字，如 "（0-1.5）m" 里的 0），或 null,
  "form_h1": 报验标高上限（数字，如 "（0-1.5）m" 里的 1.5），或 null,
  "w_bot_design": 设计底宽（数字），或 null,
  "w_top_design": 设计顶宽（数字），或 null,
  "w_bot_meas": 实测底宽（数字），或 null,
  "w_top_meas": 实测顶宽（数字），或 null,
  "volume": 设计方量 m3（数字），或 null,
  "strength": 混凝土强度等级，如 "C25"，或 null,
  "check_result": 检查结论，如 "合格"，或 null,
  "inspector": 质检员姓名，或 null,
  "foreman": 施工员姓名，或 null,
  "date": 日期，统一成 "YYYY-MM-DD" 格式，或 null,
  "raw": "把牌面上所有文字按行抄录成一段字符串"
}

必须遵守：
1. 小数点绝不能丢。宽度合理范围 0.5~5 米，"1.96" 不许写成 196。
2. 里程严格按 K28+035.819 的写法，+ 后是三位整数加小数（035.819）。
3. 只认牌面上真正写了的内容，模糊不清一律 null。
4. 输出必须是能被 json.loads 解析的合法 JSON。"""


# ----------------------------------------------------------------- 配置读写
def cfg_path():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'TzLedger')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, 'config.json')


def load_cfg():
    try:
        with open(cfg_path(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_cfg(cfg):
    try:
        with open(cfg_path(), 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        return True
    except Exception:
        return False


# ------------------------------------------------------------------- 客户端
class VLMError(Exception):
    pass


def _b64(path, max_side=1600):
    """读图并压到长边 max_side，减少 token 与上传耗时"""
    try:
        from PIL import Image
        im = Image.open(path)
        if im.mode != 'RGB':
            im = im.convert('RGB')
        w, h = im.size
        if max(w, h) > max_side:
            r = max_side / float(max(w, h))
            im = im.resize((int(w * r), int(h * r)), Image.LANCZOS)
        import io as _io
        buf = _io.BytesIO()
        im.save(buf, format='JPEG', quality=88)
        data = buf.getvalue()
    except Exception:
        with open(path, 'rb') as f:
            data = f.read()
    return base64.b64encode(data).decode('ascii')


class VLM:
    def __init__(self, provider='dashscope', api_key='', base_url='', model='',
                 timeout=90, retries=2):
        p = PROVIDERS.get(provider) or PROVIDERS['custom']
        self.provider = provider
        self.base = (base_url or p['base']).rstrip('/')
        self.model = model or p['model']
        self.key = api_key
        self.timeout = timeout
        self.retries = retries

    @classmethod
    def from_cfg(cls, cfg=None):
        cfg = cfg or load_cfg()
        v = cfg.get('vlm') or {}
        return cls(provider=v.get('provider', 'dashscope'),
                   api_key=v.get('api_key', ''),
                   base_url=v.get('base_url', ''),
                   model=v.get('model', ''))

    @property
    def missing(self):
        """缺哪些必填项，用于给出精确提示（原来一律报"缺少 API Key"）"""
        m = []
        if not self.key:
            m.append('API Key')
        if not self.base:
            m.append('接口地址')
        if not self.model:
            m.append('模型名')
        return m

    @property
    def ready(self):
        return not self.missing

    def _post(self, payload):
        req = urllib.request.Request(
            self.base + '/chat/completions',
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + self.key},
            method='POST')
        ctx = None
        # 无视系统代理（本机 HTTP_PROXY 曾把 pip 卡死，同样会卡 API 请求）
        handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(handler)
        try:
            with opener.open(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode('utf-8', 'ignore'))
        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = e.read().decode('utf-8', 'ignore')[:300]
            except Exception:
                pass
            raise VLMError('HTTP %s %s\n%s' % (e.code, e.reason, body))
        except urllib.error.URLError as e:
            raise VLMError('网络不通：%s' % e.reason)

    def read(self, image_path):
        """返回 (dict 字段, 原始返回文本)"""
        miss = self.missing
        if miss:
            raise VLMError('配置不完整，还缺：%s' % '、'.join(miss))
        if not self.model.isascii():
            raise VLMError('模型名必须是英文 ID（如 qwen-vl-max），不能填中文名称：%s'
                           % self.model)
        if not self.base.startswith('http'):
            raise VLMError('接口地址必须以 http 开头：%s' % self.base)
        b64 = _b64(image_path)
        payload = {
            'model': self.model,
            'temperature': 0,
            'messages': [{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': PROMPT},
                    {'type': 'image_url',
                     'image_url': {'url': 'data:image/jpeg;base64,' + b64}},
                ],
            }],
        }
        last = None
        for i in range(self.retries + 1):
            try:
                resp = self._post(payload)
                txt = (resp.get('choices') or [{}])[0].get('message', {}).get('content', '')
                if isinstance(txt, list):
                    txt = ''.join(part.get('text', '') for part in txt
                                  if isinstance(part, dict))
                return _as_json(txt), txt
            except VLMError as e:
                last = e
                if i < self.retries:
                    time.sleep(1.5 * (i + 1))
            except Exception as e:
                last = VLMError(str(e))
                if i < self.retries:
                    time.sleep(1.5 * (i + 1))
        raise last


def _as_json(txt):
    """从模型输出里抠出 JSON（容忍 ```json 包裹、前后废话）"""
    if not txt:
        raise VLMError('模型返回空内容')
    s = txt.strip()
    s = re.sub(r'^```(?:json)?\s*', '', s)
    s = re.sub(r'\s*```$', '', s)
    try:
        return json.loads(s)
    except Exception:
        pass
    i, j = s.find('{'), s.rfind('}')
    if i >= 0 and j > i:
        try:
            return json.loads(s[i:j + 1])
        except Exception:
            pass
    raise VLMError('模型没有返回合法 JSON：%s' % txt[:160])


if __name__ == '__main__':
    import sys
    cfg = load_cfg()
    v = VLM.from_cfg(cfg)
    print('provider=%s model=%s ready=%s' % (v.provider, v.model, v.ready))
    if len(sys.argv) > 1 and v.ready:
        d, raw = v.read(sys.argv[1])
        print(json.dumps(d, ensure_ascii=False, indent=1))
