# !/usr/bin/env python3
# -*- coding: utf-8 -*-
from inspect import stack
from json import loads, dumps
from json.decoder import JSONDecodeError
from aiohttp import ClientSession, WSMsgType, WSServerHandshakeError
from typing import Any, Callable
from asyncio import get_event_loop, all_tasks, sleep
from time import sleep as t_sleep
from re import split as re_split
from ._utils import objectize, treat_msg, exception_handler, exception_processor
from concurrent.futures import ThreadPoolExecutor


def __getattr__(identifier: str) -> object:
    if re_split(r'[/\\]', stack()[1].filename)[-1] not in ('qg_bot.py', '<frozen importlib._bootstrap>'):
        raise AssertionError("此为SDK内部使用文件，无法使用，注册机器人请使用from qg_bot.py import BOT")

    return globals()[identifier.__path__]


class BotWs:
    def __init__(self, session, ssl, logger, total_shard: int, shard_no: int, url: str, bot_id: str, bot_token: str,
                 bot_url: str, on_msg_function: Callable[[Any], Any], on_dm_function: Callable[[Any], Any],
                 on_delete_function: Callable[[Any], Any], is_filter_self: bool,
                 on_guild_event_function: Callable[[Any], Any], on_channel_event_function: Callable[[Any], Any],
                 on_guild_member_function: Callable[[Any], Any], on_reaction_function: Callable[[Any], Any],
                 on_interaction_function: Callable[[Any], Any], on_audit_function: Callable[[Any], Any],
                 on_forum_function: Callable[[Any], Any], on_audio_function: Callable[[Any], Any],
                 intents: int, msg_treat: bool, dm_treat: bool, on_start_function: Callable[[], Any], is_async: bool,
                 max_workers: int):
        """
        此为SDK内部使用类，注册机器人请使用from qg_botsdk.qg_bot import BOT

        .. seealso::
            更多教程和相关资讯可参阅：
            https://qg-botsdk.readthedocs.io/zh_CN/latest/快速入门.html
        """
        if re_split(r'[/\\]', stack()[1].filename)[-1] not in ('qg_bot.py', '<frozen importlib._bootstrap>'):
            raise AssertionError("此为SDK内部使用类，无法使用，注册机器人请使用from qg_botsdk.qg_bot import BOT")
        self.session = session
        self.__ssl = ssl
        self.logger = logger
        self.total_shard = total_shard
        self.shard_no = shard_no
        self.url = url
        self.bot_id = bot_id
        self.bot_token = bot_token
        self.bot_url = bot_url
        self.on_start_function = on_start_function
        self.on_msg_function = on_msg_function
        self.on_dm_function = on_dm_function
        self.on_delete_function = on_delete_function
        self.is_filter_self = is_filter_self
        self.on_forum_function = on_forum_function
        if not intents:
            self.logger.error('[错误] 未订阅任何事件（未订阅任何事件将导致op=9的报错，请最少订阅一个事件）')
            exit()
        self.intents = intents
        self.msg_treat = msg_treat
        self.dm_treat = dm_treat
        self.robot = None
        self.heartbeat_time = 0
        self.loop = get_event_loop()
        self.s = None
        self.reconnect_times = 0
        self.re_connect = False
        self.running = True
        self.session_id = 0
        self.flag = False
        self.heartbeat = None
        self.op9_flag = False
        self.is_async = is_async
        self.events = {"GUILD_CREATE": on_guild_event_function, "GUILD_UPDATE": on_guild_event_function,
                       "GUILD_DELETE": on_guild_event_function, "CHANNEL_CREATE": on_channel_event_function,
                       "CHANNEL_UPDATE": on_channel_event_function, "CHANNEL_DELETE": on_channel_event_function,
                       "GUILD_MEMBER_ADD": on_guild_member_function, "GUILD_MEMBER_UPDATE": on_guild_member_function,
                       "GUILD_MEMBER_REMOVE": on_guild_member_function, "MESSAGE_REACTION_ADD": on_reaction_function,
                       "MESSAGE_REACTION_REMOVE": on_reaction_function, "INTERACTION_CREATE": on_interaction_function,
                       "MESSAGE_AUDIT_PASS": on_audit_function, "MESSAGE_AUDIT_REJECT": on_audit_function,
                       "AUDIO_START": on_audio_function, "AUDIO_FINISH": on_audio_function,
                       "AUDIO_ON_MIC": on_audio_function, "AUDIO_OFF_MIC": on_audio_function}
        self.threads = ThreadPoolExecutor(max_workers) if not self.is_async else None

    async def send_connect(self):
        connect_paras = {
            "op": 2,
            "d": {
                "token": f"Bot {self.bot_id}.{self.bot_token}",
                "intents": self.intents,
                "shard": [self.shard_no, self.total_shard]
            }
        }
        await self.ws_send(dumps(connect_paras))

    async def send_reconnect(self):
        reconnect_paras = {
            "op": 6,
            "d": {
                "token": f"Bot {self.bot_id}.{self.bot_token}",
                "session_id": self.session_id,
                "seq": self.s
            }
        }
        await self.ws_send(dumps(reconnect_paras))

    async def ws_send(self, msg):
        if not self.ws.closed:
            await self.ws.send_str(msg)

    async def heart(self):
        heart_json = {"op": 1, "d": None}
        while True:
            await sleep(self.heartbeat_time)
            if not self.ws.closed:
                heart_json['d'] = self.s
                await self.ws.send_str(dumps(heart_json))

    def start_heartbeat(self):
        tasks = [task.get_name() for task in all_tasks()]
        if 'heartbeat_task' not in tasks:
            self.heartbeat = self.loop.create_task(self.heart())
            self.heartbeat.set_name('heartbeat_task')

    def get_robot_info(self, retry=False):
        robot_info = self.session.get(f'{self.bot_url}/users/@me').json()
        if 'id' not in robot_info:
            if not retry:
                return self.get_robot_info(retry)
            else:
                self.logger.error('获取机器人信息失败，机器人启动失败，程序将退出运行')
                exit()
        return objectize(robot_info)

    @exception_processor
    async def async_start_task(self, func, *args):
        await func(*args)

    @exception_processor
    def start_task(self, func, *args):
        func(*args)

    async def distribute(self, function, data):
        data["d"]["t"] = data["t"]
        data["d"]["event_id"] = data["id"]
        if function is not None:
            if not self.is_async:
                self.threads.submit(self.start_task(function, objectize(data["d"])))
            else:
                self.loop.create_task(self.async_start_task(function, objectize(data["d"])))

    @exception_processor
    async def data_process(self, data):
        t = data["t"]
        if t in ("AT_MESSAGE_CREATE", 'MESSAGE_CREATE'):
            if self.msg_treat:
                raw_msg = '' if 'content' not in data["d"] else data["d"]["content"].strip()
                at = f'<@!{self.robot.id}>'
                treated_msg = raw_msg if raw_msg.find(at) else raw_msg.replace(at, '', 1)
                data["d"]["treated_msg"] = treat_msg(treated_msg.strip())
            await self.distribute(self.on_msg_function, data)
        elif t in ("MESSAGE_DELETE", "PUBLIC_MESSAGE_DELETE", "DIRECT_MESSAGE_DELETE"):
            if self.is_filter_self:
                target = data['d']['message']['author']['id']
                op_user = data['d']['op_user']['id']
                if op_user == target:
                    return
            await self.distribute(self.on_delete_function, data)
        elif t == "DIRECT_MESSAGE_CREATE":
            if self.dm_treat:
                raw_msg = '' if 'content' not in data["d"] else data["d"]["content"].strip()
                data["d"]["treated_msg"] = treat_msg(raw_msg)
            await self.distribute(self.on_dm_function, data)
        elif t in ("FORUM_THREAD_CREATE", "FORUM_THREAD_UPDATE", "FORUM_THREAD_DELETE", "FORUM_POST_CREATE",
                   "FORUM_POST_DELETE", "FORUM_REPLY_CREATE", "FORUM_REPLY_DELETE", "FORUM_PUBLISH_AUDIT_RESULT"):
            for items in ("content", "title"):
                try:
                    data["d"]["thread_info"][items] = loads(data["d"]["thread_info"][items])
                except JSONDecodeError:
                    pass
            await self.distribute(self.on_forum_function, data)
        else:
            if t in self.events:
                func = self.events[t]
                if func:
                    await self.distribute(func, data)
            else:
                self.logger.warning(f'unknown event type: [{t}]')

    async def main(self, msg):
        data = loads(msg)
        op = data.get('op', None)
        if "s" in data:
            self.s = data["s"]
        if op == 11:
            self.logger.debug('心跳发送成功')
        elif op == 9:
            if not self.op9_flag:
                self.op9_flag = True
                if not self.re_connect:
                    await self.send_connect()
                else:
                    await self.send_reconnect()
                return
            else:
                self.logger.error('[错误] 参数出错（一般此报错为传递了无权限的事件订阅，请检查是否有权限订阅相关事件）')
                exit()
        elif op == 10:
            self.heartbeat_time = float(int(data["d"]["heartbeat_interval"]) * 0.001)
            if not self.re_connect:
                await self.send_connect()
            else:
                await self.send_reconnect()
        elif op == 0:
            if data["t"] == "READY":
                self.session_id = data["d"]["session_id"]
                self.reconnect_times = 0
                self.start_heartbeat()
                self.logger.info('连接成功，机器人开始运行')
                if not self.flag:
                    self.flag = True
                    self.robot = self.get_robot_info()
                    self.logger.info(f'机器人频道用户ID：{self.robot.id}')
                    if self.on_start_function is not None:
                        if self.is_async:
                            self.loop.create_task(self.on_start_function())
                        else:
                            self.on_start_function()
            elif data["t"] == "RESUMED":
                self.reconnect_times = 0
                self.start_heartbeat()
                self.logger.info('重连成功，机器人继续运行')
            else:
                await self.data_process(data)

    async def connect(self):
        self.reconnect_times += 1
        try:
            async with ClientSession() as ws_session:
                async with ws_session.ws_connect(self.url, ssl=self.__ssl) as self.ws:
                    while not self.ws.closed:
                        message = await self.ws.receive()
                        if message.type == WSMsgType.TEXT:
                            if not self.running:
                                if self.heartbeat is not None and not self.heartbeat.cancelled():
                                    self.heartbeat.cancel()
                                await self.ws.close()
                                self.logger.info('WS进程已结束')
                                return
                            await self.main(message.data)
                        elif message.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                            if self.running:
                                self.re_connect = True
                                if self.heartbeat is not None and not self.heartbeat.cancelled():
                                    self.heartbeat.cancel()
                                self.logger.warning('BOT_WS链接已断开，正在尝试重连……')
                                return
        except Exception as e:
            self.logger.warning('BOT_WS链接已断开，正在尝试重连……')
            if self.heartbeat is not None and not self.heartbeat.cancelled():
                self.heartbeat.cancel()
            self.logger.error(e)
            self.logger.debug(exception_handler(e))
            return

    def starter(self):
        self.loop.run_until_complete(self.connect())
        while self.running:
            self.re_connect = False if self.reconnect_times >= 20 else True
            try:
                self.loop.run_until_complete(self.connect())
            except WSServerHandshakeError:
                self.logger.warning('网络连线不稳定或已断开，请检查网络链接')
            t_sleep(5)
