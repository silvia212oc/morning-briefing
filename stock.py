"""精材（3374）等個股的「主力進出 / 分點排行」抓取與分析。

資料來源：台灣證券交易所 BSR 系統（https://bsr.twse.com.tw）。
此系統僅提供「當日」的券商分點買賣資料，且需要圖形驗證碼。
我們用 ddddocr 自動辨識驗證碼，抓下當日資料後：
  1. 計算買方/賣方分點買賣超排行
  2. 用「同日高週轉」邏輯標記疑似隔日沖分點
  3. 把每日快照存進 Gist，往後可累積出多日（5/10/20...）排行

注意：BSR 沒有歷史，「天數累積」只能從開始記錄那天起往後累積。
"""

import io
import csv
import json
import os
import re
import time

import requests

BSR_BASE = 'https://bsr.twse.com.tw/bshtm/'
BSR_MENU = BSR_BASE + 'bsMenu.aspx'

# 想追蹤的股票清單（股號 -> 名稱）。要加股票就在這裡加一筆。
WATCH_STOCKS = {
    '3374': '精材',
}

# 排行顯示前幾名
TOP_N = 6
# 疑似隔日沖判定：當日同時大買又大賣（週轉），且雙邊量都達門檻（張）
DAYTRADE_MIN_LOTS = 100
# 雙邊比值越接近 1 越像當沖/隔日沖
DAYTRADE_RATIO = 0.5


def _fetch_bsr_csv(stock_id, max_retries=6):
    """從 BSR 系統抓取某股票當日的分點 CSV 原始文字。失敗回傳 None。"""
    try:
        import ddddocr
    except Exception as e:
        print(f'[stock] ddddocr 載入失敗：{e}')
        return None

    ocr = ddddocr.DdddOcr(show_ad=False)

    for attempt in range(1, max_retries + 1):
        try:
            session = requests.Session()
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)',
            })
            resp = session.get(BSR_MENU, timeout=20)
            html = resp.text
            print(f'[stock] {stock_id} 第{attempt}次 BSR首頁 HTTP={resp.status_code} 長度={len(html)}')

            # 取出 ASP.NET 表單隱藏欄位（name= 或 id= 都可能）
            def hidden(name):
                m = re.search(
                    r'(?:id|name)="%s"[^>]*value="([^"]*)"' % re.escape(name), html
                ) or re.search(
                    r'value="([^"]*)"[^>]*(?:id|name)="%s"' % re.escape(name), html
                )
                return m.group(1) if m else ''

            viewstate = hidden('__VIEWSTATE')
            viewstate_gen = hidden('__VIEWSTATEGENERATOR')
            eventvalidation = hidden('__EVENTVALIDATION')

            # 取出驗證碼圖片網址（支援不同格式）
            m = re.search(r'(CaptchaImage\.aspx[^"\'>\s]*)', html)
            if not m:
                print(f'[stock] {stock_id} 第{attempt}次 找不到驗證碼圖片網址，跳過')
                # 輸出部分 HTML 幫助診斷
                if attempt == 1:
                    print(f'[stock] HTML前500字：{html[:500]}')
                continue
            captcha_url = BSR_BASE + m.group(1)
            img = session.get(captcha_url, timeout=20).content
            code = ocr.classification(img)
            code = re.sub(r'[^A-Za-z0-9]', '', code)
            print(f'[stock] {stock_id} 第{attempt}次 OCR驗證碼="{code}"')
            if len(code) < 4:
                print(f'[stock] {stock_id} 第{attempt}次 驗證碼太短，跳過')
                continue

            payload = {
                '__VIEWSTATE': viewstate,
                '__VIEWSTATEGENERATOR': viewstate_gen,
                '__EVENTVALIDATION': eventvalidation,
                'RadioButton_Normal': 'RadioButton_Normal',
                'TextBox_Stkno': stock_id,
                'CaptchaControl1': code,
                'btnOK': '查詢',
            }
            r2 = session.post(BSR_MENU, data=payload, timeout=20)
            page = r2.text
            print(f'[stock] {stock_id} 第{attempt}次 POST HTTP={r2.status_code} 回應長度={len(page)}')

            # 成功的話頁面會帶一個 CSV 下載連結
            m2 = re.search(r'href="([^"]*\.csv[^"]*)"', page, re.IGNORECASE)
            if not m2:
                # 找不到下載連結，也可能是驗證碼錯誤
                err_m = re.search(r'(驗證碼|錯誤|error|invalid)', page, re.IGNORECASE)
                reason = err_m.group(0) if err_m else '找不到CSV連結'
                print(f'[stock] {stock_id} 第{attempt}次 {reason}，重試')
                if attempt == 1:
                    print(f'[stock] POST回應前300字：{page[:300]}')
                continue
            csv_href = m2.group(1)
            if not csv_href.startswith('http'):
                csv_url = BSR_BASE + csv_href.lstrip('./')
            else:
                csv_url = csv_href
            print(f'[stock] {stock_id} 第{attempt}次 CSV網址={csv_url}')
            csv_resp = session.get(csv_url, timeout=20)
            csv_resp.encoding = 'big5'
            text = csv_resp.text
            print(f'[stock] {stock_id} 第{attempt}次 CSV長度={len(text)} 前100字：{text[:100]}')
            if '券商' in text or (',' in text and len(text) > 100):
                return text
            print(f'[stock] {stock_id} 第{attempt}次 CSV內容異常，重試')
        except Exception as e:
            print(f'[stock] {stock_id} 第{attempt}次 例外：{e}')
            time.sleep(1)
            continue
    print(f'[stock] {stock_id} 所有嘗試均失敗')
    return None


def _parse_bsr_csv(text):
    """解析 BSR CSV，彙總每個券商分點的買進/賣出股數。

    BSR 的 CSV 一列同時含左右兩組（序號,券商,價格,買進股數,賣出股數）x2。
    回傳：{券商名: {'buy': 股數, 'sell': 股數}}
    """
    brokers = {}
    reader = csv.reader(io.StringIO(text))
    for row in reader:
        cells = [c.strip() for c in row]
        # 嘗試左右兩個半段
        for offset in (0, 5):
            chunk = cells[offset:offset + 5]
            if len(chunk) < 5:
                continue
            _, name, _price, buy_s, sell_s = chunk
            name = re.sub(r'^\d+\s*', '', name).strip()
            if not name or name in ('券商', '證券'):
                continue
            try:
                buy = int(buy_s.replace(',', '')) if buy_s else 0
                sell = int(sell_s.replace(',', '')) if sell_s else 0
            except ValueError:
                continue
            if buy == 0 and sell == 0:
                continue
            agg = brokers.setdefault(name, {'buy': 0, 'sell': 0})
            agg['buy'] += buy
            agg['sell'] += sell
    return brokers


def _analyze(brokers):
    """把券商彙總轉成排行 + 隔日沖標記。股數轉張（/1000）。"""
    rows = []
    for name, v in brokers.items():
        buy_lots = v['buy'] / 1000
        sell_lots = v['sell'] / 1000
        net = buy_lots - sell_lots
        total = buy_lots + sell_lots
        # 疑似隔日沖：雙邊量都大且接近（高週轉、淨額相對小）
        is_daytrade = (
            min(buy_lots, sell_lots) >= DAYTRADE_MIN_LOTS
            and total > 0
            and (min(buy_lots, sell_lots) / max(buy_lots, sell_lots)) >= DAYTRADE_RATIO
        )
        rows.append({
            'name': name,
            'buy': round(buy_lots),
            'sell': round(sell_lots),
            'net': round(net),
            'daytrade': is_daytrade,
        })

    buyers = sorted([r for r in rows if r['net'] > 0], key=lambda x: -x['net'])[:TOP_N]
    sellers = sorted([r for r in rows if r['net'] < 0], key=lambda x: x['net'])[:TOP_N]
    return {'buyers': buyers, 'sellers': sellers}


def fetch_stock(stock_id):
    """抓取並分析單一股票的當日分點資料。失敗回傳 None。"""
    text = _fetch_bsr_csv(stock_id)
    if not text:
        print(f'[stock] {stock_id} 抓取 CSV 失敗')
        return None
    brokers = _parse_bsr_csv(text)
    if not brokers:
        print(f'[stock] {stock_id} 解析分點失敗，CSV 前200字：{text[:200]}')
        return None
    result = _analyze(brokers)
    result['stock_id'] = stock_id
    result['name'] = WATCH_STOCKS.get(stock_id, stock_id)
    print(f'[stock] {stock_id} 成功：買超{len(result["buyers"])}筆 賣超{len(result["sellers"])}筆')
    return result


# ---- 歷史累積（存在 Gist 裡）----

HISTORY_FILE = 'broker-history.json'


def load_history():
    """從 Gist 讀取歷史快照。回傳 dict：{stock_id: {date: result}}。"""
    gist_id = os.environ.get('GIST_ID')
    token = os.environ.get('GIST_TOKEN')
    if not gist_id or not token:
        return {}
    try:
        resp = requests.get(
            f'https://api.github.com/gists/{gist_id}',
            headers={'Authorization': f'token {token}'},
            timeout=15,
        )
        files = resp.json().get('files', {})
        content = files.get(HISTORY_FILE, {}).get('content', '')
        return json.loads(content) if content else {}
    except Exception:
        return {}


def save_snapshot(history, stock_id, date_str, result):
    """把今天的快照加入歷史（記憶體中），並裁剪到最多 120 個交易日。"""
    per_stock = history.setdefault(stock_id, {})
    per_stock[date_str] = {'buyers': result['buyers'], 'sellers': result['sellers']}
    # 只保留最近 130 天，避免 Gist 無限膨脹
    dates = sorted(per_stock.keys())
    for old in dates[:-130]:
        per_stock.pop(old, None)
    return history


def history_file_payload(history):
    """回傳要寫回 Gist 的檔案內容（給 main.py 一起 PATCH）。"""
    return json.dumps(history, ensure_ascii=False, indent=1)


def accumulate_ranking(history, stock_id, days):
    """用歷史快照算出近 N 天的累積買賣超排行。資料不足就用現有天數。"""
    per_stock = history.get(stock_id, {})
    dates = sorted(per_stock.keys())[-days:]
    if not dates:
        return None
    net_by_broker = {}
    for d in dates:
        snap = per_stock[d]
        for side in ('buyers', 'sellers'):
            for r in snap.get(side, []):
                net_by_broker[r['name']] = net_by_broker.get(r['name'], 0) + r['net']
    rows = [{'name': n, 'net': v} for n, v in net_by_broker.items()]
    buyers = sorted([r for r in rows if r['net'] > 0], key=lambda x: -x['net'])[:TOP_N]
    sellers = sorted([r for r in rows if r['net'] < 0], key=lambda x: x['net'])[:TOP_N]
    return {'days': len(dates), 'buyers': buyers, 'sellers': sellers}
