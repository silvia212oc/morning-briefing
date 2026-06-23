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
        userId='me', q='is:unread newer_than:1d category:primary', maxResults=10
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
    divider = "─" * 24

    lines = [
        f"Silvia 的晨間早報",
        f"{date_str}",
        divider,
        "",
        "今日行程",
    ]

    if events:
        for e in events:
            start = e['start']
            if 'T' in start:
                loc = f"　{e['location']}" if e['location'] else ""
                lines.append(f"  {start[11:16]}　{e['summary']}{loc}")
            else:
                loc = f"　{e['location']}" if e['location'] else ""
                lines.append(f"  全天　{e['summary']}{loc}")
    else:
        lines.append("  今天沒有行程，可以好好利用。")

    lines += ["", divider, "", "重要信件"]

    if emails:
        for e in emails:
            sender = e['from'].split('<')[0].strip() or e['from']
            lines.append(f"  {sender}")
            lines.append(f"  {e['subject']}")
            if e['snippet']:
                lines.append(f"  {e['snippet'][:80]}…")
            lines.append("")
    else:
        lines.append("  信箱很乾淨。")

    lines += [divider, ""]

    if events and emails:
        first = events[0]
        t = first['start'][11:16] if 'T' in first['start'] else '全天'
        lines.append(f"今天 {t} 有「{first['summary']}」，記得留意時間。信箱有 {len(emails)} 封主要信件，早上可以先掃一遍。")
    elif events:
        first = events[0]
        t = first['start'][11:16] if 'T' in first['start'] else '全天'
        lines.append(f"今天 {t} 有「{first['summary']}」，記得留意。信件方面很清爽，專心準備行程就好。")
    elif emails:
        lines.append(f"今天沒有行程，信箱有 {len(emails)} 封主要信件。可以趁空檔清理信件、推進手邊的事。")
    else:
        lines.append("今天行程和信件都是空的，難得清爽的一天，好好安排自己的時間。")

    return '\n'.join(lines)


def generate_summary(emails, events):
    now = datetime.now(TWN)
    weekday = ['一', '二', '三', '四', '五', '六', '日'][now.weekday()]
    date_str = f"{now.month}/{now.day}（{weekday}）早安"

    lines = [date_str, ""]

    if events:
        for e in events:
            start = e['start']
            if 'T' in start:
                lines.append(f"行程  {start[11:16]} {e['summary']}")
            else:
                lines.append(f"行程（全天）{e['summary']}")
    else:
        lines.append("今天沒有行程")

    lines.append(f"未讀信件  {len(emails)} 封")

    return '\n'.join(lines)


def update_gist(briefing, summary):
    headers = {
        'Authorization': f"token {os.environ['GIST_TOKEN']}",
        'Accept': 'application/vnd.github.v3+json',
    }
    resp = requests.patch(
        f"https://api.github.com/gists/{os.environ['GIST_ID']}",
        headers=headers,
        json={'files': {
            'morning-briefing.md': {'content': briefing},
            'morning-briefing-summary.txt': {'content': summary},
        }},
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
    summary = generate_summary(emails, events)
    print(briefing)
    update_gist(briefing, summary)
