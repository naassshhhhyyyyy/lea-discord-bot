import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import json
from datetime import datetime, timedelta
import asyncio
from typing import Optional, Dict, List
import logging
import random
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Database setup
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('partnerships.db')
        self.cursor = self.conn.cursor()
        self.init_tables()
    
    def init_tables(self):
        """Initialize all database tables"""
        # Server settings
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS server_settings (
                guild_id INTEGER PRIMARY KEY,
                application_channel INTEGER,
                review_channel INTEGER,
                partner_role INTEGER,
                log_channel INTEGER,
                applications_channel_id INTEGER,
                reviews_channel_id INTEGER,
                mod_role_id INTEGER,
                admin_role_id INTEGER,
                owner_role_id INTEGER
            )
        ''')
        
        # Applications
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                applicant_id INTEGER,
                server_name TEXT,
                server_invite TEXT,
                partnership_type TEXT,
                description TEXT,
                status TEXT DEFAULT 'pending',
                submitted_at TIMESTAMP,
                reviewed_by INTEGER,
                reviewed_at TIMESTAMP,
                message_id INTEGER,
                channel_id INTEGER
            )
        ''')
        
        # Partners
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS partners (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER,
                partner_guild_id INTEGER,
                partner_name TEXT,
                partnership_type TEXT,
                invite_link TEXT,
                joined_at TIMESTAMP,
                status TEXT DEFAULT 'active',
                UNIQUE(guild_id, partner_guild_id)
            )
        ''')
        
        # Reviews/votes
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                application_id INTEGER,
                reviewer_id INTEGER,
                vote TEXT,
                timestamp TIMESTAMP
            )
        ''')
        
        # Partnership applications channel mapping
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS partnership_apps (
                guild_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                message_id INTEGER
            )
        ''')
        
        self.conn.commit()
    
    def get_setting(self, guild_id: int, setting: str):
        self.cursor.execute(f'SELECT {setting} FROM server_settings WHERE guild_id = ?', (guild_id,))
        result = self.cursor.fetchone()
        return result[0] if result else None
    
    def set_setting(self, guild_id: int, **kwargs):
        for key, value in kwargs.items():
            self.cursor.execute(f'''
                INSERT INTO server_settings (guild_id, {key}) 
                VALUES (?, ?) 
                ON CONFLICT(guild_id) DO UPDATE SET {key} = ?
            ''', (guild_id, value, value))
        self.conn.commit()
    
    def add_application(self, guild_id, applicant_id, server_name, server_invite, partnership_type, description):
        self.cursor.execute('''
            INSERT INTO applications (guild_id, applicant_id, server_name, server_invite, partnership_type, description, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (guild_id, applicant_id, server_name, server_invite, partnership_type, description, datetime.now()))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_pending_apps(self, guild_id):
        self.cursor.execute('''
            SELECT * FROM applications 
            WHERE guild_id = ? AND status = 'pending' 
            ORDER BY submitted_at ASC
        ''', (guild_id,))
        return self.cursor.fetchall()
    
    def update_application_status(self, app_id, status, reviewer_id=None):
        self.cursor.execute('''
            UPDATE applications 
            SET status = ?, reviewed_by = ?, reviewed_at = ?
            WHERE id = ?
        ''', (status, reviewer_id, datetime.now(), app_id))
        self.conn.commit()
    
    def add_review(self, app_id, reviewer_id, vote):
        self.cursor.execute('''
            INSERT INTO reviews (application_id, reviewer_id, vote, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (app_id, reviewer_id, vote, datetime.now()))
        self.conn.commit()
    
    def get_review_count(self, app_id, vote):
        self.cursor.execute('''
            SELECT COUNT(*) FROM reviews 
            WHERE application_id = ? AND vote = ?
        ''', (app_id, vote))
        return self.cursor.fetchone()[0]
    
    def add_partner(self, guild_id, partner_guild_id, partner_name, partnership_type, invite_link):
        self.cursor.execute('''
            INSERT OR REPLACE INTO partners (guild_id, partner_guild_id, partner_name, partnership_type, invite_link, joined_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (guild_id, partner_guild_id, partner_name, partnership_type, invite_link, datetime.now()))
        self.conn.commit()
    
    def get_partners(self, guild_id):
        self.cursor.execute('''
            SELECT * FROM partners 
            WHERE guild_id = ? AND status = 'active'
            ORDER BY joined_at DESC
        ''', (guild_id,))
        return self.cursor.fetchall()
    
    def remove_partner(self, guild_id, partner_guild_id):
        self.cursor.execute('''
            UPDATE partners 
            SET status = 'inactive' 
            WHERE guild_id = ? AND partner_guild_id = ?
        ''', (guild_id, partner_guild_id))
        self.conn.commit()
    
    def save_partnership_message(self, guild_id, channel_id, message_id):
        self.cursor.execute('''
            INSERT OR REPLACE INTO partnership_apps (guild_id, channel_id, message_id)
            VALUES (?, ?, ?)
        ''', (guild_id, channel_id, message_id))
        self.conn.commit()
    
    def get_partnership_message(self, guild_id):
        self.cursor.execute('SELECT channel_id, message_id FROM partnership_apps WHERE guild_id = ?', (guild_id,))
        return self.cursor.fetchone()

db = Database()

# Permission checker functions
def has_mod_permission(interaction: discord.Interaction) -> bool:
    """Check if user has mod permissions"""
    mod_role_id = db.get_setting(interaction.guild_id, 'mod_role_id')
    admin_role_id = db.get_setting(interaction.guild_id, 'admin_role_id')
    owner_role_id = db.get_setting(interaction.guild_id, 'owner_role_id')
    
    user_roles = [role.id for role in interaction.user.roles]
    
    # Check for owner role
    if owner_role_id and owner_role_id in user_roles:
        return True
    
    # Check for admin role
    if admin_role_id and admin_role_id in user_roles:
        return True
    
    # Check for mod role
    if mod_role_id and mod_role_id in user_roles:
        return True
    
    # Check for administrator permission (fallback)
    if interaction.user.guild_permissions.administrator:
        return True
    
    return False

def has_admin_permission(interaction: discord.Interaction) -> bool:
    """Check if user has admin permissions"""
    admin_role_id = db.get_setting(interaction.guild_id, 'admin_role_id')
    owner_role_id = db.get_setting(interaction.guild_id, 'owner_role_id')
    
    user_roles = [role.id for role in interaction.user.roles]
    
    # Check for owner role
    if owner_role_id and owner_role_id in user_roles:
        return True
    
    # Check for admin role
    if admin_role_id and admin_role_id in user_roles:
        return True
    
    # Check for administrator permission (fallback)
    if interaction.user.guild_permissions.administrator:
        return True
    
    return False

def has_owner_permission(interaction: discord.Interaction) -> bool:
    """Check if user has owner permissions"""
    owner_role_id = db.get_setting(interaction.guild_id, 'owner_role_id')
    
    user_roles = [role.id for role in interaction.user.roles]
    
    # Check for owner role
    if owner_role_id and owner_role_id in user_roles:
        return True
    
    # Check for server owner
    if interaction.user.id == interaction.guild.owner_id:
        return True
    
    # Check for administrator permission (fallback)
    if interaction.user.guild_permissions.administrator:
        return True
    
    return False

# Application Modal
class PartnershipModal(discord.ui.Modal, title="🤝 Partnership Application"):
    server_name = discord.ui.TextInput(
        label="Server Name",
        placeholder="What's the name of your server?",
        required=True,
        max_length=100
    )
    
    invite_link = discord.ui.TextInput(
        label="Invite Link",
        placeholder="https://discord.gg/yourinvite",
        required=True,
        max_length=200
    )
    
    partnership_type = discord.ui.TextInput(
        label="Partnership Type",
        placeholder="e.g., Mutual, Cross-promotion, Event",
        required=True,
        max_length=50
    )
    
    description = discord.ui.TextInput(
        label="Server Description",
        placeholder="Tell us about your server...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        # Validate invite link
        invite_pattern = r'(?:https?:\/\/)?(?:www\.)?(?:discord\.gg\/|discord\.com\/invite\/)([a-zA-Z0-9\-]+)'
        if not re.match(invite_pattern, self.invite_link.value):
            await interaction.response.send_message("❌ Please provide a valid Discord invite link.", ephemeral=True)
            return
        
        # Save application
        app_id = db.add_application(
            interaction.guild_id,
            interaction.user.id,
            self.server_name.value,
            self.invite_link.value,
            self.partnership_type.value,
            self.description.value
        )
        
        # Send confirmation to user
        embed = discord.Embed(
            title="✅ Application Submitted",
            description=f"Your partnership application (#{app_id}) has been submitted successfully!",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Server", value=self.server_name.value, inline=True)
        embed.add_field(name="Type", value=self.partnership_type.value, inline=True)
        embed.add_field(name="Status", value="Pending Review", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        # Notify review channel
        review_channel_id = db.get_setting(interaction.guild_id, 'review_channel')
        if review_channel_id:
            review_channel = interaction.guild.get_channel(review_channel_id)
            if review_channel:
                review_embed = discord.Embed(
                    title="📋 New Partnership Application",
                    description=f"Application #{app_id} from {interaction.user.mention}",
                    color=discord.Color.blue(),
                    timestamp=datetime.now()
                )
                review_embed.add_field(name="Server Name", value=self.server_name.value, inline=True)
                review_embed.add_field(name="Partnership Type", value=self.partnership_type.value, inline=True)
                review_embed.add_field(name="Invite Link", value=f"[Click Here]({self.invite_link.value})", inline=True)
                review_embed.add_field(name="Description", value=self.description.value[:500], inline=False)
                review_embed.set_footer(text=f"Application ID: {app_id}")
                
                view = ReviewView(app_id, interaction.user.id, self.server_name.value)
                await review_channel.send(content=f"<@&{db.get_setting(interaction.guild_id, 'partner_role')}>" if db.get_setting(interaction.guild_id, 'partner_role') else "", embed=review_embed, view=view)
        
        # Log to log channel
        log_channel_id = db.get_setting(interaction.guild_id, 'log_channel')
        if log_channel_id:
            log_channel = interaction.guild.get_channel(log_channel_id)
            if log_channel:
                log_embed = discord.Embed(
                    title="New Application",
                    description=f"Application #{app_id} submitted by {interaction.user.mention}",
                    color=discord.Color.blue(),
                    timestamp=datetime.now()
                )
                await log_channel.send(embed=log_embed)

# Review View
class ReviewView(discord.ui.View):
    def __init__(self, app_id: int, applicant_id: int, server_name: str):
        super().__init__(timeout=604800)  # 7 days
        self.app_id = app_id
        self.applicant_id = applicant_id
        self.server_name = server_name
    
    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, emoji="✅")
    async def approve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check if user has permission (mod, admin, or owner)
        if not has_mod_permission(interaction):
            await interaction.response.send_message("❌ You need Mod, Admin, or Owner role to review applications!", ephemeral=True)
            return
        
        # Add review
        db.add_review(self.app_id, interaction.user.id, 'approve')
        
        # Get review counts
        approve_count = db.get_review_count(self.app_id, 'approve')
        deny_count = db.get_review_count(self.app_id, 'deny')
        
        # Auto-approve after 2 approves
        if approve_count >= 2:
            # Update application status
            db.update_application_status(self.app_id, 'approved', interaction.user.id)
            
            # Add to partners
            db.add_partner(
                interaction.guild_id,
                self.applicant_id,
                self.server_name,
                "Partner",
                "Partnership"
            )
            
            # Update embed
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            embed.add_field(name="Status", value="✅ APPROVED", inline=False)
            embed.add_field(name="Reviewed by", value=interaction.user.mention, inline=True)
            
            # Disable all buttons
            for item in self.children:
                item.disabled = True
            
            await interaction.response.edit_message(embed=embed, view=self)
            
            # Try to notify applicant
            try:
                applicant = await interaction.guild.fetch_member(self.applicant_id)
                if applicant:
                    approve_embed = discord.Embed(
                        title="🎉 Partnership Application Approved!",
                        description=f"Your application for **{self.server_name}** has been approved!",
                        color=discord.Color.green()
                    )
                    await applicant.send(embed=approve_embed)
            except:
                pass
            
            # Add partner role if exists
            partner_role = db.get_setting(interaction.guild_id, 'partner_role')
            if partner_role:
                try:
                    member = await interaction.guild.fetch_member(self.applicant_id)
                    role = interaction.guild.get_role(partner_role)
                    if member and role:
                        await member.add_roles(role)
                except:
                    pass
            
            # Log approval
            log_channel_id = db.get_setting(interaction.guild_id, 'log_channel')
            if log_channel_id:
                log_channel = interaction.guild.get_channel(log_channel_id)
                if log_channel:
                    await log_channel.send(f"✅ Application #{self.app_id} was approved by {interaction.user.mention}")
        else:
            await interaction.response.send_message(f"✅ Vote recorded! ({approve_count + 1}/2 approvals needed)", ephemeral=True)
    
    @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="❌")
    async def deny_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check permission (mod, admin, or owner)
        if not has_mod_permission(interaction):
            await interaction.response.send_message("❌ You need Mod, Admin, or Owner role to review applications!", ephemeral=True)
            return
        
        # Add review
        db.add_review(self.app_id, interaction.user.id, 'deny')
        
        # Get review counts
        approve_count = db.get_review_count(self.app_id, 'approve')
        deny_count = db.get_review_count(self.app_id, 'deny')
        
        # Auto-deny after 2 denies
        if deny_count >= 2:
            # Update application status
            db.update_application_status(self.app_id, 'denied', interaction.user.id)
            
            # Update embed
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.red()
            embed.add_field(name="Status", value="❌ DENIED", inline=False)
            embed.add_field(name="Reviewed by", value=interaction.user.mention, inline=True)
            
            # Disable all buttons
            for item in self.children:
                item.disabled = True
            
            await interaction.response.edit_message(embed=embed, view=self)
            
            # Try to notify applicant
            try:
                applicant = await interaction.guild.fetch_member(self.applicant_id)
                if applicant:
                    deny_embed = discord.Embed(
                        title="Partnership Application Denied",
                        description=f"Your application for **{self.server_name}** has been denied.",
                        color=discord.Color.red()
                    )
                    await applicant.send(embed=deny_embed)
            except:
                pass
            
            # Log denial
            log_channel_id = db.get_setting(interaction.guild_id, 'log_channel')
            if log_channel_id:
                log_channel = interaction.guild.get_channel(log_channel_id)
                if log_channel:
                    await log_channel.send(f"❌ Application #{self.app_id} was denied by {interaction.user.mention}")
        else:
            await interaction.response.send_message(f"❌ Vote recorded! ({deny_count + 1}/2 denials needed)", ephemeral=True)

# Bot commands
@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ {bot.user} is online!")
    await bot.change_presence(activity=discord.Game(name="🤝 Partnership Manager"))
    
    # Restore partnership messages
    for guild in bot.guilds:
        partnership_msg = db.get_partnership_message(guild.id)
        if partnership_msg:
            channel_id, message_id = partnership_msg
            channel = guild.get_channel(channel_id)
            if channel:
                try:
                    message = await channel.fetch_message(message_id)
                    if message:
                        view = PartnershipView()
                        await message.edit(view=view)
                except:
                    pass

@bot.tree.command(name="setup", description="Setup partnership system")
@app_commands.default_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤝 Partnership System Setup",
        description="Use the buttons below to configure your partnership system",
        color=discord.Color.blue()
    )
    
    view = SetupView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class SetupView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
    
    @discord.ui.button(label="Set Application Channel", style=discord.ButtonStyle.primary, emoji="📝")
    async def set_app_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please type the channel name or ID where applications should be sent:", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            channel_name = msg.content.strip()
            
            channel = None
            if channel_name.isdigit():
                channel = interaction.guild.get_channel(int(channel_name))
            else:
                channel = discord.utils.get(interaction.guild.text_channels, name=channel_name.strip('#'))
            
            if channel:
                db.set_setting(interaction.guild_id, application_channel=channel.id)
                await interaction.followup.send(f"✅ Application channel set to {channel.mention}", ephemeral=True)
                
                # Send partnership message
                view = PartnershipView()
                partnership_embed = discord.Embed(
                    title="🤝 Partnership Applications",
                    description="Click the button below to apply for partnership with our server!",
                    color=discord.Color.blue()
                )
                partnership_embed.add_field(
                    name="How to Apply",
                    value="1. Click the **Apply Now** button below\n2. Fill out the application form\n3. Wait for staff review\n4. Get notified of the decision",
                    inline=False
                )
                
                msg = await channel.send(embed=partnership_embed, view=view)
                db.save_partnership_message(interaction.guild_id, channel.id, msg.id)
            else:
                await interaction.followup.send("❌ Channel not found!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Setup timed out!", ephemeral=True)
    
    @discord.ui.button(label="Set Review Channel", style=discord.ButtonStyle.primary, emoji="👀")
    async def set_review_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please type the channel name or ID for staff reviews:", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            channel_name = msg.content.strip()
            
            channel = None
            if channel_name.isdigit():
                channel = interaction.guild.get_channel(int(channel_name))
            else:
                channel = discord.utils.get(interaction.guild.text_channels, name=channel_name.strip('#'))
            
            if channel:
                db.set_setting(interaction.guild_id, review_channel=channel.id)
                await interaction.followup.send(f"✅ Review channel set to {channel.mention}", ephemeral=True)
            else:
                await interaction.followup.send("❌ Channel not found!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Setup timed out!", ephemeral=True)
    
    @discord.ui.button(label="Set Partner Role", style=discord.ButtonStyle.primary, emoji="🎭")
    async def set_partner_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please mention the role for partners:", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            role_name = msg.content.strip()
            
            role = None
            if role_name.isdigit():
                role = interaction.guild.get_role(int(role_name))
            else:
                role = discord.utils.get(interaction.guild.roles, name=role_name.strip('<@&>'))
            
            if role:
                db.set_setting(interaction.guild_id, partner_role=role.id)
                await interaction.followup.send(f"✅ Partner role set to {role.mention}", ephemeral=True)
            else:
                await interaction.followup.send("❌ Role not found!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Setup timed out!", ephemeral=True)
    
    @discord.ui.button(label="Set Log Channel", style=discord.ButtonStyle.primary, emoji="📊")
    async def set_log_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please type the channel name or ID for logs:", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            channel_name = msg.content.strip()
            
            channel = None
            if channel_name.isdigit():
                channel = interaction.guild.get_channel(int(channel_name))
            else:
                channel = discord.utils.get(interaction.guild.text_channels, name=channel_name.strip('#'))
            
            if channel:
                db.set_setting(interaction.guild_id, log_channel=channel.id)
                await interaction.followup.send(f"✅ Log channel set to {channel.mention}", ephemeral=True)
            else:
                await interaction.followup.send("❌ Channel not found!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Setup timed out!", ephemeral=True)
    
    @discord.ui.button(label="Set Mod Role", style=discord.ButtonStyle.secondary, emoji="🛡️")
    async def set_mod_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please mention the Mod role:", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            role_name = msg.content.strip()
            
            role = None
            if role_name.isdigit():
                role = interaction.guild.get_role(int(role_name))
            else:
                role = discord.utils.get(interaction.guild.roles, name=role_name.strip('<@&>'))
            
            if role:
                db.set_setting(interaction.guild_id, mod_role_id=role.id)
                await interaction.followup.send(f"✅ Mod role set to {role.mention}", ephemeral=True)
            else:
                await interaction.followup.send("❌ Role not found!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Setup timed out!", ephemeral=True)
    
    @discord.ui.button(label="Set Admin Role", style=discord.ButtonStyle.secondary, emoji="👑")
    async def set_admin_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please mention the Admin role:", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            role_name = msg.content.strip()
            
            role = None
            if role_name.isdigit():
                role = interaction.guild.get_role(int(role_name))
            else:
                role = discord.utils.get(interaction.guild.roles, name=role_name.strip('<@&>'))
            
            if role:
                db.set_setting(interaction.guild_id, admin_role_id=role.id)
                await interaction.followup.send(f"✅ Admin role set to {role.mention}", ephemeral=True)
            else:
                await interaction.followup.send("❌ Role not found!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Setup timed out!", ephemeral=True)
    
    @discord.ui.button(label="Set Owner Role", style=discord.ButtonStyle.secondary, emoji="⭐")
    async def set_owner_role(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please mention the Owner role:", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            role_name = msg.content.strip()
            
            role = None
            if role_name.isdigit():
                role = interaction.guild.get_role(int(role_name))
            else:
                role = discord.utils.get(interaction.guild.roles, name=role_name.strip('<@&>'))
            
            if role:
                db.set_setting(interaction.guild_id, owner_role_id=role.id)
                await interaction.followup.send(f"✅ Owner role set to {role.mention}", ephemeral=True)
            else:
                await interaction.followup.send("❌ Role not found!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("❌ Setup timed out!", ephemeral=True)

class PartnershipView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    
    @discord.ui.button(label="Apply for Partnership", style=discord.ButtonStyle.success, emoji="🤝", custom_id="apply_partnership")
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PartnershipModal()
        await interaction.response.send_modal(modal)

@bot.tree.command(name="partners", description="View all partnered servers")
async def list_partners(interaction: discord.Interaction):
    partners = db.get_partners(interaction.guild_id)
    
    if not partners:
        await interaction.response.send_message("❌ No partners yet! Be the first to apply!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"🤝 Partners ({len(partners)})",
        description="Our trusted partner servers",
        color=discord.Color.gold(),
        timestamp=datetime.now()
    )
    
    for partner in partners[:10]:
        embed.add_field(
            name=partner[3],
            value=f"Type: {partner[4]}\nJoined: {partner[6][:10]}\n[Invite]({partner[5]})",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="applications", description="View pending applications (Staff only)")
async def view_applications(interaction: discord.Interaction):
    # Check permissions using role system
    if not has_mod_permission(interaction):
        await interaction.response.send_message("❌ You need Mod, Admin, or Owner role to view applications!", ephemeral=True)
        return
    
    apps = db.get_pending_apps(interaction.guild_id)
    
    if not apps:
        await interaction.response.send_message("✅ No pending applications!", ephemeral=True)
        return
    
    embed = discord.Embed(
        title=f"📋 Pending Applications ({len(apps)})",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    
    for app in apps[:5]:
        embed.add_field(
            name=f"Application #{app[0]} - {app[3]}",
            value=f"Type: {app[5]}\nSubmitted: {app[8][:10]}\nApplicant: <@{app[2]}>",
            inline=False
        )
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="approve", description="Approve a partnership application")
async def approve_app(interaction: discord.Interaction, application_id: int):
    # Check permissions using role system
    if not has_mod_permission(interaction):
        await interaction.response.send_message("❌ You need Mod, Admin, or Owner role to approve applications!", ephemeral=True)
        return
    
    apps = db.get_pending_apps(interaction.guild_id)
    app = None
    for a in apps:
        if a[0] == application_id:
            app = a
            break
    
    if not app:
        await interaction.response.send_message(f"❌ Application #{application_id} not found or already reviewed!", ephemeral=True)
        return
    
    db.update_application_status(application_id, 'approved', interaction.user.id)
    db.add_partner(interaction.guild_id, app[2], app[3], app[5], app[4])
    
    # Add partner role
    partner_role_id = db.get_setting(interaction.guild_id, 'partner_role')
    if partner_role_id:
        try:
            member = await interaction.guild.fetch_member(app[2])
            role = interaction.guild.get_role(partner_role_id)
            if member and role:
                await member.add_roles(role)
        except:
            pass
    
    await interaction.response.send_message(f"✅ Application #{application_id} approved!", ephemeral=True)
    
    # Notify applicant
    try:
        applicant = await interaction.guild.fetch_member(app[2])
        if applicant:
            embed = discord.Embed(
                title="🎉 Partnership Approved!",
                description=f"Your partnership with **{interaction.guild.name}** has been approved!",
                color=discord.Color.green()
            )
            await applicant.send(embed=embed)
    except:
        pass

@bot.tree.command(name="deny", description="Deny a partnership application")
async def deny_app(interaction: discord.Interaction, application_id: int, reason: str = None):
    # Check permissions using role system
    if not has_mod_permission(interaction):
        await interaction.response.send_message("❌ You need Mod, Admin, or Owner role to deny applications!", ephemeral=True)
        return
    
    apps = db.get_pending_apps(interaction.guild_id)
    app = None
    for a in apps:
        if a[0] == application_id:
            app = a
            break
    
    if not app:
        await interaction.response.send_message(f"❌ Application #{application_id} not found or already reviewed!", ephemeral=True)
        return
    
    db.update_application_status(application_id, 'denied', interaction.user.id)
    
    await interaction.response.send_message(f"✅ Application #{application_id} denied!", ephemeral=True)
    
    # Notify applicant
    try:
        applicant = await interaction.guild.fetch_member(app[2])
        if applicant:
            embed = discord.Embed(
                title="Partnership Application Denied",
                description=f"Your partnership application for **{interaction.guild.name}** has been denied.",
                color=discord.Color.red()
            )
            if reason:
                embed.add_field(name="Reason", value=reason, inline=False)
            await applicant.send(embed=embed)
    except:
        pass

@bot.tree.command(name="removepartner", description="Remove a partner")
async def remove_partner(interaction: discord.Interaction, server_name: str):
    # Check for admin or owner permission
    if not has_admin_permission(interaction):
        await interaction.response.send_message("❌ You need Admin or Owner role to remove partners!", ephemeral=True)
        return
    
    db.remove_partner(interaction.guild_id, server_name)
    await interaction.response.send_message(f"✅ Removed {server_name} from partners!", ephemeral=True)

@bot.tree.command(name="partnerstats", description="View partnership statistics")
async def partner_stats(interaction: discord.Interaction):
    partners = db.get_partners(interaction.guild_id)
    pending = len(db.get_pending_apps(interaction.guild_id))
    
    embed = discord.Embed(
        title="📊 Partnership Statistics",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    embed.add_field(name="Active Partners", value=str(len(partners)), inline=True)
    embed.add_field(name="Pending Applications", value=str(pending), inline=True)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="refresh", description="Refresh partnership application button")
async def refresh_button(interaction: discord.Interaction):
    # Check for admin or owner permission
    if not has_admin_permission(interaction):
        await interaction.response.send_message("❌ You need Admin or Owner role to refresh the button!", ephemeral=True)
        return
    
    partnership_msg = db.get_partnership_message(interaction.guild_id)
    if not partnership_msg:
        await interaction.response.send_message("❌ Partnership system not set up! Use /setup first.", ephemeral=True)
        return
    
    channel_id, message_id = partnership_msg
    channel = interaction.guild.get_channel(channel_id)
    
    if channel:
        try:
            old_msg = await channel.fetch_message(message_id)
            await old_msg.delete()
        except:
            pass
        
        view = PartnershipView()
        embed = discord.Embed(
            title="🤝 Partnership Applications",
            description="Click the button below to apply for partnership with our server!",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="How to Apply",
            value="1. Click the **Apply Now** button below\n2. Fill out the application form\n3. Wait for staff review\n4. Get notified of the decision",
            inline=False
        )
        
        new_msg = await channel.send(embed=embed, view=view)
        db.save_partnership_message(interaction.guild_id, channel_id, new_msg.id)
        
        await interaction.response.send_message("✅ Partnership button refreshed!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Channel not found!", ephemeral=True)

@bot.tree.command(name="help", description="Show all partnership commands")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤝 Partnership Bot Commands",
        description="Complete partnership management system",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="📝 Application Commands (Everyone)",
        value="`/apply` - Start a partnership application\n`/partners` - View all partners\n`/partnerstats` - View statistics",
        inline=False
    )
    
    embed.add_field(
        name="🔧 Staff Commands (Mod+ Roles)",
        value="`/applications` - View pending apps\n`/approve <id>` - Approve application\n`/deny <id> [reason]` - Deny application",
        inline=False
    )
    
    embed.add_field(
        name="⚙️ Admin Commands (Admin+ Roles)",
        value="`/removepartner <name>` - Remove partner\n`/refresh` - Refresh application button\n`/setup` - Configure system",
        inline=False
    )
    
    embed.add_field(
        name="👑 Role Hierarchy",
        value="**Owner Role** > **Admin Role** > **Mod Role** > **Partner Role**\nSetup `/setup` to configure these roles!",
        inline=False
    )
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="checkperms", description="Check your permission level")
async def check_perms(interaction: discord.Interaction):
    """Check what permissions you have"""
    is_mod = has_mod_permission(interaction)
    is_admin = has_admin_permission(interaction)
    is_owner = has_owner_permission(interaction)
    
    embed = discord.Embed(
        title="🔑 Your Permissions",
        color=discord.Color.blue()
    )
    
    embed.add_field(name="Mod Permissions", value="✅ Yes" if is_mod else "❌ No", inline=True)
    embed.add_field(name="Admin Permissions", value="✅ Yes" if is_admin else "❌ No", inline=True)
    embed.add_field(name="Owner Permissions", value="✅ Yes" if is_owner else "❌ No", inline=True)
    
    # Show configured roles
    mod_role_id = db.get_setting(interaction.guild_id, 'mod_role_id')
    admin_role_id = db.get_setting(interaction.guild_id, 'admin_role_id')
    owner_role_id = db.get_setting(interaction.guild_id, 'owner_role_id')
    
    if mod_role_id or admin_role_id or owner_role_id:
        embed.add_field(name="\u200b", value="**Configured Roles:**", inline=False)
        if mod_role_id:
            mod_role = interaction.guild.get_role(mod_role_id)
            if mod_role:
                embed.add_field(name="Mod Role", value=mod_role.mention, inline=True)
        if admin_role_id:
            admin_role = interaction.guild.get_role(admin_role_id)
            if admin_role:
                embed.add_field(name="Admin Role", value=admin_role.mention, inline=True)
        if owner_role_id:
            owner_role = interaction.guild.get_role(owner_role_id)
            if owner_role:
                embed.add_field(name="Owner Role", value=owner_role.mention, inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def on_member_join(member):
    # Auto-assign partner role if they're a partner
    partners = db.get_partners(member.guild.id)
    for partner in partners:
        if partner[2] == member.id:  # partner_guild_id is actually user ID in this case
            partner_role_id = db.get_setting(member.guild.id, 'partner_role')
            if partner_role_id:
                role = member.guild.get_role(partner_role_id)
                if role:
                    await member.add_roles(role)
            break

# Run the bot
if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()
    
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("❌ Please set DISCORD_BOT_TOKEN in .env file")
        print("Create a .env file with: DISCORD_BOT_TOKEN=your_token_here")
    else:
        bot.run(TOKEN)
