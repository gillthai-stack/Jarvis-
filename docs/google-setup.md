# Connecting Gmail and Google Calendar

Jarvis talks to Google with your own OAuth client, so your mail never passes through anyone else's
server. Setting it up takes about ten minutes and you only do it once.

## 1. Create a Google Cloud project

1. Go to https://console.cloud.google.com/projectcreate, name it `Jarvis` and click **Create**.
2. Make sure the new project is selected in the top bar.

## 2. Turn on the Gmail and Calendar APIs

1. Open https://console.cloud.google.com/apis/library/gmail.googleapis.com and click **Enable**.
2. Open https://console.cloud.google.com/apis/library/calendar-json.googleapis.com and click **Enable**.

## 3. Set up the consent screen

1. Open https://console.cloud.google.com/auth/overview and click **Get started**.
2. App name `Jarvis`, your email as the support and contact email. Choose **External** as the audience.
3. Under **Audience**, add your own Gmail address as a **test user**.

You can leave the app in Testing mode. Google will show an "unverified app" warning when you sign in,
because the app is yours and not reviewed by Google. Click **Continue**.

Note: in Testing mode Google expires the sign-in after 7 days, so you will need to click
**Connect Google** again once a week. Publishing the app (Audience, **Publish app**) removes that limit
for your own account.

## 4. Create the OAuth client

1. Open https://console.cloud.google.com/auth/clients and click **Create client**.
2. Application type: **Desktop app**. Name: `Jarvis`. Click **Create**.
3. Click **Download JSON**.
4. Move the file into the Jarvis folder as `data/google_credentials.json`:

```bash
mkdir -p data
mv ~/Downloads/client_secret_*.json data/google_credentials.json
```

## 5. Connect

Restart Jarvis (`./start.sh`), then click **Connect Google** in the Today panel and approve access.

Jarvis asks for three permissions:

| Permission | Used for |
| --- | --- |
| Read your email | Searching and reading mail, the morning briefing |
| Manage drafts | Writing replies into your Drafts folder. Jarvis cannot send email |
| Calendar events | Reading your schedule and adding events you ask for |

To disconnect, delete `data/google_token.json`, or remove Jarvis at https://myaccount.google.com/permissions.
