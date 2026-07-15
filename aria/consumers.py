"""
aria/consumers.py

WebSocket consumer for ARIA — the AI chat assistant.

Connect: ws://<host>/ws/aria/?token=<jwt>

Incoming message from client:
  { "type": "message", "text": "...", "conversation_id": "<uuid or null>" }
  { "type": "ping" }

Outgoing events to client:
  { "type": "tool_start", "tool": "<name>" }   — ARIA is calling a data tool
  { "type": "tool_end",   "tool": "<name>" }   — tool finished
  { "type": "token",      "text": "<chunk>" }  — streamed response word by word
  { "type": "done",       "conversation_id": "<uuid>", "title": "<str>" }
  { "type": "error",      "message": "<str>" }
  { "type": "pong" }
"""

import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)

_TOOL_LABELS = {
    'query_commercial':             'Checking commercial data',
    'query_top_commercial_feeders': 'Ranking feeders by billing',
    'query_technical':              'Checking technical data',
    'query_feeder_ranking':         'Ranking feeders',
    'query_band_compliance':        'Checking band compliance',
    'query_feeder_records':         'Pulling all-time feeder records',
    'query_hourly_load':            'Checking hourly load',
    'query_system_load':            'Aggregating system load',
    'query_period_comparison':      'Comparing periods',
    'query_financial':              'Checking financial data',
    'query_hr':                     'Checking HR data',
    'query_executive_kpis':         'Checking executive KPIs',
    'query_energy_account':         'Checking energy account data',
    'query_grid_lens':              'Checking GridLens data',
    'list_locations':               'Looking up locations',
    'web_search':                   'Searching the web',
}


class ARIAConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        self.user = user
        await self.accept()
        logger.debug('ARIA WS connected: user=%s', user.id)

    async def disconnect(self, close_code):
        logger.debug('ARIA WS disconnected: user=%s code=%s', getattr(self, 'user', '?'), close_code)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or '{}')
        except (ValueError, TypeError):
            await self._send_error('Invalid JSON.')
            return

        msg_type = data.get('type', 'message')

        if msg_type == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong'}))
            return

        if msg_type == 'message':
            text            = (data.get('text') or '').strip()
            conversation_id = data.get('conversation_id') or None

            if not text:
                await self._send_error('Message text is required.')
                return

            await self._run_aria(text, conversation_id)

    async def _run_aria(self, user_message: str, conversation_id):
        from aria.agent import stream_aria

        try:
            async for event in stream_aria(self.user, conversation_id, user_message):
                event_type = event.get('type')

                if event_type == 'tool_start':
                    label = _TOOL_LABELS.get(event['tool'], event['tool'])
                    await self.send(text_data=json.dumps({
                        'type':  'tool_start',
                        'tool':  event['tool'],
                        'label': label,
                    }))

                elif event_type == 'tool_end':
                    await self.send(text_data=json.dumps({
                        'type': 'tool_end',
                        'tool': event['tool'],
                    }))

                elif event_type == 'token':
                    await self.send(text_data=json.dumps({
                        'type': 'token',
                        'text': event['text'],
                    }))

                elif event_type == 'chart_data':
                    await self.send(text_data=json.dumps({
                        'type':  'chart_data',
                        'chart': event['chart'],
                    }))

                elif event_type == 'done':
                    await self.send(text_data=json.dumps({
                        'type':            'done',
                        'conversation_id': event['conversation_id'],
                        'title':           event['title'],
                    }))

                elif event_type == 'error':
                    await self._send_error(event.get('message', 'Unknown error.'))

        except Exception as e:
            logger.exception('ARIA stream error for user=%s', self.user.id)
            await self._send_error(str(e))

    async def _send_error(self, message: str):
        await self.send(text_data=json.dumps({'type': 'error', 'message': message}))
