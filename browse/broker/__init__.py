"""browse/broker — Outbound control-bus broker backends (§5, issues #71, #72).

Shipped backends (stdlib-only, no inbound port):
  telegram — HTTP long-polling bot (api.telegram.org); issue #71
  discord  — HTTP REST-polling of channel history (discord.com); issue #72
  ably     — HTTP REST-polling + publish (rest.ably.io); issue #72
  slack    — HTTP polling of conversations.history (slack.com); issue #72

Usage (from browse/__init__.py main()):
  from browse.broker.telegram import TelegramBroker
  broker = TelegramBroker(db=db, token=tg_token, authorized_user_id=uid)
  broker.run()          # blocks; Ctrl-C to stop

  from browse.broker.discord import DiscordBroker
  broker = DiscordBroker(db=db, token=tok, channel_id=cid, authorized_user_id=uid)
  broker.run()

  from browse.broker.ably import AblyBroker
  broker = AblyBroker(db=db, api_key=key, channel_in='browse-commands',
                       channel_out='browse-responses', authorized_client_id='operator')
  broker.run()

  from browse.broker.slack import SlackBroker
  broker = SlackBroker(db=db, bot_token=tok, channel_id=cid, authorized_user_id=uid)
  broker.run()

Architecture constraint (Discord/Ably/Slack):
  All three use HTTP REST polling rather than the native WebSocket/realtime APIs.
  This keeps the implementation stdlib-only (no pip deps) but adds ~2 s poll
  latency versus <100 ms for native WebSocket clients.
  Sub-second latency requires accepting a non-stdlib dependency (maintainer decision).

Remaining blocker for #72 closure:
  Live credentials + maintainer-run RTT benchmark (median + p95 over ≥100 messages
  on a tunnel-hostile / FPT network).
"""
