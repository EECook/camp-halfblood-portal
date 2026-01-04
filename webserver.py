"""
Camp Half-Blood Web Portal Server
=================================
For Railway deployment. Reads link codes from MySQL database.
Does NOT require discord.py - that's only for the bot.
"""

import asyncio
import os
import json
import secrets
from aiohttp import web
from typing import Optional, Dict
from datetime import datetime, timedelta
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════════════

db = None

try:
    import mysql.connector
    
    class DatabaseManager:
        def __init__(self):
            self.db_config = {
                'host': os.environ.get('MYSQL_HOST', 'localhost'),
                'port': int(os.environ.get('MYSQL_PORT', 3306)),
                'user': os.environ.get('MYSQL_USER', 'root'),
                'password': os.environ.get('MYSQL_PASSWORD', ''),
                'database': os.environ.get('MYSQL_DATABASE', 'camphalfblood'),
            }
            logger.info(f"Database: {self.db_config['host']}:{self.db_config['port']}")
        
        def _get_connection(self):
            try:
                return mysql.connector.connect(**self.db_config, connection_timeout=10)
            except Exception as e:
                logger.error(f"MySQL connection failed: {e}")
                return None
        
        def _execute(self, query, params=None, fetch_one=False, fetch_all=False):
            conn = None
            cursor = None
            try:
                conn = self._get_connection()
                if not conn:
                    return None
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
        
        # Player methods
        def get_player(self, user_id):
            return self._execute("SELECT * FROM players WHERE user_id = %s", (user_id,), fetch_one=True)
        
        def get_inventory(self, user_id):
            return self._execute("SELECT * FROM inventory WHERE user_id = %s", (user_id,), fetch_all=True)
        
        def get_unread_mail(self, user_id):
            return self._execute("SELECT * FROM mail WHERE recipient_id = %s AND is_read = 0", (user_id,), fetch_all=True)
        
        def get_all_mail(self, user_id):
            return self._execute("SELECT * FROM mail WHERE recipient_id = %s ORDER BY created_at DESC LIMIT 50", (user_id,), fetch_all=True)
        
        def mark_mail_read(self, mail_id):
            return self._execute("UPDATE mail SET is_read = 1 WHERE mail_id = %s", (mail_id,))
        
        def delete_mail(self, mail_id, user_id):
            return self._execute("DELETE FROM mail WHERE mail_id = %s AND recipient_id = %s", (mail_id, user_id))
        
        def get_cabin(self, cabin_id):
            return self._execute("SELECT * FROM cabins WHERE cabin_id = %s", (cabin_id,), fetch_one=True)
        
        def get_minecraft_link(self, discord_id):
            return self._execute("SELECT * FROM minecraft_links WHERE discord_id = %s", (discord_id,), fetch_one=True)
        
        def get_player_shop(self, user_id):
            return self._execute("SELECT * FROM player_shops WHERE owner_id = %s", (user_id,), fetch_one=True)
        
        def get_timeline_entries(self, limit=20):
            return self._execute("SELECT * FROM timeline_entries ORDER BY event_date DESC LIMIT %s", (limit,), fetch_all=True)
        
        # Link code methods (reads codes stored by Discord bot)
        def get_link_code(self, code):
            return self._execute(
                "SELECT * FROM web_link_codes WHERE code = %s AND used = 0 AND expires_at > NOW()",
                (code,), fetch_one=True
            )
        
        def mark_code_used(self, code):
            return self._execute("UPDATE web_link_codes SET used = 1, used_at = NOW() WHERE code = %s", (code,))
        
        def cleanup_expired_codes(self):
            return self._execute("DELETE FROM web_link_codes WHERE expires_at < NOW() OR used = 1")
        
        # Session methods
        def create_session(self, token, discord_id, discord_username, expires_at):
            return self._execute(
                "INSERT INTO web_sessions (session_token, discord_id, discord_username, expires_at) VALUES (%s, %s, %s, %s)",
                (token, discord_id, discord_username, expires_at)
            )
        
        def get_session(self, token):
            return self._execute(
                "SELECT * FROM web_sessions WHERE session_token = %s AND expires_at > NOW()",
                (token,), fetch_one=True
            )
        
        def delete_session(self, token):
            return self._execute("DELETE FROM web_sessions WHERE session_token = %s", (token,))
    
    db = DatabaseManager()
    logger.info("Database initialized")
    
except ImportError as e:
    logger.warning(f"MySQL not available: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# WEB SERVER
# ═══════════════════════════════════════════════════════════════════════════════

class CampHalfBloodServer:
    def __init__(self, port=None):
        self.port = port or int(os.environ.get('PORT', 8080))
        self.static_dir = os.path.dirname(__file__)
        self.app = None
        self.runner = None
        self.site = None

    def _setup_routes(self):
        self.app = web.Application()
        self.app.middlewares.append(self._cors_middleware)

        self.app.router.add_get('/', self._handle_index)
        self.app.router.add_get('/health', self._handle_health)

        # Auth
        self.app.router.add_post('/api/auth/link', self._api_verify_link_code)
        self.app.router.add_post('/api/auth/logout', self._api_logout)
        self.app.router.add_get('/api/auth/check', self._api_check_session)

        # Player
        self.app.router.add_get('/api/player/profile', self._api_get_profile)
        self.app.router.add_get('/api/player/inventory', self._api_get_inventory)

        # Mail
        self.app.router.add_get('/api/mail', self._api_get_mail)
        self.app.router.add_post('/api/mail/read/{mail_id}', self._api_mark_mail_read)
        self.app.router.add_delete('/api/mail/{mail_id}', self._api_delete_mail)

        # Public
        self.app.router.add_get('/api/public/timeline', self._api_get_timeline)
        self.app.router.add_get('/api/status', self._handle_status)

    @web.middleware
    async def _cors_middleware(self, request, handler):
        if request.method == 'OPTIONS':
            response = web.Response()
        else:
            try:
                response = await handler(request)
            except web.HTTPException as e:
                response = e
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization, X-Session-Token'
        return response

    def _get_session(self, request):
        token = request.headers.get('X-Session-Token')
        if not token or not db:
            return None
        return db.get_session(token)

    def _require_auth(self, request):
        session = self._get_session(request)
        if not session:
            raise web.HTTPUnauthorized(text=json.dumps({'error': 'Not authenticated'}), content_type='application/json')
        return session

    # ─────────────────────────────────────────────────────────────────────────
    # Static & Health
    # ─────────────────────────────────────────────────────────────────────────

    async def _handle_index(self, request):
        index_path = os.path.join(self.static_dir, 'index.html')
        if os.path.exists(index_path):
            with open(index_path, 'r', encoding='utf-8') as f:
                return web.Response(text=f.read(), content_type='text/html')
        return web.Response(text="<h1>Camp Half-Blood Portal</h1><p>Server running. index.html not found.</p>", content_type='text/html')

    async def _handle_health(self, request):
        db_status = 'unknown'
        if db:
            try:
                result = db._execute("SELECT 1", fetch_one=True)
                db_status = 'connected' if result else 'error'
            except:
                db_status = 'error'
        return web.json_response({'status': 'healthy', 'database': db_status})

    async def _handle_status(self, request):
        return web.json_response({'portal': 'online', 'version': '2.0.0'})

    # ─────────────────────────────────────────────────────────────────────────
    # Auth API
    # ─────────────────────────────────────────────────────────────────────────

    async def _api_verify_link_code(self, request):
        try:
            data = await request.json()
            code = data.get('code', '').upper().strip()

            if not code:
                return web.json_response({'error': 'No code provided'}, status=400)
            if not db:
                return web.json_response({'error': 'Database not available'}, status=503)

            db.cleanup_expired_codes()
            link_data = db.get_link_code(code)
            
            if not link_data:
                logger.warning(f"Invalid code: {code}")
                return web.json_response({'error': 'Invalid or expired code'}, status=401)

            db.mark_code_used(code)

            token = secrets.token_hex(32)
            expires_at = datetime.now() + timedelta(days=7)
            db.create_session(token, link_data['discord_id'], link_data['discord_username'], expires_at)

            logger.info(f"User {link_data['discord_username']} authenticated")

            return web.json_response({
                'success': True,
                'session_token': token,
                'discord_id': link_data['discord_id'],
                'discord_username': link_data['discord_username']
            })
        except json.JSONDecodeError:
            return web.json_response({'error': 'Invalid JSON'}, status=400)

    async def _api_logout(self, request):
        token = request.headers.get('X-Session-Token')
        if token and db:
            db.delete_session(token)
        return web.json_response({'success': True})

    async def _api_check_session(self, request):
        session = self._get_session(request)
        if session:
            return web.json_response({
                'authenticated': True,
                'discord_id': session['discord_id'],
                'discord_username': session['discord_username']
            })
        return web.json_response({'authenticated': False})

    # ─────────────────────────────────────────────────────────────────────────
    # Player API
    # ─────────────────────────────────────────────────────────────────────────

    async def _api_get_profile(self, request):
        session = self._require_auth(request)
        if not db:
            return web.json_response({'error': 'Database not available'}, status=503)

        player = db.get_player(session['discord_id'])
        if not player:
            return web.json_response({'error': 'Player not found'}, status=404)

        cabin = db.get_cabin(player['cabin_id']) if player.get('cabin_id') else None
        inventory = db.get_inventory(session['discord_id']) or []
        unread_mail = len(db.get_unread_mail(session['discord_id']) or [])
        mc_link = db.get_minecraft_link(session['discord_id'])
        shop = db.get_player_shop(session['discord_id'])

        return web.json_response({
            'user_id': player.get('user_id'),
            'username': player.get('username'),
            'drachma': player.get('drachma', 0),
            'god_parent': player.get('god_parent'),
            'cabin': {'cabin_id': cabin['cabin_id'], 'cabin_name': cabin['cabin_name']} if cabin else None,
            'inventory_count': len(inventory),
            'unread_mail': unread_mail,
            'minecraft_link': {'username': mc_link.get('minecraft_username')} if mc_link else None,
            'shop': {'shop_name': shop.get('shop_name')} if shop else None,
            'created_at': str(player.get('created_at')) if player.get('created_at') else None
        })

    async def _api_get_inventory(self, request):
        session = self._require_auth(request)
        if not db:
            return web.json_response({'error': 'Database not available'}, status=503)
        inventory = db.get_inventory(session['discord_id']) or []
        return web.json_response({'inventory': inventory})

    # ─────────────────────────────────────────────────────────────────────────
    # Mail API
    # ─────────────────────────────────────────────────────────────────────────

    async def _api_get_mail(self, request):
        session = self._require_auth(request)
        if not db:
            return web.json_response({'error': 'Database not available'}, status=503)
        mail = db.get_all_mail(session['discord_id']) or []
        for m in mail:
            if m.get('created_at'):
                m['created_at'] = str(m['created_at'])
        return web.json_response({'mail': mail})

    async def _api_mark_mail_read(self, request):
        session = self._require_auth(request)
        mail_id = int(request.match_info['mail_id'])
        if db:
            db.mark_mail_read(mail_id)
        return web.json_response({'success': True})

    async def _api_delete_mail(self, request):
        session = self._require_auth(request)
        mail_id = int(request.match_info['mail_id'])
        if db:
            db.delete_mail(mail_id, session['discord_id'])
        return web.json_response({'success': True})

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    async def _api_get_timeline(self, request):
        if not db:
            return web.json_response({'entries': []})
        entries = db.get_timeline_entries(limit=20) or []
        for e in entries:
            if e.get('event_date'):
                e['event_date'] = str(e['event_date'])
        return web.json_response({'entries': entries})

    # ─────────────────────────────────────────────────────────────────────────
    # Server Lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    async def start(self):
        self._setup_routes()
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '0.0.0.0', self.port)
        await self.site.start()
        print(f"✅ Server running on port {self.port}")

    async def stop(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    
    print("=" * 50)
    print("⚡ Camp Half-Blood Web Portal ⚡")
    print("=" * 50)
    print(f"Port: {port}")
    print(f"Database: {os.environ.get('MYSQL_HOST', 'not set')}")
    print("=" * 50)

    async def main():
        srv = CampHalfBloodServer(port=port)
        await srv.start()
        while True:
            await asyncio.sleep(3600)

    asyncio.run(main())
