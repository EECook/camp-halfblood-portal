"""
Web Portal Integration Cog
==========================
Connects the Discord bot to the web portal by storing link codes in MySQL.
The Railway-hosted webserver reads codes from the same database.

Commands:
- !weblink - Generate a code to link Discord account to web portal
- !portalsync - Force sync data
- !portalstatus - Check portal status
"""

import discord
from discord.ext import commands
import secrets
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION - YOUR RAILWAY URL
# ═══════════════════════════════════════════════════════════════════════════════

PORTAL_URL = "https://web-production-2dc4.up.railway.app"

# ═══════════════════════════════════════════════════════════════════════════════


class WebPortal(commands.Cog):
    """Web portal integration for Camp Half-Blood."""

    def __init__(self, bot):
        self.bot = bot

    def _get_god_emoji(self, god_name: str) -> str:
        """Get emoji for a god."""
        god_emojis = {
            'Zeus': '⚡', 'Poseidon': '🔱', 'Hades': '💀', 'Athena': '🦉',
            'Apollo': '☀️', 'Artemis': '🏹', 'Ares': '⚔️', 'Aphrodite': '💕',
            'Hephaestus': '🔨', 'Hermes': '👟', 'Demeter': '🌾', 'Dionysus': '🍇',
            'Hera': '👑', 'Hecate': '🌙', 'Hypnos': '😴', 'Nike': '🏆',
            'Nemesis': '⚖️', 'Iris': '🌈', 'Tyche': '🎲', 'Hestia': '🔥'
        }
        return god_emojis.get(god_name, '❓')

    def _store_link_code(self, code: str, discord_id: int, discord_username: str) -> bool:
        """Store link code in the database using the bot's DatabaseManager."""
        db = getattr(self.bot, 'db', None)
        if not db:
            logger.error("Database not available")
            return False
        
        try:
            expires_at = datetime.now() + timedelta(minutes=10)
            
            # Use the exact same pattern as your DatabaseManager
            with db.get_connection() as conn:
                cursor = conn.cursor()
                
                # Delete any existing codes for this user
                cursor.execute(
                    "DELETE FROM web_link_codes WHERE discord_id = %s",
                    (discord_id,)
                )
                
                # Insert new code
                cursor.execute(
                    """INSERT INTO web_link_codes 
                       (code, discord_id, discord_username, expires_at, used)
                       VALUES (%s, %s, %s, %s, 0)""",
                    (code, discord_id, discord_username, expires_at)
                )
                
                # Connection commits automatically via context manager
                
            logger.info(f"[WebPortal] Stored link code {code} for {discord_username} (ID: {discord_id})")
            print(f"[WebPortal] Stored link code {code} for {discord_username} (ID: {discord_id})")
            return True
                
        except Exception as e:
            logger.error(f"[WebPortal] Failed to store link code: {e}")
            print(f"[WebPortal] Failed to store link code: {e}")
            import traceback
            traceback.print_exc()
            return False

    @commands.command(name='weblink', aliases=['portallink', 'linkweb', 'webportal'])
    @commands.cooldown(1, 60, commands.BucketType.user)
    async def weblink(self, ctx):
        """
        Generate a code to link your Discord account to the Camp Half-Blood web portal.
        
        The code expires in 10 minutes and can only be used once.
        """
        db = getattr(self.bot, 'db', None)
        
        # Check if player has a profile
        if db:
            player = db.get_player(ctx.author.id)
            if not player:
                embed = discord.Embed(
                    title="⚠️ No Profile Found",
                    description=(
                        "You need to create a demigod profile before linking to the web portal!\n\n"
                        "Use `!profile` to create your profile first."
                    ),
                    color=discord.Color.orange()
                )
                await ctx.send(embed=embed)
                return

        # Generate 6-character code
        code = secrets.token_hex(3).upper()
        
        # Store code in database
        if not self._store_link_code(code, ctx.author.id, str(ctx.author)):
            embed = discord.Embed(
                title="❌ Error",
                description="Could not generate link code. Please try again later.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        # Create the DM embed
        dm_embed = discord.Embed(
            title="🏛️ Camp Half-Blood Portal Access",
            description=(
                "Your mystical link code has been generated!\n"
                "Use this code to connect your Discord account to the web portal."
            ),
            color=discord.Color.gold()
        )
        
        dm_embed.add_field(
            name="🔑 Your Link Code",
            value=f"```\n{code}\n```",
            inline=False
        )
        
        dm_embed.add_field(
            name="📍 Portal URL",
            value=f"**[Click here to access the portal]({PORTAL_URL})**",
            inline=False
        )
        
        dm_embed.add_field(
            name="⏰ Expiration",
            value="This code expires in **10 minutes**",
            inline=True
        )
        
        dm_embed.add_field(
            name="🔒 Security",
            value="One-time use only",
            inline=True
        )
        
        dm_embed.add_field(
            name="📋 Instructions",
            value=(
                f"1. Go to {PORTAL_URL}\n"
                "2. Click **My Portal** in the navigation\n"
                "3. Enter your 6-character code\n"
                "4. Click **Enter Camp**\n\n"
                "You'll have access to your profile, mail, quizzes, and more!"
            ),
            inline=False
        )
        
        dm_embed.set_footer(text="⚡ Do not share this code with anyone! ⚡")
        
        # Try to DM the user
        try:
            await ctx.author.send(embed=dm_embed)
            
            # Send confirmation in channel
            confirm_embed = discord.Embed(
                title="📬 Link Code Sent!",
                description=(
                    f"{ctx.author.mention}, I've sent your portal link code via DM!\n\n"
                    "Check your Direct Messages for the code and instructions."
                ),
                color=discord.Color.green()
            )
            confirm_embed.add_field(
                name="🌐 Portal",
                value=f"[{PORTAL_URL}]({PORTAL_URL})",
                inline=False
            )
            confirm_embed.set_footer(text="Code expires in 10 minutes")
            await ctx.send(embed=confirm_embed)
            
        except discord.Forbidden:
            # Can't DM user
            error_embed = discord.Embed(
                title="❌ Cannot Send DM",
                description=(
                    f"{ctx.author.mention}, I couldn't send you a DM!\n\n"
                    "Please enable DMs from server members:\n"
                    "**Server Settings → Privacy Settings → Allow DMs**\n\n"
                    "Then try `!weblink` again."
                ),
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)

    @weblink.error
    async def weblink_error(self, ctx, error):
        """Handle weblink command errors."""
        if isinstance(error, commands.CommandOnCooldown):
            embed = discord.Embed(
                title="⏳ Cooldown Active",
                description=(
                    f"You can request a new link code in **{error.retry_after:.0f} seconds**.\n\n"
                    "If you lost your previous code, wait for the cooldown to expire."
                ),
                color=discord.Color.orange()
            )
            await ctx.send(embed=embed)

    @commands.command(name='portalsync', aliases=['websync', 'syncportal'])
    @commands.cooldown(1, 30, commands.BucketType.user)
    async def portal_sync(self, ctx):
        """Force sync your Discord data with the web portal."""
        db = getattr(self.bot, 'db', None)
        if not db:
            await ctx.send("❌ Database not available.")
            return

        player = db.get_player(ctx.author.id)
        if not player:
            await ctx.send("❌ You don't have a profile yet! Use `!profile` to create one.")
            return

        god_emoji = self._get_god_emoji(player.get('god_parent', ''))
        
        embed = discord.Embed(
            title="🔄 Portal Sync Complete",
            description="Your data has been synced with the web portal!",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="📊 Synced Data",
            value=(
                f"**Username:** {player.get('username', ctx.author.name)}\n"
                f"**Drachma:** {player.get('drachma', 0):,} 💰\n"
                f"**God Parent:** {god_emoji} {player.get('god_parent', 'Unclaimed')}\n"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🌐 Portal",
            value=f"[Visit the portal]({PORTAL_URL})",
            inline=False
        )
        
        embed.set_footer(text="Visit the portal to see your updated profile!")
        await ctx.send(embed=embed)

    @commands.command(name='portalstatus', aliases=['webstatus'])
    @commands.cooldown(1, 10, commands.BucketType.user)  
    async def portal_status(self, ctx):
        """Check the status of the web portal."""
        embed = discord.Embed(
            title="🏛️ Web Portal Status",
            color=discord.Color.gold()
        )

        embed.add_field(
            name="🌐 Portal URL",
            value=f"[{PORTAL_URL}]({PORTAL_URL})",
            inline=False
        )
        
        embed.add_field(
            name="🔗 Link Your Account",
            value="Use `!weblink` to get your access code",
            inline=False
        )
        
        embed.add_field(
            name="✨ Portal Features",
            value=(
                "• View your profile & Drachma balance\n"
                "• Read immersive mail from the gods\n"
                "• Take personality quizzes\n"
                "• View camp timeline\n"
                "• And more!"
            ),
            inline=False
        )

        await ctx.send(embed=embed)


async def setup(bot):
    """Load the WebPortal cog."""
    await bot.add_cog(WebPortal(bot))
    logger.info("WebPortal cog loaded")
    print("[WebPortal] Cog loaded successfully")
