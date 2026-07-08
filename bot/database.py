import os
import random
from datetime import datetime
from typing import Optional
from enum import Enum
from azure.data.tables import TableServiceClient, UpdateMode, EntityProperty, EdmType
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
events_client = None
eventbets_client = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe(entity: dict) -> dict:
    """
    Azure SDK tries to cast ints to Edm.Int32 by default and crashes 
    if they exceed 32 bits (like Telegram Group chat_ids). 
    This explicitly forces large ints into Edm.Int64.
    """
    for k, v in entity.items():
        if isinstance(v, int) and (v > 2147483647 or v < -2147483648):
            entity[k] = EntityProperty(v, EdmType.INT64)
    return entity

def _unwrap(entity):
    """
    When retrieving entities, Azure Table SDK might return Edm.Int64 
    as an EntityProperty object. This unwraps them back into plain ints.
    """
    if not entity: return entity
    for k, v in list(entity.items()):
        if hasattr(v, "value"):
            entity[k] = v.value
        elif isinstance(v, str) and (k.endswith("_id") or k == "id"):
            try:
                entity[k] = int(v)
            except ValueError:
                pass
    return entity

def _unwrap_list(entities):
    return [_unwrap(e) for e in entities]

# ---------------------------------------------------------------------------
# Schema init
# ---------------------------------------------------------------------------
def init_db() -> None:
    if not CONN_STR:
        print("Warning: AZURE_STORAGE_CONNECTION_STRING not set!")
        return

    global table_service_client, users_client, bets_client, votes_client, events_client, eventbets_client
    table_service_client = TableServiceClient.from_connection_string(conn_str=CONN_STR)
    
    for table_name in ["users", "bets", "votes", "events", "eventbets"]:
        table_client = table_service_client.get_table_client(table_name)
        try:
            table_client.create_table()
        except Exception:
            pass 

    users_client = table_service_client.get_table_client("users")
    bets_client = table_service_client.get_table_client("bets")
    votes_client = table_service_client.get_table_client("votes")
    events_client = table_service_client.get_table_client("events")
    eventbets_client = table_service_client.get_table_client("eventbets")


# ---------------------------------------------------------------------------
# User operations
# ---------------------------------------------------------------------------
def get_or_create_user(user_id: int, username: Optional[str], first_name: str, chat_id: Optional[int] = None):
    try:
        user = users_client.get_entity(partition_key="Users", row_key=str(user_id))
        user = _unwrap(user)
        changed = False
        
        if user.get("username") != username or user.get("first_name") != first_name:
            user["username"] = username or ""
            user["first_name"] = first_name
            changed = True
            
        if chat_id:
            chats = user.get("chats", "")
            chat_str = str(chat_id)
            if chat_str not in chats.split(","):
                user["chats"] = f"{chats},{chat_str}" if chats else chat_str
                changed = True
                
        if changed:
            users_client.upsert_entity(_safe(user))
        return user
    except ResourceNotFoundError:
        chats = str(chat_id) if chat_id else ""
        user = {
            "PartitionKey": "Users",
            "RowKey": str(user_id),
            "user_id": user_id,
            "username": username or "",
            "first_name": first_name,
            "balance": 1.0,
            "chats": chats
        }
        users_client.upsert_entity(_safe(user))
        return _unwrap(user)

def get_user(user_id: int):
    try:
        return _unwrap(users_client.get_entity(partition_key="Users", row_key=str(user_id)))
    except ResourceNotFoundError:
        return None

def get_user_by_username(username: str):
    if not username: return None
    username_clean = username.lstrip("@").lower()
    
    users = list(users_client.list_entities())
    for u in users:
        u = _unwrap(u)
        if u.get("username") and u["username"].lower() == username_clean:
            return u
    return None

def get_all_users_ranked(chat_id: Optional[int] = None):
    users = _unwrap_list(list(users_client.list_entities()))
    if chat_id:
        chat_str = str(chat_id)
        users = [u for u in users if chat_str in u.get("chats", "").split(",")]
    users.sort(key=lambda x: x.get("balance", 0.0), reverse=True)
    return users

def transfer_balance(winner_id: int, loser_id: int, amount: float) -> None:
    winner = get_user(winner_id)
    loser = get_user(loser_id)
    if winner and loser:
        winner["balance"] += amount
        loser["balance"] -= amount
        users_client.upsert_entity(_safe(winner))
        users_client.upsert_entity(_safe(loser))


# ---------------------------------------------------------------------------
# 1v1 Bet operations
# ---------------------------------------------------------------------------
def create_bet(
    chat_id: int,
    proposer_id: int,
    opponent_id: int,
    amount: float,
    description: str,
) -> int:
    bet_id = random.randint(100000, 999999)
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
    bets_client.upsert_entity(_safe(bet))
    return bet_id

def get_bet(bet_id: int):
    query = f"RowKey eq '{bet_id}'"
    results = list(bets_client.query_entities(query))
    return _unwrap(results[0]) if results else None

def get_active_bets(chat_id: int):
    query = f"PartitionKey eq '{chat_id}'"
    results = _unwrap_list(list(bets_client.query_entities(query)))
    active_statuses = {"PROPOSED", "ACTIVE", "CLAIMED", "DISPUTED"}
    return sorted(
        [b for b in results if b.get("status") in active_statuses], 
        key=lambda x: x.get("id", 0), 
        reverse=True
    )

def get_bet_history(chat_id: int, limit: int = 10):
    query = f"PartitionKey eq '{chat_id}'"
    results = _unwrap_list(list(bets_client.query_entities(query)))
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
        bets_client.upsert_entity(_safe(bet))


# ---------------------------------------------------------------------------
# Vote operations
# ---------------------------------------------------------------------------
def add_vote(bet_id: int, voter_id: int, voted_for_id: int) -> None:
    vote = {
        "PartitionKey": str(bet_id),
        "RowKey": str(voter_id),
        "voted_for_id": voted_for_id
    }
    votes_client.upsert_entity(_safe(vote))

def get_votes(bet_id: int):
    query = f"PartitionKey eq '{bet_id}'"
    return _unwrap_list(list(votes_client.query_entities(query)))

def count_votes_for(bet_id: int, candidate_id: int) -> int:
    votes = get_votes(bet_id)
    return sum(1 for v in votes if v.get("voted_for_id") == candidate_id)


# ---------------------------------------------------------------------------
# Group Events (Pool Betting) operations
# ---------------------------------------------------------------------------
def create_event(chat_id: int, creator_id: int, description: str) -> int:
    event_id = random.randint(100000, 999999)
    while True:
        try:
            events_client.get_entity(partition_key=str(chat_id), row_key=str(event_id))
            event_id = random.randint(100000, 999999)
        except ResourceNotFoundError:
            break

    event = {
        "PartitionKey": str(chat_id),
        "RowKey": str(event_id),
        "id": event_id,
        "chat_id": chat_id,
        "creator_id": creator_id,
        "description": description,
        "status": "OPEN",
        "created_at": datetime.utcnow().isoformat(),
        "resolved_at": "",
        "message_id": 0
    }
    events_client.upsert_entity(_safe(event))
    return event_id

def get_event(event_id: int):
    query = f"RowKey eq '{event_id}'"
    results = list(events_client.query_entities(query))
    return _unwrap(results[0]) if results else None

def get_active_events(chat_id: int):
    query = f"PartitionKey eq '{chat_id}'"
    results = _unwrap_list(list(events_client.query_entities(query)))
    return sorted(
        [e for e in results if e.get("status") == "OPEN"], 
        key=lambda x: x.get("id", 0), 
        reverse=True
    )

def get_event_history(chat_id: int, limit: int = 10):
    query = f"PartitionKey eq '{chat_id}'"
    results = _unwrap_list(list(events_client.query_entities(query)))
    history = [e for e in results if e.get("status") != "OPEN"]
    history.sort(key=lambda x: x.get("resolved_at", ""), reverse=True)
    return history[:limit]

def update_event(event_id: int, **fields):
    event = get_event(event_id)
    if event:
        for k, v in fields.items():
            if v is not None:
                event[k] = v
        events_client.upsert_entity(_safe(event))

def place_event_bet(event_id: int, user_id: int, side: str, amount: float):
    bet = {
        "PartitionKey": str(event_id),
        "RowKey": str(user_id),
        "user_id": user_id,
        "side": side.upper(),
        "amount": float(amount)
    }
    eventbets_client.upsert_entity(_safe(bet))

def get_event_bets(event_id: int):
    query = f"PartitionKey eq '{event_id}'"
    return _unwrap_list(list(eventbets_client.query_entities(query)))

def resolve_event(event_id: int, winning_side: str) -> dict:
    event = get_event(event_id)
    if not event or event.get("status") != "OPEN":
        return None

    bets = get_event_bets(event_id)
    winning_side = winning_side.upper()
    
    if winning_side == "CANCEL":
        update_event(event_id, status="CANCELED", resolved_at=datetime.utcnow().isoformat())
        return {"status": "CANCELED"}
    
    for_pool = sum(b["amount"] for b in bets if b["side"] == "FOR")
    against_pool = sum(b["amount"] for b in bets if b["side"] == "AGAINST")
    
    winners = [b for b in bets if b["side"] == winning_side]
    losers = [b for b in bets if b["side"] != winning_side]
    
    winning_pool = sum(b["amount"] for b in winners)
    losing_pool = sum(b["amount"] for b in losers)
    
    if winning_pool > 0 and losing_pool > 0:
        for w in winners:
            share = w["amount"] / winning_pool
            won_amount = share * losing_pool
            user = get_user(w["user_id"])
            if user:
                user["balance"] += won_amount
                users_client.upsert_entity(_safe(user))
                
        for l in losers:
            user = get_user(l["user_id"])
            if user:
                user["balance"] -= l["amount"]
                users_client.upsert_entity(_safe(user))

    update_event(event_id, status=f"RESOLVED_{winning_side}", resolved_at=datetime.utcnow().isoformat())
    
    return {
        "status": f"RESOLVED_{winning_side}",
        "for_pool": for_pool,
        "against_pool": against_pool,
        "winners_count": len(winners),
        "losers_count": len(losers),
        "losing_pool": losing_pool
    }
