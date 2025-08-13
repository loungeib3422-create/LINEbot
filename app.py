import os
from flask import Flask, request, abort
from dotenv import load_dotenv  # ← 追加：.env読み込み用

from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

# === ここで .env を読み込んで、値を変数に入れる ===
load_dotenv()
CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")
PORT = int(os.getenv("PORT", "5000"))

# どちらかが未設定なら起動時に止めて気付けるように
if not CHANNEL_ACCESS_TOKEN or not CHANNEL_SECRET:
    raise RuntimeError("環境変数が未設定です。.env に LINE_CHANNEL_ACCESS_TOKEN / LINE_CHANNEL_SECRET を入れてください。")

# === LINE SDKの準備 ===
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# === Flaskの準備 ===
app = Flask(__name__)

# 確認用（ブラウザで http://localhost:5000/health → OK）
@app.get("/health")
def health():
    return "OK", 200

# LINEサーバーが呼ぶWebhookの入口（このURLをLINEに設定）
@app.post("/callback")
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)  # 署名検証 & イベント分配
    except InvalidSignatureError:
        abort(400)  # シークレットが違うとここで落ちます

    return "OK", 200

# 受け取ったテキストをそのまま返す（最小のエコー）
@handler.add(MessageEvent, message=TextMessage)
def on_message(event: MessageEvent):
    text = event.message.text or ""
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=text)
    )

if __name__ == "__main__":
    # 0.0.0.0にしておくとクラウドでもそのまま動かしやすい
    app.run(host="0.0.0.0", port=PORT)
