"""Durable operator intent. Never grants exchange, wallet or LIVE authority."""
import copy
import json
import math
import os
import secrets
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

INTENT = ContextVar('operator_entry_intent', default=None)
MODES = {'observe', 'manual', 'auto'}
ENVIRONMENTS = {'paper', 'live'}
LIMITS = {'notional_usd': (1, 1000), 'daily_loss_usd': (.1, 100),
          'max_positions': (1, 5), 'min_net_usd': (.01, 100),
          'max_spread_pct': (.1, 10), 'stop_loss_usd': (.1, 20), 'max_hold_seconds': (60, 86400),
          'min_spread_pct':(0,10),'slippage_bps':(0,100),'target_net_usd':(0,100),'trail_drawdown_pct':(1,90)}
EXTENDED_DEFAULTS=dict(min_spread_pct=0,slippage_bps=20,target_net_usd=0,trail_drawdown_pct=25)


def operator_exit_limits(policy, hold_seconds, stop_net):
    if not policy:
        return hold_seconds, stop_net
    stop, hold = policy.get('stop_loss_usd'), policy.get('max_hold_seconds')
    if any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) for v in (stop,hold)) or not .1 <= stop <= 20 or not 60 <= hold <= 86400:
        raise ValueError('OPERATOR_EXIT_POLICY_INVALID')
    return min(hold_seconds,hold), max(stop_net,-stop) if stop_net is not None else -stop


class Controls:
    def __init__(self, path, paper_notional=100, live_notional=5, clock=time.time):
        self.path, self.clock = Path(path), clock
        def profile(notional, mode):
            return dict(mode=mode, notional_usd=notional, daily_loss_usd=5 if mode=='auto' else 1,
                        max_positions=1, min_net_usd=.25 if mode=='auto' else .05, max_spread_pct=10,
                        stop_loss_usd=1, max_hold_seconds=1200)
        self.state = dict(version=1, revision=0, environment='paper',
                          profiles=dict(paper=profile(paper_notional, 'auto'),
                                        live=profile(min(5, live_notional), 'observe')))
        self.pending = {}
        self.events = []
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding='utf-8'))
        for p in self.state.get('profiles',{}).values():
            for key,value in EXTENDED_DEFAULTS.items():p.setdefault(key,value)
        self.validate(self.state)

    @staticmethod
    def validate(state):
        if state.get('version') != 1 or state.get('environment') not in ENVIRONMENTS:
            raise ValueError('TRADING_CONTROLS_INVALID')
        if type(state.get('revision')) is not int or state['revision'] < 0:
            raise ValueError('TRADING_CONTROLS_REVISION_INVALID')
        if set(state.get('profiles', {})) != ENVIRONMENTS:
            raise ValueError('TRADING_CONTROLS_PROFILES_INVALID')
        for p in state['profiles'].values():
            if p.get('mode') not in MODES:
                raise ValueError('TRADING_MODE_INVALID')
            for key, (low, high) in LIMITS.items():
                value = p.get(key)
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not low <= value <= high:
                    raise ValueError('TRADING_RISK_INVALID:' + key)
            if type(p['max_positions']) is not int:
                raise ValueError('TRADING_POSITION_LIMIT_INVALID')
            if p['min_spread_pct']>p['max_spread_pct'] or p['slippage_bps']!=int(p['slippage_bps']):
                raise ValueError('TRADING_SPREAD_OR_SLIPPAGE_INVALID')

    @property
    def environment(self):
        return self.state['environment']

    def profile(self, environment=None):
        return copy.deepcopy(self.state['profiles'][environment or self.environment])

    def update(self, environment=None, **changes):
        state = copy.deepcopy(self.state)
        if environment is not None:
            if environment not in ENVIRONMENTS:
                raise ValueError('TRADING_ENVIRONMENT_INVALID')
            state['environment'] = environment
        if set(changes) - (set(LIMITS) | {'mode'}):
            raise ValueError('TRADING_SETTING_UNKNOWN')
        state['profiles'][state['environment']].update(changes)
        state['revision'] += 1
        self.validate(state)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + secrets.token_hex(6) + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(state, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self.events.append(dict(ts=self.clock(), before=copy.deepcopy(self.state), after=copy.deepcopy(state)))
        self.state = state
        self.pending.clear()

    def entry_reason(self, environment, strategy, symbol=None):
        if self.environment != environment:
            return 'OTHER_TRADING_ENVIRONMENT'
        mode = self.profile(environment)['mode']
        if mode == 'observe':
            return 'OBSERVE_ONLY'
        if mode == 'manual' and INTENT.get() != (environment, strategy, symbol, self.state['revision']):
            return 'MANUAL_CONFIRMATION_REQUIRED'
        return None

    def risk_reason(self, notional, open_count, daily_net, projected_net=None, spread_pct=None, environment=None):
        p = self.profile(environment)
        values = [notional, daily_net]
        values.extend(v for v in (projected_net, spread_pct) if v is not None)
        if any(isinstance(v, bool) or not isinstance(v, (int,float)) or not math.isfinite(v) for v in values):
            return 'OPERATOR_RISK_DATA_INVALID'
        if notional <= 0 or notional > p['notional_usd'] + 1e-8:
            return 'OPERATOR_NOTIONAL_LIMIT'
        if open_count >= p['max_positions']:
            return 'OPERATOR_POSITION_LIMIT'
        if daily_net <= -p['daily_loss_usd']:
            return 'OPERATOR_DAILY_STOP'
        if projected_net is not None and projected_net < p['min_net_usd']:
            return 'OPERATOR_NET_LOW'
        if spread_pct is not None and abs(spread_pct) > p['max_spread_pct']:
            return 'OPERATOR_SPREAD_LIMIT'
        return None

    def tighten_policy(self, policy):
        p = self.profile('paper')
        return dict(policy, stop_usd=min(policy['stop_usd'], p['stop_loss_usd']),
                    max_seconds=min(policy['max_seconds'], p['max_hold_seconds']),
                    operator_revision=self.state['revision'])

    def issue(self, strategy, row):
        if self.profile()['mode'] != 'manual':
            raise ValueError('MANUAL_MODE_REQUIRED')
        now = self.clock()
        self.pending = {k:v for k,v in self.pending.items() if 0 <= now-v['ts'] <= 30}
        if len(self.pending) >= 50:
            self.pending.pop(next(iter(self.pending)))
        token = secrets.token_hex(12)
        self.pending[token] = dict(ts=now, environment=self.environment, revision=self.state['revision'],
                                   strategy=strategy, row=copy.deepcopy(row))
        return token

    def claim(self, token):
        item = self.pending.pop(token, None)
        if (item is None or item['environment'] != self.environment or item['revision'] != self.state['revision']
                or not 0 <= self.clock()-item['ts'] <= 30 or self.profile()['mode'] != 'manual'):
            raise ValueError('MANUAL_CARD_EXPIRED')
        return item

    @contextmanager
    def manual_intent(self, item):
        token = INTENT.set((item['environment'], item['strategy'], item['row'].get('symbol'), item['revision']))
        try:
            yield
        finally:
            INTENT.reset(token)
