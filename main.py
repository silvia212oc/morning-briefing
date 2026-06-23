import os
import json
import requests
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import google.generativeai as genai

TWN = timezone(timedelta(hours=8))
WEEKDAYS = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']


def load_google_creds():
    token_data = json.loads(os.environ['GOOGLE_TOKEN_JSON'])
    creds = Credentials(
        token=token_data.get('token'),
        refresh_token=token_data.get('refresh_token'),
        token_uri=token_data.get('token_uri', 'https://oauth2.googleapis.com/token'),
        client_id=token_data.get('client_id'),
        client_secret=token_data.get('client_secret'),
        scopes=token_data.get('scopes'),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def get_gmail_threads(creds):
    service = build('gmail', 'v1', credentials=creds)
    result = service.users().threads().list(
        userId='me', q='is:unread newer_than:1d', maxResults=10
    ).execute()

    emails = []
    for thread in result.get('threads', [])[:8]:
        t = service.users().threads().get(
            userId='me', id=thread['id'], format='metadata',
            metadataHeaders=['Subject', 'From']
        ).execute()
        msgs = t.get('messages', [])
        if msgs:
            headers = {h['name']: h['value'] for h in msgs[0].get('payload', {}).get('headers', [])}
            emails.append({
                'from': headers.get('From', ''),
                'subject': headers.get('Subject', ''),
                'snippet': msgs[0].get('snippet', '')[:120],
            })
    return emails


def get_calendar_events(creds):
    service = build('calendar', 'v3', credentials=creds)
    now = datetime.now(TWN)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)

    result = service.events().list(
        calendarId='primary',
        timeMin=start.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy='startTime',
    ).execute()

    events = []
    for e in result.get('items', []):
        start_time = e['start'].get('dateTime', e['start'].get('date', ''))
        events.append({
            'summary': e.get('summary', '（無標題）'),
            'start': start_time,
            'location': e.get('location', ''),
        })
    return events


def generate_briefing(emails, events):
    now = datetime.now(TWN)
    date_str = f"{now.year} 年 {now.month} 月 {now.day} 日 {WEEKDAYS[now.weekday()]}"

    emails_text = '\n'.join(
        f"- 寄件人：{e['from']}\n  主旨：{e['subject']}\n  摘要：{e['snippet']}"
        for e in emails
    ) or '無未讀信件'

    events_text = '\n'.join(
        f"- {e['start'][:16].replace('T', ' ')}: {e['summary']}" +
        (f" @ {e['location']}" if e['location'] else '')
        for e in events
    ) or '今天沒有行程'

    prompt = f"""你是 Silvia 的 AI 助理，請根據以下資訊整理今天的晨間早報。

今天日期：{date_str}

今日 Google Calendar 行程：
{events_text}

過去 24 小時未讀信件：
{emails_text}

請用以下格式輸出早報，語氣自然親切像朋友，全程繁體中文，不要用 emoji：

# Silvia 的晨間早報
**{date_str}**

## 今日行程
（列出所有行程，時間序排列；若沒有行程就說「今天行程空白，可以好好利用！」）

## 重要信件
（列出值得注意的信件並一句話摘要；純廣告或通知類可略過；若沒有就說「信箱很乾淨！」）

## 今日建議重點
（根據以上資訊，用 2-3 句話點出今天最需要注意的事或優先處理的任務）"""

    genai.configure(api_key=os.environ['GEMINI_API_KEY'])
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content(prompt)
    return response.text


def update_gist(content):
    headers = {
        'Authorization': f"token {os.environ['GIST_TOKEN']}",
        'Accept': 'application/vnd.github.v3+json',
    }
    resp = requests.patch(
        f"https://api.github.com/gists/{os.environ['GIST_ID']}",
        headers=headers,
        json={'files': {'morning-briefing.md': {'content': content}}},
    )
    if resp.status_code == 200:
        print('早報已更新到 Gist！')
    else:
        print(f'更新 Gist 失敗：{resp.status_code}')


if __name__ == '__main__':
    print('開始生成早報...')
    creds = load_google_creds()
    emails = get_gmail_threads(creds)
    events = get_calendar_events(creds)
    briefing = generate_briefing(emails, events)
    print(briefing)
    update_gist(briefing)
