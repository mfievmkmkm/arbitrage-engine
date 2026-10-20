"""Lossless encoding for large audit payloads; never changes trading numbers."""
import base64
import hashlib
import json
import zlib

TAG='arbitrage.zlib-json.v1'
TABLES={'inventory_paper_events','arb_candidates','arb_checks','arb_lifecycle','arb_diagnostic_reports'}


def unpack(raw):
    if not isinstance(raw,str) or not raw.startswith('{"_codec":'):return raw
    obj=json.loads(raw)
    if obj.get('_codec')!=TAG:return raw
    data=zlib.decompress(base64.b64decode(obj['data'],validate=True))
    if hashlib.sha256(data).hexdigest()!=obj['sha256']:raise ValueError('AUDIT_PAYLOAD_CHECKSUM_MISMATCH')
    return data.decode('utf-8')


def pack(raw):
    if not isinstance(raw,str) or len(raw)<512:return raw
    if unpack(raw)!=raw:return raw
    data=raw.encode('utf-8')
    encoded=json.dumps(dict(_codec=TAG,sha256=hashlib.sha256(data).hexdigest(),
                            data=base64.b64encode(zlib.compress(data,6)).decode()),separators=(',',':'))
    return encoded if len(encoded)<len(raw)*.85 else raw


def loads(raw):return json.loads(unpack(raw))
