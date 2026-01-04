"""
Camp Half-Blood Web Portal Server
=================================
A complete web server for the Camp Half-Blood bot system.
Designed for deployment on Railway, Render, or any Python hosting platform.

Features:
- Static file serving (documentation site)
- Player authentication via Discord link codes
- Profile, mail, inventory, timeline APIs
- Real-time data sync with MySQL database
- CORS support for frontend access
"""

import asyncio
import os
import json
import secrets
import hashlib
import time
from aiohttp import web
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE CONNECTION
# ═══════════════════════════════════════════════════════════════════════════════

# Try multiple import paths for flexibility
db = None

try:
    from utils.database import db
    logger.info("Imported database from utils.database")
except ImportError:
    try:
        from utils import db
        logger.info("Imported database from utils")
    except ImportError:
        try:
            # For Railway deployment - direct MySQL connection
            import mysql.connector
            from mysql.connector import pooling
            
            class DatabaseManager:
                """Minimal database manager for standalone deployment."""
                
                def __init__(self):
                    self.pool = None
                    self._connect()
                
                def _connect(self):
                    """Create database connection pool."""
                    db_config = {
                        'host': os.environ.get('MYSQL_HOST', 'localhost'),
                        'port': int(os.environ.get('MYSQL_PORT', 3306)),
                        'user': os.environ.get('MYSQL_USER', 'root'),
                        'password': os.environ.get('MYSQL_PASSWORD', ''),
                        'database': os.environ.get('MYSQL_DATABASE', 'camphalfblood'),
                    }
                    
                    try:
                        self.pool = pooling.MySQLConnectionPool(
                            pool_name="chb_pool",
                            pool_size=5,
                            **db_config
                        )
                        logger.info(f"Connected to MySQL at {db_config['host']}:{db_config['port']}")
                    except Exception as e:
                        logger.error(f"Failed to connect to MySQL: {e}")
                        self.pool = None
                
                def _execute(self, query, params=None, fetch_one=False, fetch_all=False):
                    """Execute a query and optionally fetch results."""
                    if not self.pool:
                        return None
                    
                    conn = None
                    cursor = None
                    try:
                        conn = self.pool.get_connection()
                        cursor = conn.cursor(dictionary=True)
                        cursor.execute(query, params or ())
                        
                        if fetch_one:
                            return cursor.fetchone()
                        elif fetch_all:
                            return cursor.fetchall()
                        else:
                            conn.commit()
                            return cursor.lastrowid
                    except Exception as e:
                        logger.error(f"Database error: {e}")
                        return None
                    finally:
                        if cursor:
                            cursor.close()
                        if conn:
                            conn.close()
                
                def get_player(self, user_id):
                    """Get player by Discord user ID."""
                    return self._execute(
                        "SELECT * FROM players WHERE user_id = %s",
                        (user_id,),
                        fetch_one=True
                    )
                
                def get_inventory(self, user_id):
                    """Get player inventory."""
                    return self._execute(
                        "SELECT * FROM inventory WHERE user_id = %s",
                        (user_id,),
                        fetch_all=True
                    )
                
                def get_unread_mail(self, user_id):
                    """Get unread mail for player."""
                    return self._execute(
                        "SELECT * FROM mail WHERE user_id = %s AND is_read = 0 ORDER BY created_at DESC",
                        (user_id,),
                        fetch_all=True
                    )
                
                def get_all_mail(self, user_id):
                    """Get all mail for player."""
                    return self._execute(
                        "SELECT * FROM mail WHERE user_id = %s ORDER BY created_at DESC LIMIT 50",
                        (user_id,),
                        fetch_all=True
                    )
                
                def mark_mail_read(self, mail_id):
                    """Mark mail as read."""
                    return self._execute(
                        "UPDATE mail SET is_read = 1 WHERE mail_id = %s",
                        (mail_id,)
                    )
                
                def delete_mail(self, mail_id, user_id):
                    """Delete mail."""
                    result = self._execute(
                        "DELETE FROM mail WHERE mail_id = %s AND user_id = %s",
                        (mail_id, user_id)
                    )
                    return result is not None
                
                def get_cabin(self, cabin_id):
                    """Get cabin by ID."""
                    return self._execute(
                        "SELECT * FROM cabins WHERE cabin_id = %s",
                        (cabin_id,),
                        fetch_one=True
                    )
                
                def get_minecraft_link(self, discord_id=None):
                    """Get Minecraft link for player."""
                    return self._execute(
                        "SELECT * FROM minecraft_links WHERE discord_id = %s",
                        (discord_id,),
                        fetch_one=True
                    )
                
                def get_player_shop(self, user_id):
                    """Get player's shop."""
                    return self._execute(
                        "SELECT * FROM player_shops WHERE owner_id = %s",
                        (user_id,),
                        fetch_one=True
                    )
                
                def get_transaction_history(self, user_id, limit=20):
                    """Get transaction history."""
                    return self._execute(
                        "SELECT * FROM transactions WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
                        (user_id, limit),
                        fetch_all=True
                    )
                
                def get_timeline_entries(self, category=None, limit=50):
                    """Get timeline entries."""
                    if category:
                        return self._execute(
                            "SELECT * FROM timeline WHERE category = %s ORDER BY event_date DESC LIMIT %s",
                            (category, limit),
                            fetch_all=True
                        )
                    return self._execute(
                        "SELECT * FROM timeline ORDER BY event_date DESC LIMIT %s",
                        (limit,),
                        fetch_all=True
                    )
                
                def get_all_players(self, limit=100):
                    """Get all players for leaderboard."""
                    return self._execute(
                        "SELECT user_id, username, drachma, god_parent FROM players ORDER BY drachma DESC LIMIT %s",
                        (limit,),
                        fetch_all=True
                    )
            
            db = DatabaseManager()
            logger.info("Created standalone DatabaseManager")
            
        except ImportError as e:
            logger.warning(f"Could not set up database: {e}")
            db = None

# Try to import config for additional settings
try:
    import config
except ImportError:
    config = None
    logger.warning("Config module not found, using defaults")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN SERVER CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class CampHalfBloodServer:
    """
    Main web server for Camp Half-Blood portal.
    Serves static files and provides API endpoints for the frontend.
    """

    def __init__(self, host: str = '0.0.0.0', port: int = None, static_dir: str = None):
        """
        Initialize the server.

        Args:
            host: Host to bind to ('0.0.0.0' for external access)
            port: Port to serve on (defaults to PORT env var or 8080)
            static_dir: Directory containing static files (index.html, etc.)
        """
        self.host = host
        self.port = port or int(os.environ.get('PORT', 8080))

        # Find static directory
        if static_dir:
            self.static_dir = static_dir
        else:
            possible_dirs = [
                os.path.join(os.path.dirname(__file__), 'static'),
                os.path.join(os.path.dirname(__file__), 'public'),
                os.path.join(os.path.dirname(__file__), 'web'),
                os.path.dirname(__file__),
            ]
            for d in possible_dirs:
                if os.path.exists(os.path.join(d, 'index.html')):
                    self.static_dir = d
                    break
            else:
                self.static_dir = os.path.dirname(__file__)

        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None

        # Link codes: {code: {discord_id, discord_username, created_at, expires_at}}
        self.pending_link_codes: Dict[str, Dict] = {}

        # Sessions: {token: {discord_id, discord_username, created_at, expires_at}}
        self.sessions: Dict[str, Dict] = {}

        # Secret key
        self.secret_key = os.environ.get('WEB_SECRET_KEY') or secrets.token_hex(32)

    # ═══════════════════════════════════════════════════════════════════════════
    # ROUTE SETUP
    # ═══════════════════════════════════════════════════════════════════════════

    def _setup_routes(self):
        """Configure all routes."""
        self.app = web.Application()
        self.app.middlewares.append(self._cors_middleware)

        # Static routes
        self.app.router.add_get('/', self._handle_index)
        self.app.router.add_get('/index.html', self._handle_index)
        self.app.router.add_get('/portal', self._handle_index)
        self.app.router.add_get('/portal.html', self._handle_index)
        self.app.router.add_get('/health', self._handle_health)

        # Authentication API
        self.app.router.add_post('/api/auth/link', self._api_verify_link_code)
        self.app.router.add_post('/api/auth/logout', self._api_logout)
        self.app.router.add_get('/api/auth/check', self._api_check_session)

        # Player API
        self.app.router.add_get('/api/player/profile', self._api_get_profile)
        self.app.router.add_get('/api/player/inventory', self._api_get_inventory)
        self.app.router.add_get('/api/player/transactions', self._api_get_transactions)

        # Mail API
        self.app.router.add_get('/api/mail', self._api_get_mail)
        self.app.router.add_post('/api/mail/read/{mail_id}', self._api_mark_mail_read)
        self.app.router.add_delete('/api/mail/{mail_id}', self._api_delete_mail)

        # Public API (no auth)
        self.app.router.add_get('/api/public/gods', self._api_get_gods)
        self.app.router.add_get('/api/public/leaderboard', self._api_get_public_leaderboard)
        self.app.router.add_get('/api/public/timeline', self._api_get_public_timeline)

        # Status
        self.app.router.add_get('/api/status', self._handle_status)

        # Static files
        if os.path.exists(os.path.join(self.static_dir, 'static')):
            self.app.router.add_static('/static', os.path.join(self.static_dir, 'static'))

        # Catch-all for other HTML
        self.app.router.add_get('/{filename}.html', self._handle_html)

    @web.middleware
    async def _cors_middleware(self, request: web.Request, handler):
        """Add CORS headers."""
        if request.method == 'OPTIONS':
            response = web.Response()
        else:
            try:
                response = await handler(request)
            except web.HTTPException as e:
                response = e

        # Allow requests from any origin (adjust for production)
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Session-Token'
        return response

    # ═══════════════════════════════════════════════════════════════════════════
    # LINK CODE SYSTEM (Called from Discord bot)
    # ═══════════════════════════════════════════════════════════════════════════

    def generate_link_code(self, discord_id: int, discord_username: str) -> str:
        """
        Generate a link code for a Discord user.
        Call this from your Discord bot's !weblink command.

        Returns: 6-character alphanumeric code
        """
        self._cleanup_expired_codes()

        # Remove existing code for this user
        for code, data in list(self.pending_link_codes.items()):
            if data['discord_id'] == discord_id:
                del self.pending_link_codes[code]

        # Generate new code
        code = secrets.token_hex(3).upper()

        self.pending_link_codes[code] = {
            'discord_id': discord_id,
            'discord_username': discord_username,
            'created_at': datetime.now(),
            'expires_at': datetime.now() + timedelta(minutes=10)
        }

        logger.info(f"Generated link code {code} for {discord_username}")
        return code

    def _cleanup_expired_codes(self):
        """Remove expired link codes."""
        now = datetime.now()
        expired = [code for code, data in self.pending_link_codes.items()
                   if data['expires_at'] < now]
        for code in expired:
            del self.pending_link_codes[code]

    def _cleanup_expired_sessions(self):
        """Remove expired sessions."""
        now = datetime.now()
        expired = [token for token, data in self.sessions.items()
                   if data['expires_at'] < now]
        for token in expired:
            del self.sessions[token]

    def _create_session(self, discord_id: int, discord_username: str) -> str:
        """Create a new session for authenticated user."""
        self._cleanup_expired_sessions()
        token = secrets.token_hex(32)
        self.sessions[token] = {
            'discord_id': discord_id,
            'discord_username': discord_username,
            'created_at': datetime.now(),
            'expires_at': datetime.now() + timedelta(days=7)
        }
        return token

    def _get_session(self, request: web.Request) -> Optional[Dict]:
        """Get session from request."""
        token = request.headers.get('X-Session-Token')
        if not token:
            return None

        session = self.sessions.get(token)
        if not session:
            return None

        if session['expires_at'] < datetime.now():
            del self.sessions[token]
            return None

        return session

    def _require_auth(self, request: web.Request) -> Dict:
        """Require authentication."""
        session = self._get_session(request)
        if not session:
            raise web.HTTPUnauthorized(
                text=json.dumps({'error': 'Not authenticated'}),
                content_type='application/json'
            )
        return session

    # ═══════════════════════════════════════════════════════════════════════════
    # STATIC FILE HANDLERS
    # ═══════════════════════════════════════════════════════════════════════════

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Serve main page."""
        index_path = os.path.join(self.static_dir, 'index.html')
        if os.path.exists(index_path):
            with open(index_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return web.Response(text=content, content_type='text/html')
        return web.Response(text=self._fallback_page(), content_type='text/html')

    async def _handle_html(self, request: web.Request) -> web.Response:
        """Serve other HTML files."""
        filename = request.match_info['filename'] + '.html'
        file_path = os.path.join(self.static_dir, filename)
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return web.Response(text=content, content_type='text/html')
        raise web.HTTPNotFound()

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            'status': 'healthy',
            'service': 'Camp Half-Blood Portal',
            'database': 'connected' if db else 'disconnected'
        })

    async def _handle_status(self, request: web.Request) -> web.Response:
        """API status."""
        return web.json_response({
            'bot': 'Camp Half-Blood',
            'portal': 'online',
            'version': '2.0.0',
            'database': 'connected' if db else 'disconnected',
            'active_sessions': len(self.sessions)
        })

    # ═══════════════════════════════════════════════════════════════════════════
    # AUTHENTICATION API
    # ═══════════════════════════════════════════════════════════════════════════

    async def _api_verify_link_code(self, request: web.Request) -> web.Response:
        """Verify link code and create session."""
        try:
            data = await request.json()
            code = data.get('code', '').upper().strip()

            if not code:
                return web.json_response({'error': 'No code provided'}, status=400)

            self._cleanup_expired_codes()

            if code not in self.pending_link_codes:
                return web.json_response({'error': 'Invalid or expired code'}, status=401)

            link_data = self.pending_link_codes.pop(code)
            token = self._create_session(link_data['discord_id'], link_data['discord_username'])

            logger.info(f"User {link_data['discord_username']} authenticated via link code")

            return web.json_response({
                'success': True,
                'session_token': token,
                'discord_id': link_data['discord_id'],
                'discord_username': link_data['discord_username'],
                'expires_in': 7 * 24 * 60 * 60
            })

        except json.JSONDecodeError:
            return web.json_response({'error': 'Invalid JSON'}, status=400)

    async def _api_logout(self, request: web.Request) -> web.Response:
        """Logout and invalidate session."""
        token = request.headers.get('X-Session-Token')
        if token and token in self.sessions:
            del self.sessions[token]
        return web.json_response({'success': True})

    async def _api_check_session(self, request: web.Request) -> web.Response:
        """Check if session is valid."""
        session = self._get_session(request)
        if session:
            return web.json_response({
                'authenticated': True,
                'discord_id': session['discord_id'],
                'discord_username': session['discord_username']
            })
        return web.json_response({'authenticated': False})

    # ═══════════════════════════════════════════════════════════════════════════
    # PLAYER DATA API
    # ═══════════════════════════════════════════════════════════════════════════

    async def _api_get_profile(self, request: web.Request) -> web.Response:
        """Get player profile."""
        session = self._require_auth(request)

        if not db:
            return web.json_response({'error': 'Database not available'}, status=503)

        try:
            player = db.get_player(session['discord_id'])
            if not player:
                return web.json_response({'error': 'Player not found'}, status=404)

            # Get related data
            cabin = None
            if player.get('cabin_id'):
                cabin = db.get_cabin(player['cabin_id'])

            inventory = db.get_inventory(session['discord_id']) or []
            unread_mail = len(db.get_unread_mail(session['discord_id']) or [])

            mc_link = None
            if hasattr(db, 'get_minecraft_link'):
                mc_link = db.get_minecraft_link(discord_id=session['discord_id'])

            shop = None
            if hasattr(db, 'get_player_shop'):
                shop = db.get_player_shop(session['discord_id'])

            profile_data = {
                'user_id': player.get('user_id'),
                'username': player.get('username'),
                'drachma': player.get('drachma', 0),
                'god_parent': player.get('god_parent'),
                'cabin': {
                    'cabin_id': cabin.get('cabin_id'),
                    'cabin_name': cabin.get('cabin_name'),
                    'divine_favor': cabin.get('divine_favor', 0)
                } if cabin else None,
                'inventory_count': len(inventory),
                'unread_mail': unread_mail,
                'minecraft_link': {
                    'username': mc_link.get('minecraft_username'),
                    'uuid': mc_link.get('minecraft_uuid')
                } if mc_link else None,
                'shop': {
                    'shop_id': shop.get('shop_id'),
                    'shop_name': shop.get('shop_name'),
                    'shop_type': shop.get('shop_type')
                } if shop else None,
                'created_at': str(player.get('created_at')) if player.get('created_at') else None
            }

            return web.json_response(profile_data)

        except Exception as e:
            logger.error(f"Error getting profile: {e}")
            return web.json_response({'error': str(e)}, status=500)

    async def _api_get_inventory(self, request: web.Request) -> web.Response:
        """Get player inventory."""
        session = self._require_auth(request)

        if not db:
            return web.json_response({'error': 'Database not available'}, status=503)

        try:
            inventory = db.get_inventory(session['discord_id']) or []

            enriched = []
            for item in inventory:
                item_data = {
                    'inventory_id': item.get('inventory_id'),
                    'item_id': item.get('item_id'),
                    'quantity': item.get('quantity', 1),
                    'acquired_at': str(item.get('acquired_at')) if item.get('acquired_at') else None,
                    'name': item.get('item_id', '').replace('_', ' ').title(),
                    'emoji': '📦'
                }

                # Try to get item info from config
                if config and hasattr(config, 'SHOP_ITEMS'):
                    for cat_id, cat_data in config.SHOP_ITEMS.items():
                        if item['item_id'] in cat_data.get('items', {}):
                            item_info = cat_data['items'][item['item_id']]
                            item_data['name'] = item_info.get('name', item_data['name'])
                            item_data['emoji'] = item_info.get('emoji', '📦')
                            item_data['description'] = item_info.get('description', '')
                            break

                enriched.append(item_data)

            return web.json_response({'inventory': enriched})

        except Exception as e:
            logger.error(f"Error getting inventory: {e}")
            return web.json_response({'error': str(e)}, status=500)

    async def _api_get_transactions(self, request: web.Request) -> web.Response:
        """Get transaction history."""
        session = self._require_auth(request)

        if not db:
            return web.json_response({'error': 'Database not available'}, status=503)

        try:
            limit = min(int(request.query.get('limit', 20)), 100)
            transactions = db.get_transaction_history(session['discord_id'], limit=limit) or []

            for tx in transactions:
                if tx.get('created_at'):
                    tx['created_at'] = str(tx['created_at'])

            return web.json_response({'transactions': transactions})

        except Exception as e:
            logger.error(f"Error getting transactions: {e}")
            return web.json_response({'error': str(e)}, status=500)

    # ═══════════════════════════════════════════════════════════════════════════
    # MAIL API
    # ═══════════════════════════════════════════════════════════════════════════

    async def _api_get_mail(self, request: web.Request) -> web.Response:
        """Get player mail."""
        session = self._require_auth(request)

        if not db:
            return web.json_response({'error': 'Database not available'}, status=503)

        try:
            if hasattr(db, 'get_all_mail'):
                mail = db.get_all_mail(session['discord_id']) or []
            else:
                mail = db.get_unread_mail(session['discord_id']) or []

            for m in mail:
                if m.get('created_at'):
                    m['created_at'] = str(m['created_at'])

            return web.json_response({'mail': mail})

        except Exception as e:
            logger.error(f"Error getting mail: {e}")
            return web.json_response({'error': str(e)}, status=500)

    async def _api_mark_mail_read(self, request: web.Request) -> web.Response:
        """Mark mail as read."""
        session = self._require_auth(request)
        mail_id = int(request.match_info['mail_id'])

        if not db:
            return web.json_response({'error': 'Database not available'}, status=503)

        try:
            db.mark_mail_read(mail_id)
            return web.json_response({'success': True})
        except Exception as e:
            logger.error(f"Error marking mail read: {e}")
            return web.json_response({'error': str(e)}, status=500)

    async def _api_delete_mail(self, request: web.Request) -> web.Response:
        """Delete mail."""
        session = self._require_auth(request)
        mail_id = int(request.match_info['mail_id'])

        if not db:
            return web.json_response({'error': 'Database not available'}, status=503)

        try:
            success = db.delete_mail(mail_id, session['discord_id'])
            if success:
                return web.json_response({'success': True})
            return web.json_response({'error': 'Could not delete mail'}, status=400)
        except Exception as e:
            logger.error(f"Error deleting mail: {e}")
            return web.json_response({'error': str(e)}, status=500)

    # ═══════════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════════════════

    async def _api_get_gods(self, request: web.Request) -> web.Response:
        """Get gods list (public)."""
        # Default gods if config not available
        gods = {
            'Zeus': {'emoji': '⚡', 'domain': 'Sky, Thunder'},
            'Poseidon': {'emoji': '🔱', 'domain': 'Sea, Earthquakes'},
            'Hades': {'emoji': '💀', 'domain': 'Underworld'},
            'Athena': {'emoji': '🦉', 'domain': 'Wisdom, Warfare'},
            'Apollo': {'emoji': '☀️', 'domain': 'Sun, Music'},
            'Artemis': {'emoji': '🏹', 'domain': 'Hunt, Moon'},
            'Ares': {'emoji': '⚔️', 'domain': 'War'},
            'Aphrodite': {'emoji': '💕', 'domain': 'Love, Beauty'},
            'Hephaestus': {'emoji': '🔨', 'domain': 'Fire, Forge'},
            'Hermes': {'emoji': '👟', 'domain': 'Travel, Thieves'},
            'Demeter': {'emoji': '🌾', 'domain': 'Agriculture'},
            'Dionysus': {'emoji': '🍇', 'domain': 'Wine, Festivity'},
        }

        if config and hasattr(config, 'OLYMPIAN_GODS'):
            gods = config.OLYMPIAN_GODS

        return web.json_response({'gods': gods})

    async def _api_get_public_leaderboard(self, request: web.Request) -> web.Response:
        """Get public leaderboard."""
        if not db:
            return web.json_response({'error': 'Database not available'}, status=503)

        try:
            limit = min(int(request.query.get('limit', 10)), 20)
            players = db.get_all_players(limit=100) or []
            leaderboard = sorted(players, key=lambda p: p.get('drachma', 0), reverse=True)[:limit]

            public_lb = [{
                'username': p.get('username'),
                'drachma': p.get('drachma', 0),
                'god_parent': p.get('god_parent')
            } for p in leaderboard]

            return web.json_response({'leaderboard': public_lb})

        except Exception as e:
            logger.error(f"Error getting leaderboard: {e}")
            return web.json_response({'error': str(e)}, status=500)

    async def _api_get_public_timeline(self, request: web.Request) -> web.Response:
        """Get public timeline."""
        if not db:
            return web.json_response({'error': 'Database not available'}, status=503)

        try:
            limit = min(int(request.query.get('limit', 20)), 50)
            entries = db.get_timeline_entries(limit=limit) or []

            for entry in entries:
                if entry.get('event_date'):
                    entry['event_date'] = str(entry['event_date'])
                if entry.get('created_at'):
                    entry['created_at'] = str(entry['created_at'])

            return web.json_response({'entries': entries})

        except Exception as e:
            logger.error(f"Error getting timeline: {e}")
            return web.json_response({'error': str(e)}, status=500)

    # ═══════════════════════════════════════════════════════════════════════════
    # FALLBACK PAGE
    # ═══════════════════════════════════════════════════════════════════════════

    def _fallback_page(self) -> str:
        """Generate fallback page."""
        return """<!DOCTYPE html>
<html><head><title>Camp Half-Blood Portal</title>
<style>
body { font-family: Georgia, serif; background: #0a0a12; color: #f5f5f0; 
       display: flex; align-items: center; justify-content: center; 
       min-height: 100vh; margin: 0; }
.container { text-align: center; max-width: 600px; padding: 2rem; }
h1 { color: #D4AF37; font-size: 2.5rem; }
p { font-size: 1.2rem; line-height: 1.6; }
.status { background: rgba(74,222,128,0.1); border-left: 4px solid #4ade80; 
          padding: 1rem; margin: 2rem 0; text-align: left; }
</style></head><body>
<div class="container">
<h1>⚡ Camp Half-Blood Portal ⚡</h1>
<p>The server is running, but the frontend files were not found.</p>
<div class="status">
<strong>✅ Server Status: Online</strong><br>
Place your <code>index.html</code> in the same directory as this server, 
or in a <code>static/</code> subdirectory.
</div>
<p>API Endpoints are available at <code>/api/*</code></p>
</div></body></html>"""

    # ═══════════════════════════════════════════════════════════════════════════
    # SERVER LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════════════

    async def start(self):
        """Start the server."""
        self._setup_routes()
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        logger.info(f"⚡ Camp Half-Blood Portal started at http://{self.host}:{self.port}")

    async def stop(self):
        """Stop the server."""
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        logger.info("⚡ Camp Half-Blood Portal stopped")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

# Global server instance for bot integration
server = None

def get_server() -> CampHalfBloodServer:
    """Get or create server instance."""
    global server
    if server is None:
        server = CampHalfBloodServer()
    return server


if __name__ == '__main__':
    """Run standalone server."""
    import argparse

    parser = argparse.ArgumentParser(description='Camp Half-Blood Web Portal')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=None, help='Port (default: PORT env or 8080)')
    parser.add_argument('--static', default=None, help='Static files directory')
    args = parser.parse_args()

    async def main():
        srv = CampHalfBloodServer(
            host=args.host,
            port=args.port,
            static_dir=args.static
        )
        await srv.start()

        try:
            while True:
                await asyncio.sleep(3600)
        except KeyboardInterrupt:
            await srv.stop()

    print("⚡ Camp Half-Blood Web Portal ⚡")
    print(f"Starting server...")
    print("Press Ctrl+C to stop\n")

    asyncio.run(main())
