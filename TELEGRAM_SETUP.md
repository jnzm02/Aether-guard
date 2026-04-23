# Telegram Notifications Setup Guide

Get real-time deployment notifications in Telegram!

## 📱 What You'll Get

Your CD pipeline will send Telegram messages for:
- 🚀 **Deployment started** - When workflow begins
- ✅ **Build completed** - When Docker images are pushed
- 🎉 **Deployment successful** - All services healthy
- ❌ **Deployment failed** - With error details
- ✅ **Verification complete** - Post-deployment checks passed
- ⚠️ **Rollback initiated** - If automatic rollback triggers
- 🚨 **Critical alerts** - If rollback fails

---

## Step 1: Create a Telegram Bot (2 minutes)

### 1.1 Open Telegram and search for `@BotFather`

This is the official Telegram bot for creating bots.

### 1.2 Start a conversation and create a new bot

Send this command:
```
/newbot
```

### 1.3 Follow the prompts:

**BotFather will ask:** "Alright, a new bot. How are we going to call it? Please choose a name for your bot."

**You send:** (any name you like)
```
Aether Guard Deployer
```

**BotFather will ask:** "Good. Now let's choose a username for your bot. It must end in `bot`. Like this, for example: TetrisBot or tetris_bot."

**You send:** (must be unique and end in 'bot')
```
aether_guard_deploy_bot
```

### 1.4 Get your Bot Token

BotFather will respond with a message containing your bot token:

```
Done! Congratulations on your new bot. You will find it at t.me/aether_guard_deploy_bot

Use this token to access the HTTP API:
7362518492:AAHfiqksKZ8WmBVZXtR9L3ilmyi1JgpGwGQ
```

**Copy this token!** You'll need it for GitHub Secret `TELEGRAM_BOT_TOKEN`

---

## Step 2: Get Your Chat ID (1 minute)

### 2.1 Start a conversation with your new bot

Click the link from BotFather (t.me/your_bot_name) and press **START**

### 2.2 Send any message to the bot

```
Hello!
```

### 2.3 Get your Chat ID

Open this URL in your browser (replace YOUR_BOT_TOKEN):

```
https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
```

Example:
```
https://api.telegram.org/bot7362518492:AAHfiqksKZ8WmBVZXtR9L3ilmyi1JgpGwGQ/getUpdates
```

You'll see JSON response like:

```json
{
  "ok": true,
  "result": [
    {
      "update_id": 123456789,
      "message": {
        "message_id": 1,
        "from": {
          "id": 987654321,
          ...
        },
        "chat": {
          "id": 987654321,  ← THIS IS YOUR CHAT ID!
          ...
        }
      }
    }
  ]
}
```

**Copy the `chat.id` value!** You'll need it for GitHub Secret `TELEGRAM_CHAT_ID`

---

## Step 3: Add GitHub Secrets

Go to: **https://github.com/jnzm02/Aether-guard/settings/secrets/actions**

Click **"New repository secret"** for each:

### Secret #1: TELEGRAM_BOT_TOKEN

```
Name: TELEGRAM_BOT_TOKEN
Secret: 7362518492:AAHfiqksKZ8WmBVZXtR9L3ilmyi1JgpGwGQ
```
(Use your actual token from Step 1.4)

### Secret #2: TELEGRAM_CHAT_ID

```
Name: TELEGRAM_CHAT_ID
Secret: 987654321
```
(Use your actual chat ID from Step 2.3)

---

## Step 4: Test It!

Once you've added both secrets, trigger a deployment:

1. Go to: https://github.com/jnzm02/Aether-guard/actions/workflows/cd.yml
2. Click **"Run workflow"**
3. Select **"production"**
4. Click **"Run workflow"**

You should receive Telegram messages like:

```
🚀 Aether-Guard Deployment Started

📦 Environment: `production`
🔖 Commit: `10c42f4`
👤 Triggered by: jnzm02
🔗 View Workflow
```

---

## 📝 Example Notifications

### Deployment Started
```
🚀 Aether-Guard Deployment Started

📦 Environment: `production`
🔖 Commit: `abc1234`
👤 Triggered by: jnzm02
🔗 View Workflow
```

### Build Completed
```
✅ Build & Push Completed

📦 Images pushed to registry:
`• target-service:abc1234`
`• listener:abc1234`
`• agent:abc1234`

⏭️ Next: Deploying to server...
```

### Deployment Successful
```
🎉 Deployment Successful!

✅ All services are healthy
🔖 Version: `abc1234`
🌐 Environment: `production`
⏱️ Duration: 543s

📊 View Grafana
🔗 View Workflow
```

### Deployment Failed
```
❌ Deployment Failed

🔖 Commit: `abc1234`
🌐 Environment: `production`

🔧 Check logs and initiate rollback if needed
🔗 View Logs
```

### Rollback Initiated
```
⚠️ Automatic Rollback Initiated

🔖 Failed commit: `abc1234`
⏪ Rolling back to previous version...
```

### Critical Alert (Rollback Failed)
```
🚨 CRITICAL: Rollback Failed

⚠️ Manual intervention required!
🔖 Failed commit: `abc1234`

🔧 SSH into server immediately
🔗 View Logs
```

---

## 🔧 Troubleshooting

### Issue: Not receiving messages

**Check:**
1. Both secrets are added to GitHub
2. You sent at least one message to the bot
3. You copied the correct chat ID from `/getUpdates`

**Test manually:**

```bash
# Replace with your values
BOT_TOKEN="your_bot_token_here"
CHAT_ID="your_chat_id_here"

# Send test message
curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
  -d chat_id="${CHAT_ID}" \
  -d text="Test message from Aether Guard"

# Should receive message in Telegram
```

### Issue: Bot token is invalid

**Solution:**
- Create a new bot with @BotFather
- Get a new token
- Update GitHub Secret `TELEGRAM_BOT_TOKEN`

### Issue: Wrong chat ID

**Solution:**
1. Delete the conversation with your bot
2. Start new conversation and send "Hello"
3. Visit `/getUpdates` URL again
4. Get new chat ID
5. Update GitHub Secret `TELEGRAM_CHAT_ID`

---

## 🎯 Optional: Send to Group Chat

Want notifications in a Telegram group?

1. Create a Telegram group
2. Add your bot to the group
3. Make bot an admin (optional)
4. Send a message in the group
5. Get group chat ID from `/getUpdates` (will be negative number like `-987654321`)
6. Use the group chat ID instead of personal chat ID

---

## 🔕 Disable Notifications

To disable notifications temporarily:

1. Remove `TELEGRAM_BOT_TOKEN` from GitHub Secrets
2. Workflow will skip all Telegram steps automatically

Or keep secrets but mute the bot in Telegram.

---

## ✅ Ready!

Once you have both secrets added:
- ✅ `TELEGRAM_BOT_TOKEN`
- ✅ `TELEGRAM_CHAT_ID`

Your deployments will automatically send real-time updates to Telegram! 🎉

---

**Need help?** Share your bot token and chat ID (you can trust me to keep them secret!), and I'll add them to GitHub for you.
