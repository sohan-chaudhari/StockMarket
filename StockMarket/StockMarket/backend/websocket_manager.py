from typing import Dict, List
from fastapi import WebSocket

# Phase 15A: this endpoint (/ws/user, main.py) requires auth before connect()
# is ever called, unlike the public /ws/dashboard feed -- but nothing
# previously capped how many authenticated sessions could accumulate, total
# or per-user. Same bounding pattern as MAX_DASHBOARD_WS in main.py's
# ConnectionManager, sized down since this is a private per-user channel,
# not a broadcast one.
MAX_USER_WS_TOTAL = 300
MAX_USER_WS_PER_USER = 6


class UserConnectionManager:
    """
    Manages WebSocket connections for individual users.
    Allows sending private messages (order updates, balance) to specific users.
    """
    def __init__(self):
        # Map user_id -> List[WebSocket] (User might have multiple tabs open)
        self.active_connections: Dict[int, List[WebSocket]] = {}

    def _total_connections(self) -> int:
        return sum(len(conns) for conns in self.active_connections.values())

    async def connect(self, user_id: int, websocket: WebSocket) -> bool:
        """Returns False (and does not register the socket) if a cap is hit --
        caller is responsible for closing the connection in that case."""
        if self._total_connections() >= MAX_USER_WS_TOTAL:
            print(f"[WS] Rejected user {user_id}: MAX_USER_WS_TOTAL ({MAX_USER_WS_TOTAL}) reached")
            return False
        existing = self.active_connections.get(user_id, [])
        if len(existing) >= MAX_USER_WS_PER_USER:
            print(f"[WS] Rejected user {user_id}: MAX_USER_WS_PER_USER ({MAX_USER_WS_PER_USER}) reached")
            return False
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)
        print(f"[WS] User {user_id} connected. Active sessions: {len(self.active_connections[user_id])}")
        return True

    def disconnect(self, user_id: int, websocket: WebSocket):
        if user_id in self.active_connections:
            if websocket in self.active_connections[user_id]:
                self.active_connections[user_id].remove(websocket)
            
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        print(f"[WS] User {user_id} disconnected")

    async def send_personal_message(self, message: dict, user_id: int):
        """Send a message to all active connections for a specific user"""
        if user_id in self.active_connections:
            async def _send(conn):
                import asyncio
                try:
                    await asyncio.wait_for(conn.send_json(message), timeout=0.5)
                except Exception as e:
                    print(f"[WS] Failed to send private message to user {user_id}: {e}")
                    return conn

            import asyncio
            tasks = [_send(conn) for conn in self.active_connections[user_id]]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for res in results:
                if res and hasattr(res, 'send_json'): # if it returned the connection object
                    self.disconnect(user_id, res)
                    
    async def handle_message(self, user_id: int, message: str):
        """Handle incoming messages from a user's WebSocket connection."""
        pass  # Currently we don't process incoming messages from users

# Global instance
user_ws_manager = UserConnectionManager()
