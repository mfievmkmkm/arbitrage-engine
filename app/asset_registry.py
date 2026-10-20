"""Expiring exact-asset evidence shared by discovery and PAPER execution."""
import json
import math
import aiosqlite

SCHEMA='''CREATE TABLE IF NOT EXISTS asset_identity_evidence(
 venue TEXT NOT NULL,symbol TEXT NOT NULL,chain_id TEXT NOT NULL,contract TEXT NOT NULL,
 verified_at REAL NOT NULL,expires_at REAL NOT NULL,payload TEXT NOT NULL,
 PRIMARY KEY(venue,symbol,chain_id,contract));'''


class Registry:
    def __init__(self,path,clock):
        self.path,self.clock=str(path),clock
        self.saved={}

    async def record(self,route,evidence,market):
        if not 0<=self.clock()-evidence['verified_at']<=300 or not self.clock()<evidence['expires_at']<=evidence['verified_at']+300:
            raise ValueError('ASSET_IDENTITY_EVIDENCE_EXPIRED')
        if market.get('base')!=route['base']:
            raise ValueError('ASSET_DENOMINATION_MAPPING_REQUIRED')
        if market.get('contract'):
            size=market.get('contractSize')
            if isinstance(size,bool) or not isinstance(size,(int,float)) or not math.isfinite(size) or size<=0:
                raise ValueError('ASSET_CONTRACT_SIZE_UNKNOWN')
        data=dict(evidence,venue=route['cex_venue'],symbol=route['cex_symbol'],
                  contract_size=market.get('contractSize'),base_multiplier=1,
                  denomination_evidence='EXACT_BASE_AND_EXCHANGE_NETWORK_CONTRACT')
        chain=str(route['chain_id']);contract=route['asset'].lower() if chain!='solana' else route['asset']
        key=(route['cex_venue'],route['cex_symbol'],chain,contract,evidence['verified_at'],evidence['expires_at'])
        if self.saved.get(key)==data:return dict(data)
        async with aiosqlite.connect(self.path) as db:
            await db.execute(SCHEMA)
            conflicts=await (await db.execute('SELECT contract FROM asset_identity_evidence WHERE venue=? AND symbol=? AND chain_id=? AND expires_at>? AND contract<>?',
                (route['cex_venue'],route['cex_symbol'],chain,self.clock(),contract))).fetchall()
            if conflicts:raise ValueError('ASSET_REGISTRY_CONTRACT_CONFLICT')
            await db.execute('INSERT INTO asset_identity_evidence VALUES(?,?,?,?,?,?,?) ON CONFLICT(venue,symbol,chain_id,contract) DO UPDATE SET verified_at=excluded.verified_at,expires_at=excluded.expires_at,payload=excluded.payload',
                (route['cex_venue'],route['cex_symbol'],chain,contract,evidence['verified_at'],evidence['expires_at'],json.dumps(data,allow_nan=False)))
            await db.commit()
        if len(self.saved)>=1024:self.saved.clear()
        self.saved[key]=dict(data)
        return data
