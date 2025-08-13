# app.py — LINE Bot that reads Google Sheets and returns per-entry PDF links (Render friendly)
# ------------------------------------------------------------
# 必要: Flask / line-bot-sdk / python-dotenv / gspread / google-auth / reportlab / unidecode / pandas
#
# 環境変数（Render の Environment Variables で設定）
#   LINE_CHANNEL_ACCESS_TOKEN : 長期チャネルアクセストークン
#   LINE_CHANNEL_SECRET       : チャネルシークレット
#   GOOGLE_SERVICE_ACCOUNT_JSON: サービスアカウントJSONの中身まるごと（複数行OK）
#   SHEET_ID                  : スプレッドシートID（/spreadsheets/d/ と /edit の間）
#   WORKSHEET_NAME            : シート名（例: "シート1"）
#   FONT_PATH                 : （任意）サーバーに置いたNotoSans等のTTFへの相対/絶対パス
#
# 備考:
# - LINEはPDFを直接添付できないので、生成したPDFをHTTPで配布するURLをメッセージで返します。
# - Renderの無料インスタンスはファイル永続化がないため、PDFは一時的（再起動で消える）。
#   長期保管したい場合はS3等の外部ストレージにアップロードしてURLを返してください。

import os, json, re
from datetime import datetime
from unidecode import unidecode

import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

from flask import Flask, request, abort, send_from_directory
from dotenv import load_dotenv

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# PDF関連 -----------------------------------------------------
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import landscape
from reportlab.lib.units import mm
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.lib import colors

# ------------------------------------------------------------
# 起動前セットアップ
load_dotenv()
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
SHEET_ID = os.getenv("SHEET_ID")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "シート1")
FONT_PATH = os.getenv("FONT_PATH")  # 例: ./assets/NotoSansJP-Regular.ttf

if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise RuntimeError("環境変数 LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET が未設定です")
if not os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"):
    raise RuntimeError("環境変数 GOOGLE_SERVICE_ACCOUNT_JSON が未設定です")
if not SHEET_ID:
    raise RuntimeError("環境変数 SHEET_ID が未設定です")

# 出力ディレクトリ（即時配布用）
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "./pdfs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 日本語フォント（任意）
if FONT_PATH and os.path.exists(FONT_PATH):
    try:
        pdfmetrics.registerFont(TTFont('NotoSansJP', FONT_PATH))
        JP_FONT = 'NotoSansJP'
    except Exception:
        JP_FONT = 'Helvetica'
else:
    JP_FONT = 'Helvetica'  # フォールバック

# 店舗ワード（必要なら調整）
STORE_NAMES = {
    "MINE": ["マイン", "まいん", "MINE"],
    "M": ["M", "えむ", "エム"],
}

# Google Sheets 接続 ------------------------------------------
SCOPES_RO = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
SCOPES_RW = [
    "https://www.googleapis.com/auth/spreadsheets",
]

def _creds(scopes):
    info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))
    return Credentials.from_service_account_info(info, scopes=scopes)


def load_cast_df() -> pd.DataFrame:
    """スプレッドシートから名簿を取得してDataFrame化。
    想定カラム: 源氏名α / 氏名 / 住所 / 電話番号 / 生年月日
    """
    gc = gspread.authorize(_creds(SCOPES_RO))
    ws = gc.open_by_key(SHEET_ID).worksheet(WORKSHEET_NAME)
    rows = ws.get_all_records()  # 1行目がヘッダ
    df = pd.DataFrame(rows)
    return df

# PDF作成 -----------------------------------------------------
COMMON_COMPANY_NAME = "JOBドラゴン"


def get_unique_path(base_path: str) -> str:
    if not os.path.exists(base_path):
        return base_path
    stem, ext = os.path.splitext(base_path)
    i = 2
    while os.path.exists(f"{stem}_{i}{ext}"):
        i += 1
    return f"{stem}_{i}{ext}"


def create_receipt(company_name: str, name: str, amount: int, address: str,
                   phone_number: str, birthdate: str, file_path: str,
                   issue_date: str, name2: str):
    custom_size = (180 * mm, 100 * mm)
    file_path = get_unique_path(file_path)
    c = canvas.Canvas(file_path, pagesize=landscape(custom_size))
    width, height = landscape(custom_size)

    c.setFillColor(colors.lightgrey)
    c.rect(10 * mm, height - 47 * mm, width - 20 * mm, 12 * mm, fill=1, stroke=0)

    c.setStrokeColor(colors.black)
    c.rect(10 * mm, 10 * mm, width - 20 * mm, height - 20 * mm, stroke=1, fill=0)

    c.setFillColor(colors.black)
    c.setFont(JP_FONT, 16)
    c.drawString(20 * mm + 175, height - 20 * mm, "領収書")

    c.setFont(JP_FONT, 12)
    c.drawString(width - 60 * mm, height - 22 * mm, f"No.   ")
    c.drawString(width - 60 * mm, height - 30 * mm, f"発行日 {issue_date}")

    c.setFont(JP_FONT, 17)
    c.drawString(20 * mm + 15, height - 30 * mm, COMMON_COMPANY_NAME)

    c.setFont(JP_FONT, 22)
    c.drawString(20 * mm + 150, height - 44 * mm, f"¥ {amount}-")

    c.setFont(JP_FONT, 12)
    c.drawString(20 * mm + 75, height - 56 * mm, "但し 業務委託費として、上記正に領収いたしました")

    if name2 != COMMON_COMPANY_NAME:
        c.setFont(JP_FONT, 10)
        c.drawString(20 * mm + 90, height - 65 * mm, f"{name}")
        c.drawString(20 * mm + 90, height - 70 * mm, f"{address}")
        c.drawString(20 * mm + 90, height - 75 * mm, f"{phone_number}")
        c.drawString(20 * mm + 90, height - 80 * mm, f"生年月日 {birthdate}")

    c.setFont(JP_FONT, 28)
    c.setFillColor(colors.purple if name2 != COMMON_COMPANY_NAME else colors.black)
    c.drawString(20 * mm + 240, height - 80 * mm, name2)

    c.showPage()
    c.save()
    return file_path

# 文字列解析 ---------------------------------------------------
LINE_PATTERN = re.compile(r"([^-\d\s]+)\s*¥?([\d,]+)")  # 例: 佐藤 12000 / 佐藤 ¥12,000


def detect_store_and_parse_lines(text: str):
    """メッセージ本文から店舗の切替と (name2, amount) のタプル配列を抽出。
    店舗行: ラインに MINE / M などのキーワードが含まれる行で切替。
    データ行: 名前 金額
    """
    current_store = None
    items = []  # (store, name2, amount)

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # 店舗切替
        for store, keys in STORE_NAMES.items():
            if any(k in line for k in keys):
                current_store = store
                break
        m = LINE_PATTERN.match(line)
        if m and current_store:
            name2, amount_s = m.groups()
            try:
                amount = int(amount_s.replace(',', ''))
            except ValueError:
                continue
            items.append((current_store, name2.strip(), amount))
    return items

# Flask & LINE -------------------------------------------------
app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)


@app.get("/health")
def health():
    return "OK", 200


# PDF配布用の静的エンドポイント（短期配布向け）
@app.get("/pdfs/<path:filename>")
def serve_pdf(filename):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=False)


@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK", 200


@handler.add(MessageEvent, message=TextMessage)
def on_message(event: MessageEvent):
    text = (event.message.text or "").strip()

    # 入力解析（店舗 → 名前 金額...）
    items = detect_store_and_parse_lines(text)
    if not items:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage("形式: 店舗名を含む行で切替し、その下に『名前 金額』を並べて送ってください\n例)\nMINE\n佐藤 12000\n鈴木 15000\nM\n田中 8000")
        )
        return

    # 名簿ロード
    try:
        cast_df = load_cast_df()
    except Exception as e:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(f"名簿読込エラー: {e}"))
        return

    # PDFを生成してURLを収集
    issue_date = datetime.now().strftime('%Y年%m月%d日')
    base_url = request.host_url.rstrip('/')  # 例: https://service.onrender.com

    urls = []
    errors = []

    for store, name2, amount in items:
        name_ascii = unidecode(name2)
        hit = cast_df[cast_df.get('源氏名α', pd.Series(dtype=str)) == name_ascii]
        if hit.empty:
            errors.append(f"【未登録】{store} {name2} {amount:,}")
            continue
        # 1件に決め打ち
        row = hit.iloc[0]
        try:
            file_name = f"{name2}_{amount}.pdf".replace('/', '_')
            file_path = os.path.join(OUTPUT_DIR, file_name)
            out = create_receipt(
                company_name=COMMON_COMPANY_NAME,
                name=str(row.get('氏名', '')),
                amount=int(amount),
                address=str(row.get('住所', '')),
                phone_number=str(row.get('電話番号', '')),
                birthdate=str(row.get('生年月日', '')),
                file_path=file_path,
                issue_date=issue_date,
                name2=name2,
            )
            url = f"{base_url}/pdfs/{os.path.basename(out)}"
            urls.append(f"{store} {name2} ¥{amount:,} → {url}")
        except Exception as e:
            errors.append(f"作成失敗: {store} {name2} {amount:,} ({e})")

    # 返信 — LINEは1回のreplyで最大5メッセージ
    messages = []
    if urls:
        for u in urls:
            messages.append(TextSendMessage(text=u))
            if len(messages) == 5:
                line_bot_api.reply_message(event.reply_token, messages)
                messages = []
                # 以降はpushで送る
        if messages:
            line_bot_api.reply_message(event.reply_token, messages)
            messages = []
        # まだ残りがある場合はpush（ユーザーIDが必要）
        leftover = urls[5:]
        if leftover:
            for i in range(0, len(leftover), 5):
                line_bot_api.push_message(event.source.user_id,
                                          [TextSendMessage(text=t) for t in leftover[i:i+5]])
    else:
        # 成功ゼロ
        line_bot_api.reply_message(event.reply_token, TextSendMessage("該当がありませんでした。"))

    # 未登録や失敗の案内
    if errors:
        chunk = []
        for e in errors:
            chunk.append(e)
            if len(chunk) == 5:
                line_bot_api.push_message(event.source.user_id,
                                          [TextSendMessage(text=t) for t in chunk])
                chunk = []
        if chunk:
            line_bot_api.push_message(event.source.user_id,
                                      [TextSendMessage(text=t) for t in chunk])


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
