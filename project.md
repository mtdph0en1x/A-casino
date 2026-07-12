# ЖопоБанк (AssBank) Telegram Bot

A serverless, async Telegram bot for friendly group betting and economy management. Built with Python, FastAPI, and Azure Table Storage, and designed to run cost-effectively on Azure Container Apps.

## 🏗️ Architecture

- **Language:** Python 3.11
- **Framework:** `python-telegram-bot` (v20+) running in Webhook mode.
- **Web Server:** FastAPI / Uvicorn (receives webhooks from Telegram).
- **Database:** Azure Table Storage (NoSQL) for high-performance, cheap, and serverless data persistence.
- **Hosting:** Azure Container Apps (scales to 0 when idle to save costs).
- **CI/CD:** GitHub Actions (automatically builds the Docker image and deploys to Azure on `git push`).

## 🗄️ Database Schema (Azure Table Storage)

The bot uses the `azure-data-tables` SDK. To handle Telegram's large integer IDs (like negative group chat IDs), the bot wraps large integers into `EdmType.INT64` during upsert and unwraps them on retrieval.

1. **`users`**
   - Stores user profiles and their global balance.
   - *PartitionKey:* `"Users"`
   - *RowKey:* `user_id`
   - *Fields:* `username`, `first_name`, `balance`, `chats` (comma-separated list of group IDs they interact in)
2. **`bets`**
   - Stores 1v1 bets between users.
   - *PartitionKey:* `chat_id`
   - *RowKey:* `bet_id`
3. **`votes`**
   - Stores dispute votes for 1v1 bets.
   - *PartitionKey:* `bet_id`
   - *RowKey:* `voter_id`
4. **`events`**
   - Stores group-wide pool betting events.
   - *PartitionKey:* `chat_id`
   - *RowKey:* `event_id`
5. **`eventbets`**
   - Stores individual wagers placed on a group event.
   - *PartitionKey:* `event_id`
   - *RowKey:* `user_id`

## 💬 Commands & Features

### 1v1 Bets
Users can challenge each other to direct bets. If the loser disputes the outcome, the chat votes to decide the winner.
- `/bet @username <amount> <condition>` — Challenge someone to a bet.
- `/bets` — Show active 1v1 bets in the current chat.
- `/claim <id>` — Declare yourself the winner of a bet.
- `/history` — Show the history of resolved 1v1 bets.

### Group Events (Pool Betting)
A pari-mutuel betting system where users pool their money on `FOR` or `AGAINST`. Winners split the losers' pool proportionally.
- `/event <condition>` — Create a new group event.
- `/events` — Show open events in the current chat.
- `/beton <id> <за/проти> <amount>` — Place a wager on an event.
- `/resolve <id> <за/проти/відміна>` — Resolve the event (creator only).
- `/ehistory` — Show the history of resolved group events.

### Economy & Stats
Balances are global, but leaderboards are isolated to the context of the current group. Users can go into negative balances (debt) for drama.
- `/balance` — Check your global balance.
- `/leaderboard` — See the ranking of all users who have participated in the current group.

## 🚀 Deployment & Operations

### Privacy Mode & Performance
- **Group Privacy Mode:** MUST be ENABLED in BotFather. This ensures Telegram only sends messages starting with `/` to the bot's webhook, allowing the Azure Container to scale to 0 and preventing unnecessary compute costs.
- **In-Memory Cache:** The bot uses an in-memory dictionary cache to track seen users per session, preventing aggressive database spam from the `auto_register` feature.

### Cost Optimization
Because the bot is deployed on Azure Container Apps with a profile of `0.1 vCPU` and `0.2 GB RAM`, it fits comfortably within the generous free tier (180,000 vCPU-seconds/month). It costs essentially $0 to run, as it sleeps when idle and only wakes up to process slash commands.
