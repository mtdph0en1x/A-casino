import os
import random
from datetime import datetime
from typing import Optional
from enum import Enum
from azure.data.tables import TableServiceClient, UpdateMode
from azure.core.exceptions import ResourceNotFoundError

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CONN_STR = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")

# Clients
table_service_client = None
users_client = None
bets_client = None
votes_client = None

# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------
def init_db() -> None:
    if not CONN_STR:
        print("Warning: AZURE_STORAGE_CONNECTION_STRING not set!")
        return

    global table_service_client, users_client, bets_client, votes_client
    table_service_client = TableServiceClient.from_connection_string(conn_str=CONN_STR)
    
    # Ensure tables exist
    for table_name in ["users", "bets", "votes"]:
        table_client = table_service_client.get_table_client(table_name)
        try:
            table_client.create_table()
        except Exception:
            pass # Table already exists

    users_client = table_service_client.get_table_client("users")
    bets_client = table_service_client.get_table_client("bets")
    votes_client = table_service_client.get_table_client("votes")


# ---------------------------------------------------------------------------
# User operations
# ---------------------------------------------------------------------------
def get_or_create_user(user_id: int, username: Optional[str], first_name: str):
    try:
        user = users_client.get_entity(partition_key="Users", row_key=str(user_id))
        if user.get("username") != username or user.get("first_name") != first_name:
            user["username"] = username or ""
            user["first_name"] = first_name
            users_client.upsert_entity(user)
        return user
    except ResourceNotFoundError:
        user = {
            "PartitionKey": "Users",
            "RowKey": str(user_id),
            "user_id": user_id,
            "username": username or "",
            "first_name": first_name,
            "balance": 1.0
        }
        users_client.upsert_entity(user)
        return user

def get_user(user_id: int):
    try:
        return users_client.get_entity(partition_key="Users", row_key=str(user_id))
    except ResourceNotFoundError:
        return None

def get_user_by_username(username: str):
    if not username: return None
    username_clean = username.lstrip("@").lower()
    
    users = list(users_client.query_entities(""))
    for u in users:
        if u.get("username") and u["username"].lower() == username_clean:
            return u
    return None

def get_all_users_ranked():
    users = list(users_client.query_entities(""))
    users.sort(key=lambda x: x.get("balance", 0.0), reverse=True)
    return users

def transfer_balance(winner_id: int, loser_id: int, amount: float) -> None:
    winner = get_user(winner_id)
    loser = get_user(loser_id)
    if winner and loser:
        winner["balance"] += amount
        loser["balance"] -= amount
        users_client.upsert_entity(winner)
        users_client.upsert_entity(loser)


# ---------------------------------------------------------------------------
# Bet operations
# ---------------------------------------------------------------------------
def create_bet(
    chat_id: int,
    proposer_id: int,
    opponent_id: int,
    amount: float,
    description: str,
) -> int:
    bet_id = random.randint(100000, 999999)
    # Ensure ID uniqueness
    while True:
        try:
            bets_client.get_entity(partition_key=str(chat_id), row_key=str(bet_id))
            bet_id = random.randint(100000, 999999)
        except ResourceNotFoundError:
            break

    bet = {
        "PartitionKey": str(chat_id),
        "RowKey": str(bet_id),
        "id": bet_id,
        "chat_id": chat_id,
        "proposer_id": proposer_id,
        "opponent_id": opponent_id,
        "amount": float(amount),
        "description": description,
        "status": "PROPOSED",
        "created_at": datetime.utcnow().isoformat(),
        "resolved_at": ""
    }
    bets_client.upsert_entity(bet)
    return bet_id

def get_bet(bet_id: int):
    query = f"RowKey eq '{bet_id}'"
    results = list(bets_client.query_entities(query))
    return results[0] if results else None

def get_active_bets(chat_id: int):
    query = f"PartitionKey eq '{chat_id}'"
    results = list(bets_client.query_entities(query))
    active_statuses = {"PROPOSED", "ACTIVE", "CLAIMED", "DISPUTED"}
    return sorted(
        [b for b in results if b.get("status") in active_statuses], 
        key=lambda x: x.get("id", 0), 
        reverse=True
    )

def get_bet_history(chat_id: int, limit: int = 10):
    query = f"PartitionKey eq '{chat_id}'"
    results = list(bets_client.query_entities(query))
    resolved_statuses = {"CONFIRMED", "DECLINED"}
    history = [b for b in results if b.get("status") in resolved_statuses]
    history.sort(key=lambda x: x.get("resolved_at", ""), reverse=True)
    return history[:limit]

def update_bet(bet_id: int, **fields) -> None:
    bet = get_bet(bet_id)
    if bet:
        for k, v in fields.items():
            if v is None:
                continue
            if isinstance(v, Enum):
                v = v.value
            bet[k] = v
        bets_client.upsert_entity(bet)


# ---------------------------------------------------------------------------
# Vote operations
# ---------------------------------------------------------------------------
def add_vote(bet_id: int, voter_id: int, voted_for_id: int) -> None:
    vote = {
        "PartitionKey": str(bet_id),
        "RowKey": str(voter_id),
        "voted_for_id": voted_for_id
    }
    votes_client.upsert_entity(vote)

def get_votes(bet_id: int):
    query = f"PartitionKey eq '{bet_id}'"
    return list(votes_client.query_entities(query))

def count_votes_for(bet_id: int, candidate_id: int) -> int:
    votes = get_votes(bet_id)
    return sum(1 for v in votes if v.get("voted_for_id") == candidate_id)
