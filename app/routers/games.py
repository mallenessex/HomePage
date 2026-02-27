import asyncio
import json
import random
from datetime import date, datetime, time as dt_time, timedelta

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from typing import Optional, List, Dict, Any
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import models, database, server_utils, crossword_service
from . import auth
from ..config import settings

router = APIRouter(
    prefix="/games",
    tags=["games"]
)

templates = Jinja2Templates(directory=str(settings.BASE_DIR / "templates"))

PLAY_MODE_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": "multiplayer", "label": "Multiplayer"},
    {"value": "single_player", "label": "Single Player"},
)

GENRE_OPTIONS: tuple[dict[str, str], ...] = (
    {"value": "tactical_artillery", "label": "Tactical Artillery"},
    {"value": "puzzle", "label": "Puzzle"},
    {"value": "board_strategy", "label": "Board Strategy"},
    {"value": "word_puzzle", "label": "Word Puzzle"},
    {"value": "party", "label": "Party"},
    {"value": "general", "label": "General"},
)

VALID_PLAY_MODES = {item["value"] for item in PLAY_MODE_OPTIONS}
VALID_GENRES = {item["value"] for item in GENRE_OPTIONS}

GAME_TYPE_DEFAULT_TAGS: dict[str, tuple[str, str]] = {
    "scorched_earth": ("multiplayer", "tactical_artillery"),
    "scorch": ("multiplayer", "tactical_artillery"),
    "2048": ("single_player", "puzzle"),
    "tetris": ("single_player", "puzzle"),
    "civic_chess": ("multiplayer", "board_strategy"),
    "chess": ("multiplayer", "board_strategy"),
    "family_crossword": ("multiplayer", "word_puzzle"),
}

GENRE_ALIASES = {
    "tactical": "tactical_artillery",
    "artillery": "tactical_artillery",
    "board": "board_strategy",
    "boardgame": "board_strategy",
    "board_game": "board_strategy",
    "word": "word_puzzle",
    "wordgame": "word_puzzle",
    "word_game": "word_puzzle",
}


def _normalize_slug(value: Optional[str]) -> str:
    return (value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _default_tags_for_game_type(game_type: Optional[str]) -> tuple[str, str]:
    return GAME_TYPE_DEFAULT_TAGS.get(_normalize_slug(game_type), ("multiplayer", "general"))


def _normalize_play_mode(value: Optional[str], fallback: str = "multiplayer") -> str:
    key = _normalize_slug(value)
    if key in {"single", "singleplayer", "solo"}:
        key = "single_player"
    elif key in {"multi", "multi_player", "coop", "co_op"}:
        key = "multiplayer"
    if key in VALID_PLAY_MODES:
        return key
    return fallback if fallback in VALID_PLAY_MODES else "multiplayer"


def _normalize_genre(value: Optional[str], fallback: str = "general") -> str:
    key = _normalize_slug(value)
    key = GENRE_ALIASES.get(key, key)
    if key in VALID_GENRES:
        return key
    return fallback if fallback in VALID_GENRES else "general"


def _default_join_url_for_game_type(game_type: Optional[str]) -> Optional[str]:
    key = _normalize_slug(game_type)
    if key in {"scorched_earth", "scorch"}:
        return "/games/scorch"
    if key in {"2048"}:
        return "/games/2048"
    if key in {"tetris"}:
        return "/games/tetris"
    if key in {"civic_chess", "chess"}:
        return "/games/chess"
    if key in {"family_crossword", "crossword"}:
        return "/games/crossword"
    return None


def _parse_crossword_date(raw: Optional[str]) -> date:
    try:
        return crossword_service.parse_publish_date(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _publish_crosswords_for_local_today() -> None:
    async with database.AsyncSessionLocal() as db:
        await crossword_service.ensure_crosswords_for_date(db, crossword_service.local_today())


async def run_crossword_scheduler() -> None:
    """Publish today's crossword at startup, then each day at 00:05 local time."""
    while True:
        try:
            await _publish_crosswords_for_local_today()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"Crossword scheduler publish failed: {exc}")

        now = datetime.now().astimezone()
        next_run = datetime.combine(now.date(), dt_time(hour=0, minute=5), tzinfo=now.tzinfo)
        if now >= next_run:
            next_run = next_run + timedelta(days=1)
        delay_seconds = max(30, int((next_run - now).total_seconds()))
        await asyncio.sleep(delay_seconds)

# --- Scorched Earth Multiplayer Manager ---
# A faithful clone of Wendell Hicken's 1991 classic


class ScorchGame:
    PHYSICS = {
        "gravity": 9.8,
        "max_wind": 15.0,
        "step_size": 0.08,
    }
    INITIAL_CASH = 50000
    DAMAGE_CASH_PER_HP = 150
    KILL_BONUS_CASH = 8000
    ROUND_SURVIVOR_BONUS = 5000
    MAX_PLAYERS = 10
    WEAPONS: Dict[str, Dict[str, Any]] = {
        "baby_missile": {
            "name": "Baby Missile", "price": 0, "radius": 18, "damage": 25,
            "color": "#ffff00", "stock": -1, "is_dirt": False,
            "special": None, "sub_count": 1,
        },
        "missile": {
            "name": "Missile", "price": 0, "radius": 38, "damage": 55,
            "color": "#ffff00", "stock": -1, "is_dirt": False,
            "special": None, "sub_count": 1,
        },
        "large_missile": {
            "name": "Large Missile", "price": 4500, "radius": 55, "damage": 70,
            "color": "#ff8800", "stock": 0, "is_dirt": False,
            "special": None, "sub_count": 1,
        },
        "baby_nuke": {
            "name": "Baby Nuke", "price": 12000, "radius": 75, "damage": 85,
            "color": "#ff4400", "stock": 0, "is_dirt": False,
            "special": None, "sub_count": 1,
        },
        "nuke": {
            "name": "Nuke", "price": 25000, "radius": 105, "damage": 95,
            "color": "#ff0000", "stock": 0, "is_dirt": False,
            "special": None, "sub_count": 1,
        },
        "mirv": {
            "name": "MIRV", "price": 30000, "radius": 28, "damage": 40,
            "color": "#00ffff", "stock": 0, "is_dirt": False,
            "special": "mirv", "sub_count": 5,
        },
        "deaths_head": {
            "name": "Death's Head", "price": 45000, "radius": 45, "damage": 60,
            "color": "#ff00ff", "stock": 0, "is_dirt": False,
            "special": "deaths_head", "sub_count": 5,
        },
        "funky_bomb": {
            "name": "Funky Bomb", "price": 18000, "radius": 20, "damage": 30,
            "color": "#ff69b4", "stock": 0, "is_dirt": False,
            "special": "funky", "sub_count": 8,
        },
        "napalm": {
            "name": "Napalm", "price": 10000, "radius": 48, "damage": 55,
            "color": "#ff6600", "stock": 0, "is_dirt": False,
            "special": "napalm", "sub_count": 1,
        },
        "hot_napalm": {
            "name": "Hot Napalm", "price": 22000, "radius": 65, "damage": 80,
            "color": "#ff3300", "stock": 0, "is_dirt": False,
            "special": "hot_napalm", "sub_count": 1,
        },
        "dirt_charge": {
            "name": "Dirt Charge", "price": 2000, "radius": 28, "damage": 0,
            "color": "#8b4513", "stock": 0, "is_dirt": True,
            "special": None, "sub_count": 1,
        },
        "dirt_ball": {
            "name": "Dirt Ball", "price": 6000, "radius": 52, "damage": 0,
            "color": "#a0522d", "stock": 0, "is_dirt": True,
            "special": None, "sub_count": 1,
        },
        "heavy_roller": {
            "name": "Heavy Roller", "price": 8000, "radius": 32, "damage": 55,
            "color": "#888888", "stock": 0, "is_dirt": False,
            "special": "roller", "sub_count": 1,
        },
        "leap_frog": {
            "name": "Leap Frog", "price": 9000, "radius": 25, "damage": 40,
            "color": "#00ff00", "stock": 0, "is_dirt": False,
            "special": "leapfrog", "sub_count": 3,
        },
    }

    ITEMS: Dict[str, Dict[str, Any]] = {
        "light_shield": {"name": "Light Shield", "price": 5000, "absorption": 0.25},
        "medium_shield": {"name": "Medium Shield", "price": 12000, "absorption": 0.50},
        "heavy_shield": {"name": "Heavy Shield", "price": 25000, "absorption": 0.75},
        "parachute": {"name": "Parachute", "price": 3000, "type": "parachute"},
        "fuel": {"name": "Fuel Tank", "price": 5000, "type": "fuel", "range": 30},
    }

    TERRAIN_TYPES = ["random", "hilly", "mountainous", "flat", "craggy"]

    TANK_COLORS = [
        "#ff4444", "#44ff44", "#4488ff", "#ffff44",
        "#ff44ff", "#44ffff", "#ff8844", "#88ff44",
        "#ff4488", "#8844ff",
    ]

    def __init__(self, terrain_type: str = "random"):
        self.players: List[Dict[str, Any]] = []
        self.terrain: List[float] = []
        self.current_turn_index = 0
        self.wind = 0.0
        self.width = 1200
        self.height = 1200
        self.ground_height = 600  # terrain generation uses original scale
        self.terrain_type = terrain_type if terrain_type in self.TERRAIN_TYPES else "random"
        self.pending_shot_player_id: Optional[int] = None
        self.pending_shot_weapon: Optional[str] = None
        self.pending_sub_remaining: int = 0
        self.round_number = 1
        self.max_rounds = 10
        self.round_active = True
        self.scores: Dict[int, int] = {}
        self._color_index = 0
        self.generate_terrain()
        self._roll_wind()

    def _roll_wind(self):
        self.wind = round(random.uniform(-self.PHYSICS["max_wind"], self.PHYSICS["max_wind"]), 2)

    def _next_player_id(self) -> int:
        if not self.players:
            return 1
        return max(player["id"] for player in self.players) + 1

    def _assign_color(self) -> str:
        color = self.TANK_COLORS[self._color_index % len(self.TANK_COLORS)]
        self._color_index += 1
        return color

    def _spawn_x(self) -> int:
        min_x = 60
        max_x = self.width - 60
        for _ in range(50):
            candidate = random.randint(min_x, max_x)
            if all(abs(candidate - player["x"]) >= 80 for player in self.players):
                return candidate
        return random.randint(min_x, max_x)

    def generate_terrain(self):
        self.terrain = []
        gh = self.ground_height  # use ground_height so terrain stays the same scale
        if self.terrain_type == "flat":
            base = gh * 0.65
            for _ in range(self.width):
                self.terrain.append(base + random.uniform(-3, 3))
        elif self.terrain_type == "hilly":
            h = gh * 0.6
            s = 0.0
            for _ in range(self.width):
                s += (random.random() - 0.5) * 0.2
                s *= 0.98
                h += s
                h = max(200, min(gh - 60, h))
                self.terrain.append(h)
        elif self.terrain_type == "mountainous":
            h = gh * 0.5
            s = 0.0
            for _ in range(self.width):
                s += (random.random() - 0.5) * 0.6
                s *= 0.99
                h += s
                h = max(120, min(gh - 40, h))
                self.terrain.append(h)
        elif self.terrain_type == "craggy":
            h = gh * 0.55
            for _ in range(self.width):
                h += (random.random() - 0.5) * 5.0
                if random.random() < 0.02:
                    h += random.choice([-40, -30, 30, 40])
                h = max(100, min(gh - 40, h))
                self.terrain.append(h)
        else:
            h = gh * 0.6
            s = 0.0
            for _ in range(self.width):
                s += (random.random() - 0.5) * 0.35
                s *= 0.985
                h += s
                h = max(150, min(gh - 50, h))
                self.terrain.append(h)
        # Smooth pass
        smoothed = list(self.terrain)
        for i in range(2, len(smoothed) - 2):
            smoothed[i] = (
                self.terrain[i - 2] + self.terrain[i - 1] + self.terrain[i]
                + self.terrain[i + 1] + self.terrain[i + 2]
            ) / 5
        self.terrain = smoothed

    def add_player(self, name: str, color: str = "") -> Optional[Dict[str, Any]]:
        if len(self.players) >= self.MAX_PLAYERS:
            return None
        player_id = self._next_player_id()
        x_pos = self._spawn_x()
        y_pos = self.terrain[x_pos]
        if not color:
            color = self._assign_color()
        player = {
            "id": player_id,
            "name": name,
            "color": color,
            "x": x_pos,
            "y": y_pos,
            "hp": 100,
            "angle": 45 if x_pos < self.width / 2 else 135,
            "power": 70,
            "cash": self.INITIAL_CASH,
            "inventory": {key: wpn["stock"] for key, wpn in self.WEAPONS.items()},
            "items": {},
            "shield": None,
            "shield_hp": 0,
            "score": 0,
        }
        self.players.append(player)
        self.scores[player_id] = 0
        if len(self.players) == 1:
            self.current_turn_index = 0
        return player

    def remove_player(self, player_id: int):
        idx = self.player_index(player_id)
        if idx is None:
            return
        del self.players[idx]
        if self.players:
            if idx < self.current_turn_index:
                self.current_turn_index -= 1
            if self.current_turn_index >= len(self.players):
                self.current_turn_index = 0
            self.current_turn_index = self._next_alive_index(self.current_turn_index)
        else:
            self.current_turn_index = 0
        if self.pending_shot_player_id == player_id:
            self.pending_shot_player_id = None
            self.pending_shot_weapon = None
            self.pending_sub_remaining = 0

    def player_index(self, player_id: int) -> Optional[int]:
        for idx, player in enumerate(self.players):
            if player["id"] == player_id:
                return idx
        return None

    def player_by_id(self, player_id: int) -> Optional[Dict[str, Any]]:
        idx = self.player_index(player_id)
        if idx is None:
            return None
        return self.players[idx]

    def is_players_turn(self, player_id: int) -> bool:
        if not self.players:
            return False
        idx = self.player_index(player_id)
        if idx is None:
            return False
        return idx == self.current_turn_index and self.players[idx]["hp"] > 0

    def buy_weapon(self, player_id: int, weapon_key: str, quantity: int = 1) -> tuple[bool, str]:
        player = self.player_by_id(player_id)
        weapon = self.WEAPONS.get(weapon_key)
        if player is None:
            return False, "Player not found."
        if weapon is None:
            return False, "Unknown weapon."
        if not self.is_players_turn(player_id):
            return False, "You can only shop on your turn."
        if weapon["price"] <= 0:
            return False, "This weapon cannot be purchased."
        quantity = max(1, min(int(quantity), 20))
        total = weapon["price"] * quantity
        if player["cash"] < total:
            return False, "Not enough cash."
        player["cash"] -= total
        player["inventory"][weapon_key] = player["inventory"].get(weapon_key, 0) + quantity
        return True, f"Purchased {quantity}x {weapon['name']}."

    def buy_item(self, player_id: int, item_key: str) -> tuple[bool, str]:
        player = self.player_by_id(player_id)
        item = self.ITEMS.get(item_key)
        if player is None:
            return False, "Player not found."
        if item is None:
            return False, "Unknown item."
        if not self.is_players_turn(player_id):
            return False, "You can only shop on your turn."
        if player["cash"] < item["price"]:
            return False, "Not enough cash."
        player["cash"] -= item["price"]
        if "absorption" in item:
            player["shield"] = item_key
            player["shield_hp"] = int(item["absorption"] * 100)
        else:
            player["items"][item_key] = player["items"].get(item_key, 0) + 1
        return True, f"Purchased {item['name']}."

    def start_shot(self, player_id: int, weapon_key: str, angle: int, power: int) -> tuple[bool, str, Optional[Dict[str, Any]]]:
        if len(self.players) < 2:
            return False, "Need at least two players.", None
        if not self.is_players_turn(player_id):
            return False, "It is not your turn.", None
        weapon = self.WEAPONS.get(weapon_key)
        if weapon is None:
            return False, "Unknown weapon.", None
        player = self.player_by_id(player_id)
        if player is None or player["hp"] <= 0:
            return False, "Player not available.", None

        stock = player["inventory"].get(weapon_key, 0)
        if stock == 0:
            return False, "Out of stock for that weapon.", None
        if stock > 0:
            player["inventory"][weapon_key] = stock - 1

        clamped_angle = max(0, min(180, int(angle)))
        clamped_power = max(5, min(240, int(power)))
        player["angle"] = clamped_angle
        player["power"] = clamped_power

        self.pending_shot_player_id = player_id
        self.pending_shot_weapon = weapon_key
        self.pending_sub_remaining = weapon.get("sub_count", 1)
        idx = self.player_index(player_id)
        payload = {
            "type": "fire",
            "playerIndex": idx,
            "playerId": player_id,
            "weapon": weapon_key,
            "weaponData": {
                "name": weapon["name"],
                "color": weapon["color"],
                "radius": weapon["radius"],
                "special": weapon.get("special"),
                "sub_count": weapon.get("sub_count", 1),
                "is_dirt": weapon.get("is_dirt", False),
            },
            "angle": clamped_angle,
            "power": clamped_power,
            "wind": self.wind,
            "players": self.players,
        }
        return True, "", payload

    def apply_explosion(self, x: float, y: float, radius: float, is_dirt: bool) -> Dict[str, Any]:
        x = float(max(0, min(self.width - 1, x)))
        y = float(max(0, min(self.ground_height, y)))
        radius = float(max(5, min(200, radius)))

        for i in range(max(0, int(x - radius)), min(self.width, int(x + radius))):
            dx = i - x
            distance_sq = (radius * radius) - (dx * dx)
            if distance_sq <= 0:
                continue
            delta = distance_sq ** 0.5
            if is_dirt:
                # Dirt weapon: raise terrain (fill / bury)
                fill_top = y + delta * 0.5
                if fill_top > self.terrain[i]:
                    self.terrain[i] = min(self.ground_height - 20, fill_top)
            else:
                # Regular weapon: lower terrain (crater)
                crater_floor = y - delta
                if crater_floor < self.terrain[i]:
                    self.terrain[i] = max(30, crater_floor)

        weapon_key = self.pending_shot_weapon or "missile"
        weapon = self.WEAPONS.get(weapon_key, self.WEAPONS["missile"])
        max_damage = int(weapon.get("damage", 55))
        attacker = self.player_by_id(self.pending_shot_player_id) if self.pending_shot_player_id else None
        awards: list[dict[str, Any]] = []
        status_lines: list[str] = []
        for player in self.players:
            if player["hp"] <= 0:
                continue
            distance = ((player["x"] - x) ** 2 + (player["y"] - y) ** 2) ** 0.5
            if distance < radius and not is_dirt:
                damage = int((1 - distance / radius) * max_damage)
                damage = max(1, damage)
                # Shield absorption
                if player.get("shield") and player.get("shield_hp", 0) > 0:
                    absorbed = min(damage, player["shield_hp"])
                    damage -= absorbed
                    player["shield_hp"] -= absorbed
                    if player["shield_hp"] <= 0:
                        player["shield"] = None
                        player["shield_hp"] = 0
                if damage > 0:
                    prev_hp = player["hp"]
                    player["hp"] = max(0, player["hp"] - damage)
                    if attacker and attacker["id"] != player["id"]:
                        reward = damage * self.DAMAGE_CASH_PER_HP
                        attacker["cash"] += reward
                        score_gain = damage
                        award = {
                            "attacker_id": attacker["id"],
                            "target_id": player["id"],
                            "target_name": player["name"],
                            "damage": damage,
                            "cash": reward,
                            "kill": False,
                        }
                        if prev_hp > 0 and player["hp"] == 0:
                            attacker["cash"] += self.KILL_BONUS_CASH
                            award["cash"] += self.KILL_BONUS_CASH
                            award["kill"] = True
                            score_gain += 50
                        self.scores[attacker["id"]] = self.scores.get(attacker["id"], 0) + score_gain
                        attacker["score"] = self.scores[attacker["id"]]
                        awards.append(award)

        # Fall damage from terrain deformation
        for player in self.players:
            if player["hp"] <= 0:
                continue
            old_y = player["y"]
            new_y = self.terrain[int(player["x"])]
            player["y"] = new_y
            fall_distance = old_y - new_y
            if fall_distance > 10:
                has_parachute = player.get("items", {}).get("parachute", 0) > 0
                if has_parachute:
                    player["items"]["parachute"] -= 1
                    status_lines.append(f"{player['name']}'s parachute deployed!")
                else:
                    fall_damage = int(fall_distance * 0.4)
                    if fall_damage > 0:
                        player["hp"] = max(0, player["hp"] - fall_damage)
                        status_lines.append(f"{player['name']} fell and took {fall_damage} damage!")

        for award in awards:
            attacker_p = self.player_by_id(award["attacker_id"])
            target_name = award.get("target_name", "?")
            if attacker_p:
                if award["kill"]:
                    status_lines.append(
                        f"{attacker_p['name']} destroyed {target_name}! +${award['cash']}"
                    )
                else:
                    status_lines.append(
                        f"{attacker_p['name']} hit {target_name} for {award['damage']} dmg (+${award['cash']})"
                    )

        # Track sub-explosions for multi-warhead weapons
        self.pending_sub_remaining -= 1
        if self.pending_sub_remaining <= 0:
            self.pending_shot_player_id = None
            self.pending_shot_weapon = None
            self.pending_sub_remaining = 0

        # Check for round end
        alive = [p for p in self.players if p["hp"] > 0]
        round_over = len(alive) <= 1 and len(self.players) >= 2
        winner = None
        if round_over and alive:
            winner = alive[0]
            winner["cash"] += self.ROUND_SURVIVOR_BONUS
            self.scores[winner["id"]] = self.scores.get(winner["id"], 0) + 100
            winner["score"] = self.scores[winner["id"]]

        return {
            "awards": awards,
            "status": status_lines,
            "round_over": round_over,
            "round_winner": winner["name"] if winner else None,
            "round_number": self.round_number,
            "all_done": self.pending_sub_remaining <= 0,
        }

    def start_new_round(self):
        self.round_number += 1
        self.round_active = True
        self.generate_terrain()
        self._roll_wind()
        for player in self.players:
            player["hp"] = 100
            player["shield"] = None
            player["shield_hp"] = 0
            x_pos = self._spawn_x()
            player["x"] = x_pos
            player["y"] = self.terrain[x_pos]
            for key, wpn in self.WEAPONS.items():
                player["inventory"][key] = wpn["stock"]
        self.pending_shot_player_id = None
        self.pending_shot_weapon = None
        self.pending_sub_remaining = 0
        self.current_turn_index = 0
        if self.players:
            self.current_turn_index = self._next_alive_index(0)

    def _next_alive_index(self, start_index: int) -> int:
        if not self.players:
            return 0
        for offset in range(len(self.players)):
            idx = (start_index + offset) % len(self.players)
            if self.players[idx]["hp"] > 0:
                return idx
        return 0

    def advance_turn(self):
        if not self.players:
            return
        next_index = self.current_turn_index
        for _ in range(len(self.players)):
            next_index = (next_index + 1) % len(self.players)
            if self.players[next_index]["hp"] > 0:
                self.current_turn_index = next_index
                break
        self._roll_wind()

    def get_state(self):
        return {
            "type": "game_state",
            "players": self.players,
            "terrain": self.terrain,
            "turn": self.current_turn_index,
            "wind": self.wind,
            "weapons": self.WEAPONS,
            "items": self.ITEMS,
            "physics": self.PHYSICS,
            "shop_open": len(self.players) >= 2,
            "round_number": self.round_number,
            "max_rounds": self.max_rounds,
            "scores": self.scores,
            "terrain_type": self.terrain_type,
        }

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.player_by_socket: Dict[WebSocket, int] = {}
        self.game = ScorchGame()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        self.player_by_socket.pop(websocket, None)

    def assign_player(self, websocket: WebSocket, player_id: int):
        self.player_by_socket[websocket] = player_id

    def player_id_for(self, websocket: WebSocket) -> Optional[int]:
        return self.player_by_socket.get(websocket)

    async def send_personal(self, websocket: WebSocket, message: dict):
        await websocket.send_text(json.dumps(message))

    async def broadcast(self, message: dict):
        stale_connections: list[WebSocket] = []
        for connection in list(self.active_connections):
            try:
                await connection.send_text(json.dumps(message))
            except Exception:
                stale_connections.append(connection)
        for stale in stale_connections:
            self.disconnect(stale)

    def clear_assignments(self):
        self.player_by_socket.clear()

manager = ConnectionManager()

# --- Routes ---

@router.get("/", response_class=HTMLResponse)
async def games_index(
    request: Request,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
    db: AsyncSession = Depends(database.get_db)
):
    # Fetch active lobbies
    result = await db.execute(
        select(models.GameLobby)
        .where(or_(models.GameLobby.status.is_(None), models.GameLobby.status != "closed"))
        .order_by(models.GameLobby.created_at.desc())
    )
    lobbies = result.scalars().all()
    
    active_modules = await server_utils.get_active_modules(db)

    return templates.TemplateResponse(
        request=request,
        name="games/index.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "lobbies": lobbies,
            "active_modules": active_modules,
            "play_mode_options": PLAY_MODE_OPTIONS,
            "genre_options": GENRE_OPTIONS,
        }
    )

@router.post("/lobby/add")
async def add_lobby(
    server_name: str = Form(...),
    game_type: str = Form(...),
    play_mode: str = Form("multiplayer"),
    genre: str = Form("general"),
    max_players: int = Form(8),
    join_url: Optional[str] = Form(None),
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db)
):
    default_mode, default_genre = _default_tags_for_game_type(game_type)
    inferred_join_url = _default_join_url_for_game_type(game_type)
    normalized_join_url = (join_url or "").strip() or inferred_join_url
    lobby = models.GameLobby(
        server_name=server_name.strip(),
        game_type=game_type.strip(),
        play_mode=_normalize_play_mode(play_mode, fallback=default_mode),
        genre=_normalize_genre(genre, fallback=default_genre),
        host_username=f"@{current_user.username}",
        player_count=0,
        max_players=max_players,
        join_url=normalized_join_url,
        user_id=current_user.id,
        status="pending"
    )
    db.add(lobby)
    await db.commit()
    await db.refresh(lobby)
    return RedirectResponse(url=f"/games?created_lobby_id={lobby.id}", status_code=303)


@router.post("/lobby/{lobby_id}/launch")
async def launch_lobby(
    lobby_id: int,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    result = await db.execute(select(models.GameLobby).where(models.GameLobby.id == lobby_id))
    lobby = result.scalar_one_or_none()
    if not lobby:
        raise HTTPException(status_code=404, detail="Lobby not found")
    if lobby.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the lobby creator can launch this game")

    if not lobby.join_url:
        lobby.join_url = _default_join_url_for_game_type(lobby.game_type)
    if not lobby.join_url:
        raise HTTPException(status_code=400, detail="This lobby has no launch target configured")

    lobby.status = "online"
    lobby.player_count = max(1, int(lobby.player_count or 0))
    await db.commit()
    return RedirectResponse(url=f"/games/lobby/{lobby.id}/join", status_code=303)


@router.post("/lobby/{lobby_id}/close")
async def close_lobby(
    lobby_id: int,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    result = await db.execute(select(models.GameLobby).where(models.GameLobby.id == lobby_id))
    lobby = result.scalar_one_or_none()
    if not lobby:
        raise HTTPException(status_code=404, detail="Lobby not found")

    is_admin = bool(getattr(current_user, "role", "") == "admin" or getattr(current_user, "is_admin", False))
    is_host = bool(current_user.id == lobby.user_id)
    if not (is_admin or is_host):
        raise HTTPException(status_code=403, detail="Only the lobby host or an admin can close this lobby")

    lobby.status = "closed"
    lobby.player_count = 0
    await db.commit()
    return RedirectResponse(url="/games", status_code=303)


@router.get("/lobby/{lobby_id}/join")
async def join_lobby(
    lobby_id: int,
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db),
):
    result = await db.execute(select(models.GameLobby).where(models.GameLobby.id == lobby_id))
    lobby = result.scalar_one_or_none()
    if not lobby:
        raise HTTPException(status_code=404, detail="Lobby not found")
    if lobby.status == "closed":
        raise HTTPException(status_code=400, detail="Lobby is closed")
    if lobby.status != "online":
        raise HTTPException(status_code=400, detail="Lobby is not launched yet")
    if not lobby.join_url:
        raise HTTPException(status_code=400, detail="Lobby launch target is missing")

    if current_user.id != lobby.user_id:
        current_count = int(lobby.player_count or 0)
        max_players = int(lobby.max_players or 0)
        if max_players > 0 and current_count >= max_players:
            raise HTTPException(status_code=400, detail="Lobby is full")
        lobby.player_count = current_count + 1
        await db.commit()

    return RedirectResponse(url=lobby.join_url, status_code=303)

@router.get("/scorch", response_class=HTMLResponse)
async def play_scorch(
    request: Request,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
    db: AsyncSession = Depends(database.get_db)
):
    active_modules = await server_utils.get_active_modules(db)

    return templates.TemplateResponse(
        request=request,
        name="games/scorch.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules
        }
    )

@router.websocket("/scorch/ws")
async def scorch_socket(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        await manager.send_personal(websocket, manager.game.get_state())
        
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
            except json.JSONDecodeError:
                continue
            message_type = message.get("type")
            
            if message_type == "join":
                existing_player_id = manager.player_id_for(websocket)
                if existing_player_id is not None:
                    await manager.send_personal(websocket, {"type": "joined_ack", "player_id": existing_player_id})
                    continue

                raw_name = str(message.get("name", "")).strip()
                clean_name = raw_name[:15] if raw_name else f"Guest_{random.randint(100, 999)}"
                color = "#%06x" % random.randint(0, 0xFFFFFF)
                player = manager.game.add_player(clean_name, color)
                if player is None:
                    await manager.send_personal(websocket, {"type": "error", "message": "Lobby is full."})
                    continue
                manager.assign_player(websocket, player["id"])
                await manager.send_personal(
                    websocket,
                    {"type": "joined_ack", "player_id": player["id"], "player_name": player["name"]},
                )
                await manager.broadcast(manager.game.get_state())

            elif message_type == "shop_buy":
                player_id = manager.player_id_for(websocket)
                if player_id is None:
                    await manager.send_personal(websocket, {"type": "error", "message": "Join before shopping."})
                    continue
                weapon_key = str(message.get("weapon", "")).strip().lower()
                try:
                    quantity = int(message.get("qty", 1))
                except (TypeError, ValueError):
                    quantity = 1
                ok, text = manager.game.buy_weapon(player_id, weapon_key, quantity)
                if not ok:
                    await manager.send_personal(websocket, {"type": "error", "message": text})
                    continue
                await manager.broadcast({"type": "economy_update", "players": manager.game.players})
                await manager.send_personal(websocket, {"type": "shop_result", "message": text})
            elif message_type == "buy_item":
                player_id = manager.player_id_for(websocket)
                if player_id is None:
                    await manager.send_personal(websocket, {"type": "error", "message": "Join before shopping."})
                    continue
                item_key = str(message.get("item", "")).strip().lower()
                ok, text = manager.game.buy_item(player_id, item_key)
                if not ok:
                    await manager.send_personal(websocket, {"type": "error", "message": text})
                    continue
                await manager.broadcast({"type": "economy_update", "players": manager.game.players})
                await manager.send_personal(websocket, {"type": "shop_result", "message": text})


            elif message_type == "fire":
                player_id = manager.player_id_for(websocket)
                if player_id is None:
                    await manager.send_personal(websocket, {"type": "error", "message": "Join before firing."})
                    continue
                weapon_key = str(message.get("weapon", "missile")).strip().lower() or "missile"
                try:
                    angle = int(message.get("angle", 45))
                except (TypeError, ValueError):
                    angle = 45
                try:
                    power = int(message.get("power", 70))
                except (TypeError, ValueError):
                    power = 70
                ok, error, payload = manager.game.start_shot(player_id, weapon_key, angle, power)
                if not ok or payload is None:
                    await manager.send_personal(websocket, {"type": "error", "message": error})
                    continue
                await manager.broadcast(payload)
            
            elif message_type == "explosion":
                player_id = manager.player_id_for(websocket)
                if player_id is None:
                    continue
                if manager.game.pending_shot_player_id != player_id:
                    continue
                weapon_key = manager.game.pending_shot_weapon or "missile"
                weapon = manager.game.WEAPONS.get(weapon_key, manager.game.WEAPONS["missile"])
                try:
                    x = float(message.get("x", 0))
                    y = float(message.get("y", 0))
                except (TypeError, ValueError):
                    continue
                try:
                    radius = float(message.get("radius", weapon["radius"]))
                except (TypeError, ValueError):
                    radius = float(weapon["radius"])
                is_dirt = bool(weapon.get("is_dirt", False))
                explosion_result = manager.game.apply_explosion(x, y, radius, is_dirt)
                await manager.broadcast({
                    "type": "terrain_update",
                    "terrain": manager.game.terrain,
                    "players": manager.game.players,
                    "awards": explosion_result["awards"],
                    "status": explosion_result["status"],
                    "round_over": explosion_result.get("round_over", False),
                    "round_winner": explosion_result.get("round_winner"),
                    "round_number": explosion_result.get("round_number", 1),
                    "all_done": explosion_result.get("all_done", True),
                })

            elif message_type == "next_turn":
                player_id = manager.player_id_for(websocket)
                if player_id is None:
                    continue
                current_turn_player = None
                if manager.game.players:
                    current_turn_player = manager.game.players[manager.game.current_turn_index]["id"]
                if player_id != current_turn_player:
                    continue
                manager.game.advance_turn()
                await manager.broadcast({
                    "type": "turn_update",
                    "turn": manager.game.current_turn_index,
                    "wind": manager.game.wind,
                    "players": manager.game.players,
                })

            elif message_type == "new_round":
                if manager.game.round_number < manager.game.max_rounds:
                    manager.game.start_new_round()
                    await manager.broadcast(manager.game.get_state())

            elif message_type == "reset":
                terrain_type = str(message.get("terrain_type", "random")).strip().lower()
                manager.game = ScorchGame(terrain_type)
                manager.clear_assignments()
                await manager.broadcast(manager.game.get_state())

    except WebSocketDisconnect:
        player_id = manager.player_id_for(websocket)
        manager.disconnect(websocket)
        if player_id is not None:
            manager.game.remove_player(player_id)
            await manager.broadcast(manager.game.get_state())

@router.get("/2048", response_class=HTMLResponse)
async def play_2048(
    request: Request,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
    db: AsyncSession = Depends(database.get_db)
):
    active_modules = await server_utils.get_active_modules(db)

    return templates.TemplateResponse(
        request=request,
        name="games/2048.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules
        }
    )


@router.get("/tetris", response_class=HTMLResponse)
async def play_tetris(
    request: Request,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
    db: AsyncSession = Depends(database.get_db)
):
    active_modules = await server_utils.get_active_modules(db)

    return templates.TemplateResponse(
        request=request,
        name="games/tetris.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules
        }
    )


@router.get("/crossword", response_class=HTMLResponse)
async def play_crossword(
    request: Request,
    publish_date: Optional[str] = None,
    edition: Optional[str] = None,
    reveal: bool = False,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
    db: AsyncSession = Depends(database.get_db),
):
    selected_date = _parse_crossword_date(publish_date)
    try:
        selected_edition = crossword_service.normalize_edition(
            edition or crossword_service.default_edition_for_date(selected_date)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if selected_date.weekday() == 6:
        await crossword_service.publish_crossword(db, selected_date, "sunday")

    puzzle = await crossword_service.publish_crossword(db, selected_date, selected_edition)
    puzzle_payload = crossword_service.decode_payload_json(puzzle.payload_json or "{}")
    archive_entries = await crossword_service.list_crosswords(db, limit=45)
    active_modules = await server_utils.get_active_modules(db)

    available_editions = ["daily"]
    if selected_date.weekday() == 6:
        available_editions.append("sunday")
    if selected_edition not in available_editions:
        available_editions.append(selected_edition)

    return templates.TemplateResponse(
        request=request,
        name="games/crossword.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "active_modules": active_modules,
            "puzzle": puzzle,
            "puzzle_payload": puzzle_payload,
            "archive_entries": archive_entries,
            "selected_date": selected_date.isoformat(),
            "selected_edition": selected_edition,
            "available_editions": available_editions,
            "prev_date": (selected_date - timedelta(days=1)).isoformat(),
            "next_date": (selected_date + timedelta(days=1)).isoformat(),
            "reveal": bool(reveal),
        },
    )


@router.get("/api/crossword/today")
async def crossword_today_api(
    edition: Optional[str] = None,
    include_payload: bool = True,
    db: AsyncSession = Depends(database.get_db),
):
    today = crossword_service.local_today()
    try:
        selected_edition = crossword_service.normalize_edition(
            edition or crossword_service.default_edition_for_date(today)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    daily_puzzle = await crossword_service.publish_crossword(db, today, "daily")
    selected_puzzle = await crossword_service.publish_crossword(db, today, selected_edition)
    response: dict[str, Any] = {
        "date": today.isoformat(),
        "selected_edition": selected_edition,
        "selected": crossword_service.serialize_puzzle(selected_puzzle, include_payload=include_payload),
        "daily": crossword_service.serialize_puzzle(daily_puzzle, include_payload=False),
    }
    if today.weekday() == 6:
        sunday_puzzle = await crossword_service.publish_crossword(db, today, "sunday")
        response["sunday"] = crossword_service.serialize_puzzle(sunday_puzzle, include_payload=False)
    return response


@router.get("/api/crossword/archive")
async def crossword_archive_api(
    edition: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 30,
    include_payload: bool = False,
    db: AsyncSession = Depends(database.get_db),
):
    normalized_edition: Optional[str] = None
    if edition:
        try:
            normalized_edition = crossword_service.normalize_edition(edition)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    parsed_from = _parse_crossword_date(date_from) if date_from else None
    parsed_to = _parse_crossword_date(date_to) if date_to else None
    if parsed_from and parsed_to and parsed_from > parsed_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to.")

    puzzles = await crossword_service.list_crosswords(
        db,
        limit=limit,
        edition=normalized_edition,
        date_from=parsed_from,
        date_to=parsed_to,
    )
    return {
        "count": len(puzzles),
        "items": [
            crossword_service.serialize_puzzle(item, include_payload=include_payload)
            for item in puzzles
        ],
    }


# --- Chess Engine & Match Manager ---

class ChessEngine:
    """Pure Python chess engine with full rule validation."""
    INITIAL_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"

    @staticmethod
    def parse_fen(fen: str) -> Dict[str, Any]:
        parts = fen.split()
        board: List[List[Optional[str]]] = []
        for rank in parts[0].split('/'):
            row: List[Optional[str]] = []
            for ch in rank:
                if ch.isdigit():
                    row.extend([None] * int(ch))
                else:
                    row.append(ch)
            board.append(row)
        return {
            "board": board,
            "turn": parts[1],
            "castling": parts[2],
            "en_passant": parts[3],
            "halfmove": int(parts[4]),
            "fullmove": int(parts[5]),
        }

    @staticmethod
    def to_fen(state: Dict[str, Any]) -> str:
        rows = []
        for rank in state["board"]:
            fen_row = ""
            empty = 0
            for sq in rank:
                if sq is None:
                    empty += 1
                else:
                    if empty > 0:
                        fen_row += str(empty)
                        empty = 0
                    fen_row += sq
            if empty > 0:
                fen_row += str(empty)
            rows.append(fen_row)
        ep = state["en_passant"] if state["en_passant"] else "-"
        cast = state["castling"] if state["castling"] else "-"
        return f"{'/'.join(rows)} {state['turn']} {cast} {ep} {state['halfmove']} {state['fullmove']}"

    @staticmethod
    def sq_to_rc(sq: str) -> tuple:
        return (8 - int(sq[1]), ord(sq[0]) - ord('a'))

    @staticmethod
    def rc_to_sq(r: int, c: int) -> str:
        return chr(c + ord('a')) + str(8 - r)

    @staticmethod
    def piece_color(piece: Optional[str]) -> Optional[str]:
        if piece is None:
            return None
        return 'w' if piece.isupper() else 'b'

    @classmethod
    def _is_square_attacked(cls, board: list, r: int, c: int, by_color: str) -> bool:
        opp_pawn = 'P' if by_color == 'w' else 'p'
        # Pawn attacks
        if by_color == 'w':
            pr = r + 1
            for pc in (c - 1, c + 1):
                if 0 <= pr < 8 and 0 <= pc < 8 and board[pr][pc] == opp_pawn:
                    return True
        else:
            pr = r - 1
            for pc in (c - 1, c + 1):
                if 0 <= pr < 8 and 0 <= pc < 8 and board[pr][pc] == opp_pawn:
                    return True
        # Knight
        opp_knight = 'N' if by_color == 'w' else 'n'
        for dr, dc in ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == opp_knight:
                return True
        # Rook/Queen (straight)
        opp_rook = 'R' if by_color == 'w' else 'r'
        opp_queen = 'Q' if by_color == 'w' else 'q'
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            while 0 <= nr < 8 and 0 <= nc < 8:
                p = board[nr][nc]
                if p is not None:
                    if p == opp_rook or p == opp_queen:
                        return True
                    break
                nr += dr
                nc += dc
        # Bishop/Queen (diagonal)
        opp_bishop = 'B' if by_color == 'w' else 'b'
        for dr, dc in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            nr, nc = r + dr, c + dc
            while 0 <= nr < 8 and 0 <= nc < 8:
                p = board[nr][nc]
                if p is not None:
                    if p == opp_bishop or p == opp_queen:
                        return True
                    break
                nr += dr
                nc += dc
        # King
        opp_king = 'K' if by_color == 'w' else 'k'
        for dr, dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < 8 and 0 <= nc < 8 and board[nr][nc] == opp_king:
                return True
        return False

    @classmethod
    def _find_king(cls, board: list, color: str) -> Optional[tuple]:
        king = 'K' if color == 'w' else 'k'
        for r in range(8):
            for c in range(8):
                if board[r][c] == king:
                    return (r, c)
        return None

    @classmethod
    def _is_in_check(cls, board: list, color: str) -> bool:
        pos = cls._find_king(board, color)
        if pos is None:
            return True
        opp = 'b' if color == 'w' else 'w'
        return cls._is_square_attacked(board, pos[0], pos[1], opp)

    @classmethod
    def _deep_copy_board(cls, board: list) -> list:
        return [row[:] for row in board]

    @classmethod
    def _make_move_internal(cls, state: Dict, fr: int, fc: int, tr: int, tc: int, promo: Optional[str]) -> Dict:
        board = cls._deep_copy_board(state["board"])
        piece = board[fr][fc]
        captured = board[tr][tc]
        color = cls.piece_color(piece)
        opp = 'b' if color == 'w' else 'w'
        pt = piece.upper() if piece else ''
        new_ep = "-"
        new_castling = state["castling"]
        halfmove = state["halfmove"] + 1
        fullmove = state["fullmove"]

        # En passant capture
        if pt == 'P' and cls.rc_to_sq(tr, tc) == state["en_passant"]:
            cap_row = fr
            board[cap_row][tc] = None
            captured = 'p' if color == 'w' else 'P'

        # Move piece
        board[tr][tc] = piece
        board[fr][fc] = None

        # Pawn promotion
        if pt == 'P' and (tr == 0 or tr == 7):
            if promo:
                board[tr][tc] = promo if color == 'b' else promo.upper()
            else:
                board[tr][tc] = 'Q' if color == 'w' else 'q'

        # Pawn double push -> en passant target
        if pt == 'P' and abs(tr - fr) == 2:
            ep_row = (fr + tr) // 2
            new_ep = cls.rc_to_sq(ep_row, fc)

        # Castling move
        if pt == 'K':
            if tc - fc == 2:  # kingside
                board[fr][7] = None
                board[fr][5] = 'R' if color == 'w' else 'r'
            elif fc - tc == 2:  # queenside
                board[fr][0] = None
                board[fr][3] = 'R' if color == 'w' else 'r'

        # Update castling rights
        if pt == 'K':
            if color == 'w':
                new_castling = new_castling.replace('K', '').replace('Q', '')
            else:
                new_castling = new_castling.replace('k', '').replace('q', '')
        if pt == 'R':
            if color == 'w':
                if fr == 7 and fc == 7:
                    new_castling = new_castling.replace('K', '')
                elif fr == 7 and fc == 0:
                    new_castling = new_castling.replace('Q', '')
            else:
                if fr == 0 and fc == 7:
                    new_castling = new_castling.replace('k', '')
                elif fr == 0 and fc == 0:
                    new_castling = new_castling.replace('q', '')
        # Rook captured
        if tr == 0 and tc == 7:
            new_castling = new_castling.replace('k', '')
        if tr == 0 and tc == 0:
            new_castling = new_castling.replace('q', '')
        if tr == 7 and tc == 7:
            new_castling = new_castling.replace('K', '')
        if tr == 7 and tc == 0:
            new_castling = new_castling.replace('Q', '')
        if not new_castling:
            new_castling = '-'

        # Halfmove clock
        if pt == 'P' or captured is not None:
            halfmove = 0
        # Fullmove
        if color == 'b':
            fullmove += 1

        return {
            "board": board,
            "turn": opp,
            "castling": new_castling,
            "en_passant": new_ep,
            "halfmove": halfmove,
            "fullmove": fullmove,
        }

    @classmethod
    def _pseudo_moves(cls, state: Dict, r: int, c: int) -> List[tuple]:
        board = state["board"]
        piece = board[r][c]
        if piece is None:
            return []
        color = cls.piece_color(piece)
        opp = 'b' if color == 'w' else 'w'
        pt = piece.upper()
        moves: List[tuple] = []

        if pt == 'P':
            direction = -1 if color == 'w' else 1
            start_row = 6 if color == 'w' else 1
            promo_row = 0 if color == 'w' else 7
            # Forward one
            nr = r + direction
            if 0 <= nr < 8 and board[nr][c] is None:
                if nr == promo_row:
                    for p in ('q', 'r', 'b', 'n'):
                        moves.append((nr, c, p))
                else:
                    moves.append((nr, c, None))
                    # Forward two from start
                    if r == start_row:
                        nr2 = r + 2 * direction
                        if board[nr2][c] is None:
                            moves.append((nr2, c, None))
            # Captures
            for dc in (-1, 1):
                nc = c + dc
                if 0 <= nc < 8 and 0 <= nr < 8:
                    target = board[nr][nc]
                    if target is not None and cls.piece_color(target) == opp:
                        if nr == promo_row:
                            for p in ('q', 'r', 'b', 'n'):
                                moves.append((nr, nc, p))
                        else:
                            moves.append((nr, nc, None))
            # En passant
            if state["en_passant"] != '-':
                ep_r, ep_c = cls.sq_to_rc(state["en_passant"])
                if ep_r == r + direction and abs(ep_c - c) == 1:
                    moves.append((ep_r, ep_c, None))

        elif pt == 'N':
            for dr, dc in ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < 8 and 0 <= nc < 8:
                    t = board[nr][nc]
                    if t is None or cls.piece_color(t) == opp:
                        moves.append((nr, nc, None))

        elif pt in ('B', 'R', 'Q'):
            dirs = []
            if pt in ('B', 'Q'):
                dirs += [(-1, -1), (-1, 1), (1, -1), (1, 1)]
            if pt in ('R', 'Q'):
                dirs += [(-1, 0), (1, 0), (0, -1), (0, 1)]
            for dr, dc in dirs:
                nr, nc = r + dr, c + dc
                while 0 <= nr < 8 and 0 <= nc < 8:
                    t = board[nr][nc]
                    if t is None:
                        moves.append((nr, nc, None))
                    elif cls.piece_color(t) == opp:
                        moves.append((nr, nc, None))
                        break
                    else:
                        break
                    nr += dr
                    nc += dc

        elif pt == 'K':
            for dr, dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < 8 and 0 <= nc < 8:
                    t = board[nr][nc]
                    if t is None or cls.piece_color(t) == opp:
                        moves.append((nr, nc, None))
            # Castling
            castling = state["castling"]
            back_row = 7 if color == 'w' else 0
            if r == back_row and c == 4:
                ks = 'K' if color == 'w' else 'k'
                qs = 'Q' if color == 'w' else 'q'
                if ks in castling:
                    if board[back_row][5] is None and board[back_row][6] is None:
                        if not cls._is_in_check(board, color) and \
                           not cls._is_square_attacked(board, back_row, 5, opp) and \
                           not cls._is_square_attacked(board, back_row, 6, opp):
                            moves.append((back_row, 6, None))
                if qs in castling:
                    if board[back_row][3] is None and board[back_row][2] is None and board[back_row][1] is None:
                        if not cls._is_in_check(board, color) and \
                           not cls._is_square_attacked(board, back_row, 3, opp) and \
                           not cls._is_square_attacked(board, back_row, 2, opp):
                            moves.append((back_row, 2, None))

        return moves

    @classmethod
    def get_legal_moves(cls, state: Dict) -> List[Dict[str, Any]]:
        moves = []
        turn = state["turn"]
        for r in range(8):
            for c in range(8):
                piece = state["board"][r][c]
                if piece is None or cls.piece_color(piece) != turn:
                    continue
                for (tr, tc, promo) in cls._pseudo_moves(state, r, c):
                    new_state = cls._make_move_internal(state, r, c, tr, tc, promo)
                    if not cls._is_in_check(new_state["board"], turn):
                        moves.append({
                            "from": cls.rc_to_sq(r, c),
                            "to": cls.rc_to_sq(tr, tc),
                            "promotion": promo,
                        })
        return moves

    @classmethod
    def make_move(cls, state: Dict, from_sq: str, to_sq: str, promotion: Optional[str] = None) -> Optional[Dict]:
        legal = cls.get_legal_moves(state)
        for m in legal:
            if m["from"] == from_sq and m["to"] == to_sq:
                if m["promotion"] is not None and promotion is not None:
                    if m["promotion"] != promotion:
                        continue
                elif m["promotion"] is not None and promotion is None:
                    # Default to queen
                    if m["promotion"] != 'q':
                        continue
                fr, fc = cls.sq_to_rc(from_sq)
                tr, tc = cls.sq_to_rc(to_sq)
                promo = promotion or m.get("promotion")
                return cls._make_move_internal(state, fr, fc, tr, tc, promo)
        return None

    @classmethod
    def game_status(cls, state: Dict) -> str:
        legal = cls.get_legal_moves(state)
        if len(legal) == 0:
            if cls._is_in_check(state["board"], state["turn"]):
                return "checkmate"
            return "stalemate"
        if cls._is_in_check(state["board"], state["turn"]):
            return "check"
        if state["halfmove"] >= 100:
            return "draw_50"
        # Insufficient material
        pieces = []
        for r in range(8):
            for c in range(8):
                p = state["board"][r][c]
                if p is not None:
                    pieces.append(p)
        if len(pieces) == 2:
            return "draw_material"
        if len(pieces) == 3:
            for p in pieces:
                if p.upper() in ('B', 'N'):
                    return "draw_material"
        return "active"

    @classmethod
    def get_moves_for_square(cls, state: Dict, sq: str) -> List[str]:
        legal = cls.get_legal_moves(state)
        return list(set(m["to"] for m in legal if m["from"] == sq))

    @classmethod
    def move_to_algebraic(cls, state: Dict, from_sq: str, to_sq: str, promotion: Optional[str] = None) -> str:
        board = state["board"]
        fr, fc = cls.sq_to_rc(from_sq)
        tr, tc = cls.sq_to_rc(to_sq)
        piece = board[fr][fc]
        if piece is None:
            return f"{from_sq}{to_sq}"
        pt = piece.upper()
        captured = board[tr][tc]
        # En passant capture
        is_ep = (pt == 'P' and tc != fc and captured is None)
        if is_ep:
            captured = 'p'
        # Castling
        if pt == 'K' and abs(tc - fc) == 2:
            return "O-O" if tc > fc else "O-O-O"
        notation = ""
        if pt != 'P':
            notation += pt
            # Disambiguation
            same_piece_moves = []
            turn = state["turn"]
            for r2 in range(8):
                for c2 in range(8):
                    p2 = board[r2][c2]
                    if p2 and p2 == piece and (r2, c2) != (fr, fc):
                        for m in cls._pseudo_moves(state, r2, c2):
                            if m[0] == tr and m[1] == tc:
                                ns = cls._make_move_internal(state, r2, c2, tr, tc, m[2])
                                if not cls._is_in_check(ns["board"], turn):
                                    same_piece_moves.append((r2, c2))
                                    break
            if same_piece_moves:
                same_file = any(c2 == fc for _, c2 in same_piece_moves)
                same_rank = any(r2 == fr for r2, _ in same_piece_moves)
                if not same_file:
                    notation += chr(fc + ord('a'))
                elif not same_rank:
                    notation += str(8 - fr)
                else:
                    notation += from_sq
        if captured is not None:
            if pt == 'P':
                notation += chr(fc + ord('a'))
            notation += 'x'
        notation += to_sq
        if promotion:
            notation += '=' + promotion.upper()
        # Check/checkmate after move
        new_state = cls._make_move_internal(state, fr, fc, tr, tc, promotion)
        if cls._is_in_check(new_state["board"], new_state["turn"]):
            new_legal = cls.get_legal_moves(new_state)
            notation += '#' if len(new_legal) == 0 else '+'
        return notation


class ChessMatchManager:
    """Manages active WebSocket connections for chess games."""

    def __init__(self):
        self.connections: Dict[int, Dict[int, WebSocket]] = {}  # game_id -> {user_id: ws}
        self.spectators: Dict[int, List[WebSocket]] = {}  # game_id -> [ws, ...]
        self.move_history: Dict[int, List[str]] = {}  # game_id -> ["e2e4", ...]

    async def connect(self, game_id: int, websocket: WebSocket):
        await websocket.accept()

    def add_player(self, game_id: int, user_id: int, websocket: WebSocket):
        if game_id not in self.connections:
            self.connections[game_id] = {}
        self.connections[game_id][user_id] = websocket

    def add_spectator(self, game_id: int, websocket: WebSocket):
        if game_id not in self.spectators:
            self.spectators[game_id] = []
        self.spectators[game_id].append(websocket)

    def disconnect(self, game_id: int, websocket: WebSocket):
        if game_id in self.connections:
            to_remove = [uid for uid, ws in self.connections[game_id].items() if ws is websocket]
            for uid in to_remove:
                del self.connections[game_id][uid]
            if not self.connections[game_id]:
                del self.connections[game_id]
        if game_id in self.spectators:
            self.spectators[game_id] = [ws for ws in self.spectators[game_id] if ws is not websocket]
            if not self.spectators[game_id]:
                del self.spectators[game_id]

    async def broadcast(self, game_id: int, message: dict):
        stale = []
        targets = []
        if game_id in self.connections:
            targets.extend(self.connections[game_id].values())
        if game_id in self.spectators:
            targets.extend(self.spectators[game_id])
        for ws in targets:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                stale.append(ws)
        for ws in stale:
            self.disconnect(game_id, ws)

    async def send_personal(self, websocket: WebSocket, message: dict):
        await websocket.send_text(json.dumps(message))

chess_mgr = ChessMatchManager()


async def _get_user_from_ws_cookie(websocket: WebSocket):
    """Extract user from WebSocket cookie."""
    cookie_token = websocket.cookies.get("access_token")
    if not cookie_token or not cookie_token.startswith("Bearer "):
        return None
    token = cookie_token[7:]
    try:
        from ..auth_utils import jwt, JWTError
        from ..crud_users import get_user_by_username
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username = payload.get("sub")
        if not username:
            return None
        async with database.AsyncSessionLocal() as db:
            user = await get_user_by_username(db, username=username)
            return user
    except Exception:
        return None


@router.websocket("/chess/{game_id}/ws")
async def chess_ws(game_id: int, websocket: WebSocket):
    await chess_mgr.connect(game_id, websocket)
    user = await _get_user_from_ws_cookie(websocket)
    user_id = user.id if user else None
    username = user.username if user else None

    # Load game from DB
    async with database.AsyncSessionLocal() as db:
        result = await db.execute(
            select(models.ChessGame)
            .options(selectinload(models.ChessGame.white_player), selectinload(models.ChessGame.black_player))
            .where(models.ChessGame.id == game_id)
        )
        game = result.scalar_one_or_none()
        if not game:
            await chess_mgr.send_personal(websocket, {"type": "error", "message": "Game not found."})
            return

        fen = game.fen
        game_status_val = game.status
        white_name = game.white_player.username if game.white_player else None
        black_name = game.black_player.username if game.black_player else None
        white_id = game.white_player_id
        black_id = game.black_player_id

    # Assign player or spectator
    my_color = None
    if user_id and user_id == white_id:
        chess_mgr.add_player(game_id, user_id, websocket)
        my_color = "white"
    elif user_id and user_id == black_id:
        chess_mgr.add_player(game_id, user_id, websocket)
        my_color = "black"
    else:
        chess_mgr.add_spectator(game_id, websocket)

    # Send initial state
    state = ChessEngine.parse_fen(fen)
    legal_moves = ChessEngine.get_legal_moves(state) if game_status_val == "active" else []
    history = chess_mgr.move_history.get(game_id, [])
    status_text = ChessEngine.game_status(state) if game_status_val == "active" else game_status_val

    await chess_mgr.send_personal(websocket, {
        "type": "state",
        "fen": fen,
        "turn": state["turn"],
        "legal_moves": legal_moves,
        "white_player": white_name,
        "black_player": black_name,
        "white_id": white_id,
        "black_id": black_id,
        "my_color": my_color,
        "my_user_id": user_id,
        "status": status_text,
        "move_history": history,
    })

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue
            msg_type = msg.get("type")

            if msg_type == "move":
                if not user_id:
                    await chess_mgr.send_personal(websocket, {"type": "error", "message": "Not authenticated."})
                    continue

                # Re-load game from DB for latest state
                async with database.AsyncSessionLocal() as db:
                    result = await db.execute(select(models.ChessGame).where(models.ChessGame.id == game_id))
                    game = result.scalar_one_or_none()
                    if not game or game.status != "active":
                        await chess_mgr.send_personal(websocket, {"type": "error", "message": "Game is not active."})
                        continue

                    state = ChessEngine.parse_fen(game.fen)
                    # Check it's this player's turn
                    if state["turn"] == 'w' and user_id != game.white_player_id:
                        await chess_mgr.send_personal(websocket, {"type": "error", "message": "It's not your turn."})
                        continue
                    if state["turn"] == 'b' and user_id != game.black_player_id:
                        await chess_mgr.send_personal(websocket, {"type": "error", "message": "It's not your turn."})
                        continue

                    from_sq = str(msg.get("from", "")).strip().lower()
                    to_sq = str(msg.get("to", "")).strip().lower()
                    promotion = msg.get("promotion")
                    if promotion:
                        promotion = str(promotion).strip().lower()

                    # Generate algebraic notation before move
                    alg = ChessEngine.move_to_algebraic(state, from_sq, to_sq, promotion)

                    new_state = ChessEngine.make_move(state, from_sq, to_sq, promotion)
                    if new_state is None:
                        await chess_mgr.send_personal(websocket, {"type": "error", "message": "Illegal move."})
                        continue

                    new_fen = ChessEngine.to_fen(new_state)
                    gs = ChessEngine.game_status(new_state)
                    db_status = "active"
                    if gs == "checkmate":
                        db_status = "white_win" if new_state["turn"] == 'b' else "black_win"
                        # Oops: checkmate means the side TO MOVE has no moves. So the OTHER side wins.
                        db_status = "black_win" if new_state["turn"] == 'w' else "white_win"
                    elif gs in ("stalemate", "draw_50", "draw_material"):
                        db_status = "draw"

                    game.fen = new_fen
                    game.status = db_status
                    await db.commit()

                    # Update move history
                    if game_id not in chess_mgr.move_history:
                        chess_mgr.move_history[game_id] = []
                    chess_mgr.move_history[game_id].append(alg)

                    legal_moves = ChessEngine.get_legal_moves(new_state) if db_status == "active" else []

                    await chess_mgr.broadcast(game_id, {
                        "type": "move_made",
                        "fen": new_fen,
                        "turn": new_state["turn"],
                        "from": from_sq,
                        "to": to_sq,
                        "promotion": promotion,
                        "algebraic": alg,
                        "legal_moves": legal_moves,
                        "status": gs if db_status == "active" else db_status,
                        "move_history": chess_mgr.move_history[game_id],
                    })

            elif msg_type == "resign":
                if not user_id:
                    continue
                async with database.AsyncSessionLocal() as db:
                    result = await db.execute(select(models.ChessGame).where(models.ChessGame.id == game_id))
                    game = result.scalar_one_or_none()
                    if not game or game.status != "active":
                        continue
                    if user_id == game.white_player_id:
                        game.status = "black_win"
                    elif user_id == game.black_player_id:
                        game.status = "white_win"
                    else:
                        continue
                    await db.commit()
                    await chess_mgr.broadcast(game_id, {
                        "type": "game_over",
                        "status": game.status,
                        "reason": "resignation",
                    })

    except WebSocketDisconnect:
        chess_mgr.disconnect(game_id, websocket)
        if my_color:
            await chess_mgr.broadcast(game_id, {
                "type": "player_disconnect",
                "color": my_color,
            })


@router.get("/chess", response_class=HTMLResponse)
async def chess_list(
    request: Request,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
    db: AsyncSession = Depends(database.get_db)
):
    result = await db.execute(
        select(models.ChessGame)
        .options(selectinload(models.ChessGame.white_player), selectinload(models.ChessGame.black_player))
        .order_by(models.ChessGame.updated_at.desc())
    )
    games = result.scalars().all()
    active_modules = await server_utils.get_active_modules(db)
    return templates.TemplateResponse(
        request=request,
        name="games/chess_list.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "games": games,
            "active_modules": active_modules,
        }
    )


@router.post("/chess/create")
async def create_chess_game(
    as_white: bool = Form(...),
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db)
):
    try:
        new_game = models.ChessGame(
            white_player_id=current_user.id if as_white else None,
            black_player_id=None if as_white else current_user.id,
        )
        db.add(new_game)
        await db.commit()
        await db.refresh(new_game)
        return RedirectResponse(url=f"/games/chess/{new_game.id}", status_code=303)
    except Exception as e:
        return HTMLResponse(content=f"Error creating game: {e}", status_code=500)


@router.get("/chess/{game_id}", response_class=HTMLResponse)
async def view_chess_game(
    game_id: int,
    request: Request,
    current_user: Optional[models.User] = Depends(auth.get_current_user_optional),
    db: AsyncSession = Depends(database.get_db)
):
    result = await db.execute(
        select(models.ChessGame)
        .options(selectinload(models.ChessGame.white_player), selectinload(models.ChessGame.black_player))
        .where(models.ChessGame.id == game_id)
    )
    game = result.scalar_one_or_none()
    if not game:
        return RedirectResponse(url="/games/chess?error=Game+not+found")
    active_modules = await server_utils.get_active_modules(db)
    return templates.TemplateResponse(
        request=request,
        name="games/chess.html",
        context={
            "user": current_user,
            "node_name": settings.NODE_NAME,
            "platform_name": settings.PLATFORM_NAME,
            "game": game,
            "active_modules": active_modules,
        }
    )


@router.post("/chess/{game_id}/join")
async def join_chess_game(
    game_id: int,
    color: str = Form(...),
    current_user: models.User = Depends(auth.get_current_user),
    db: AsyncSession = Depends(database.get_db)
):
    result = await db.execute(select(models.ChessGame).where(models.ChessGame.id == game_id))
    game = result.scalar_one_or_none()
    if game:
        if color == "white" and not game.white_player_id:
            game.white_player_id = current_user.id
        elif color == "black" and not game.black_player_id:
            game.black_player_id = current_user.id
        await db.commit()
    return RedirectResponse(url=f"/games/chess/{game_id}", status_code=303)
