#!/usr/bin/python
import random
import re
import asyncio
import json
import os
import sys
import time
import urllib.request
import urllib.error
from meshcore import TCPConnection
from meshcore import MeshCore
from meshcore import EventType
import discord

#set DEBUG_MESH=True to skip posting to discord
DEBUG_MESH=False

MESHCORE_HOSTNAME = os.getenv("MESHCORE_HOSTNAME")
PORT = 5000
WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL")
MSGBOT_TOKEN = os.getenv("MSGBOT_TOKEN")
DISCORD_CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID"))

# these are just placeholders .. will be filled in by get_channels()
CHNL_IDX_PUB = 0 # Public
CHNL_IDX_TEST = 1 #test
CHNL_IDX_BOT = 2 #bot


# in any channel, prefacing a message with BOT_MESH_USER sends a command to the bot
if DEBUG_MESH:
    BOT_MESH_USER = '@[msgbot' # all lower case
    CHNL_NAME_BOT = '#crispr' # channel name for direct bot commands
else:
    BOT_MESH_USER = '@[msg bot' # all lower case
    CHNL_NAME_BOT = '#bot' # channel name for direct bot commands
    BOT_TEST_CHNL = '#crispr' # don't send messages from this chnl to discord


#globals
con = None
mc = None
channels = []


def _post_discord_webhook(url: str, content: str) -> None:
    payload = {"content": content}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        # Some environments see Cloudflare 403 without an explicit UA
        "User-Agent": f"meshbot/1.0 (+https://example) Python/{sys.version_info[0]}.{sys.version_info[1]}"
    }
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        # Read to complete the request; response body is ignored
        _ = resp.read()


async def send_to_discord(webhook_url: str, content: str) -> None:
    if DEBUG_MESH:
        return
    
    try:
        await asyncio.to_thread(_post_discord_webhook, webhook_url, content)
    except urllib.error.HTTPError as he:
        print(f"Discord webhook HTTP {he.code}: {he.reason}")
    except Exception as e:
        # Non-fatal: log and continue
        print(f"Discord webhook error: {e}")

async def help(message):
    await message.channel.send('$pub <msg>: send a msg in Public')
    await message.channel.send('$test <msg>: send a msg in #test')

async def get_channels():
    global channels
    global CHNL_IDX_PUB
    global CHNL_IDX_TEST
    global CHNL_IDX_BOT
    
    channel_idx = 0
    while True:
        res = await mc.commands.get_channel(channel_idx)
        if res.type == EventType.ERROR:
            break
        name = res.payload.get('channel_name')
        idx = res.payload.get('channel_idx')
        if name == 'Public':
            CHNL_IDX_PUB = idx
        elif name == '#test':
            CHNL_IDX_TEST = idx
        elif name == CHNL_NAME_BOT:
            CHNL_IDX_BOT = idx
        if res.payload.get('channel_name') != '':
            channels.append(res.payload)
        channel_idx += 1

magic8_responses = ["It is certain.","It is decidedly so.","Without a doubt.","Yes definitely.","You may rely on it.","As I see it, yes.","Most likely.","Outlook good.","Yes.","Signs point to yes.","Reply hazy, try again.","Ask again later.","Better not tell you now.","Cannot predict now.","Concentrate and ask again.","Don't count on it.","My reply is no.","My sources say no.","Outlook not so good.","Very doubtful."]
def magic8():
    answer=magic8_responses[random.randint(0,len(magic8_responses)-1)]
    return answer

# do commands incoming from mesh    
async def do_mesh_commands(payload,channel_idx,channel_name,user,msg):
    doit = False
    if channel_idx == CHNL_IDX_BOT:
        doit = True
    elif msg.lower().startswith(BOT_MESH_USER):
        sidx = msg.find(']')
        if sidx > 0:
            msg = msg[sidx+1:]
            print(msg)
            doit = True

    if doit:
        resp = None
        msg = msg.lstrip()
        cmd = msg.lower()

        if cmd.startswith('test'):
            timestamp = payload.get('sender_timestamp')
#            snr = payload.get('SNR')
            hops = payload.get('path_len')
            text = payload.get('text')
            elapsed = round((time.time()-timestamp)*1000)
#            resp = f"ack [{user}]{msg}|SNR:{snr}|hops:{hops}|{elapsed}ms"
            resp = f"ack [{user}]{msg}|SNR:{snr}|hops:{hops}|{elapsed}ms"
            print(resp)
        elif cmd.startswith('magic8'):
            msg = magic8()
            resp = f"[{user}]{msg}"

        if resp != None:
            #send to mesh
            res = await mc.commands.send_chan_msg(channel_idx,resp)
            print(res) # needs this or send flaky
            #send to discord
            if WEBHOOK_URL and channel_name != BOT_TEST_CHNL:
                webhook_message = f"[{channel_name}] {resp}"
                asyncio.create_task(send_to_discord(WEBHOOK_URL, webhook_message))        


async def mesh_listener () :
#    await meshconnect(con,mc)
    print("start")
    global con
    global mc
    con  = TCPConnection(MESHCORE_HOSTNAME, PORT)
    await con.connect()
    mc = MeshCore(con)
    await mc.connect()
    await get_channels()
    
    while True:
        result = await mc.commands.get_msg()
        if result.type == EventType.NO_MORE_MSGS:
            # No messages currently; wait briefly and continue listening
            await asyncio.sleep(0.5)
            continue
        elif result.type == EventType.ERROR:
            print(f"Error retrieving messages: {result.payload}")
            #            await meshconnect(mc)
            con  = TCPConnection(MESHCORE_HOSTNAME, PORT)
            await con.connect()
            mc = MeshCore(con)
            await mc.connect()
            continue
        # Extract and print channel name and text if available; otherwise fallback to raw result
        payload = getattr(result, 'payload', {}) or {}
#        print(payload)
        channel_idx = payload.get('channel_idx')
        text = payload.get('text')
        if channel_idx is not None and text is not None:

            for chnl in channels:
                if chnl.get('channel_idx') == channel_idx:
                    channel_name = chnl.get('channel_name')
                    break

            user = None
            msg = text
            if isinstance(text, str):
                parts = text.split(":", 1)
                if len(parts) == 2:
                    user = parts[0].strip()
                    msg = parts[1].lstrip()

            # Style channel label per channel
            channel_display = channel_name
            if channel_name == "Public":
                channel_display = "\x1b[37;44mPublic\x1b[0m"  # white on blue
            elif channel_name == "#test":
                channel_display = "\x1b[37;41m#test\x1b[0m"    # white on red

            if user:
                ansi_user = f"\x1b[37;44m{user}\x1b[0m"
                console_message = f"{channel_display} {ansi_user} {msg}"
                webhook_message = f"[{channel_name}] {user}: {msg}"
            else:
                console_message = f"{channel_display} {text}"
                webhook_message = f"[{channel_name}] {text}"

            print(console_message)

            #post public and #test messages to DISCORD_CHANNEL_ID
            if WEBHOOK_URL and channel_idx == CHNL_IDX_PUB or channel_idx == CHNL_IDX_TEST or channel_idx == CHNL_IDX_BOT:
                # Fire-and-forget to avoid blocking the receive loop
                await send_to_discord(WEBHOOK_URL, webhook_message)
                
            await do_mesh_commands(payload,channel_idx,channel_name,user,msg)


        else:
            print(result)


            
#testing
if DEBUG_MESH:
    asyncio.run(mesh_listener())
    sys.exit()


# Enable the necessary intents
intents = discord.Intents.default()
intents.message_content = True

# Create a client (bot) instance
client = discord.Client(intents=intents)

# Event triggered when the bot is ready
@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    asyncio.create_task(mesh_listener())

# Event triggered when a message is sent in any channel
@client.event
async def on_message(message):
    # Ignore messages sent by the bot itself to prevent infinite loops
#    if message.author == client.user:
#        return

    # Check if the message is from a specific channel (by ID)
    if message.channel.id == DISCORD_CHANNEL_ID:
#        print(f"received {message.author}: {message.content}")

        if message.content.startswith('$pub'):
            res = await mc.commands.send_chan_msg(CHNL_IDX_PUB,f"[{message.author}]{message.content[4:].lstrip()}")
            print(res) # needs this or send flaky
        elif message.content.startswith('$test'):
            res = await mc.commands.send_chan_msg(CHNL_IDX_PUB,f"[{message.author}]{message.content[5:].lstrip()}")
            print(res) # needs this or send flaky
        elif message.content == "help":
            asyncio.create_task(help())

# Run the bot with your token
# It's recommended to load your token from an environment variable for security
client.run(MSGBOT_TOKEN)
