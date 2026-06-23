import os
import json
import requests
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

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

    lines = [
        "# Silvia 的晨間早報",
        f"**{date_str}**",
        "",
        "## 今日行程",
    ]

    if events:
        for e in events:
            start = e['start']
            if 'T' in start:
                time_part = start[11:16]
                loc = f"（{e['location']}）" if e['location'] else ""
                lines.append(f"- {time_part} {e['summary']}{loc}")
            else:
                loc = f"（{e['location']}）" if e['location'] else ""
                lines.append(f"- 全天 {e['summary']}{loc}")
    else:
        lines.append("今天行程空白，可以好好利用！")

    lines += ["", "## 重要信件"]

    if emails:
        for e in emails:
            sender = e['from'].split('<')[0].strip() or e['from']
            snippet = e['snippet'][:80] + "…" if len(e['snippet']) > 80 else e['snippet']
            lines.append(f"- **{sender}**：{e['subject']}")
            if snippet:
                lines.append(f"  {snippet}")
    else:
        lines.append("信箱很乾淨！")

    lines += ["", "## 今日建議重點"]

    tips = []
    if events:
        first = events[0]
        start = first['start']
        if 'T' in start:
            tips.append(f"今天第一個行程是 {start[11:16]} 的「{first['summary']}」，記得提前準備。")
        else:
            tips.append(f"今天有全天行程「{first['summary']}」，注意時間安排。")
    if emails:
        tips.append(f"信箱有 {len(emails)} 封未讀信件，建議早上先快速過濾，標記需要回覆的。")
    if not tips:
        tips.append("今天行程和信件都很清爽，適合處理需要專注的工作或學習。")

    lines += tips

    return '\n'.join(lines)


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
