# Connecting Telegram

With Telegram set up you can message Jarvis from your phone, send it photos and PDFs, and ask it to
message you. Jarvis must be running on your Mac for it to answer.

1. In Telegram, open a chat with **@BotFather** and send `/newbot`. Pick a name (for example `Jarvis`) and a
   username ending in `bot` (for example `gill_jarvis_bot`).
2. BotFather replies with a token like `123456789:AA...`. Put it in `.env`:

   ```
   TELEGRAM_BOT_TOKEN=123456789:AA...
   ```

3. Restart Jarvis (`./start.sh`), then send your new bot any message. It replies with a line like
   `TELEGRAM_CHAT_ID=987654321`.
4. Add that line to `.env` and restart Jarvis again.

That's it. Jarvis answers only your chat, and ignores messages from anyone else who finds the bot.

## What you can send

- **Text:** anything you'd ask on the dashboard. `/new` starts a fresh conversation.
- **Photos and PDFs:** Jarvis reads them. They are saved in `data/telegram/`, so you can say
  "draft an email to accounts@client.com with this invoice attached" and the file goes into the Gmail draft.
- Voice notes aren't supported yet. Use your keyboard's dictation instead.

Keep the bot token private: anyone with it can control the bot.
